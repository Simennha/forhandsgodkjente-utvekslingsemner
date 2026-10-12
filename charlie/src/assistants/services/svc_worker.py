"""worker service: queue processing, retention, audit export, heartbeats, anomalies, reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from assistants.adapters.objectstore import MemoryObjectStore
from assistants.adapters.secrets import SecretStore
from assistants.audit import PgAuditLog, export_batch
from assistants.common.clock import OSLO, SystemClock, oslo_month
from assistants.common.config import load_config
from assistants.lola_queue.store import PgQueueReader
from assistants.ops.worker import (
    detect_anomalies,
    extract_transcripts,
    process_lola_queue,
    purge_transcripts,
)
from assistants.services.clients import HttpExtractor
from assistants.services.common import URLS, ServiceClient, every, log, pool
from assistants.storage.stores import PgCostLedger
from assistants.storage.transcripts import PgTranscripts


def build(store: SecretStore) -> FastAPI:  # noqa: PLR0915 - one wiring function per service
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    clock = SystemClock()
    db = pool(store.get("db.dsn"))
    guest_db = pool(store.get("db.guest_dsn"))  # Lola's separate database (red team #10)
    audit = PgAuditLog(db)
    me = store.get("self.token")
    extractor = HttpExtractor(ServiceClient(URLS["extractor"], me))
    executor = ServiceClient(URLS["executor"], me)
    killswitch = ServiceClient(URLS["killswitch"], me)
    transcripts = PgTranscripts(db)
    queue = PgQueueReader(guest_db)
    objects = MemoryObjectStore()  # SIMULATED WORM store until S3 object lock is configured
    state: dict[str, Any] = {"exported": 0, "audit_seq": 0, "last_report": ""}

    def alert(message: str) -> None:
        executor.post(
            "/proposals",
            {
                "proposal": {
                    "schema": "proposal/v1",
                    "agent": "charlie",
                    "tool": "alert.notify_owner",
                    "params": {"message": message[:300], "priority": "high"},
                }
            },
        )

    def queue_job() -> None:
        process_lola_queue(queue, extractor, transcripts, clock, audit)

    def export_job() -> None:
        state["exported"] = export_batch(audit, objects, clock, state["exported"])

    def anchor_job() -> None:
        entries = audit.entries(max(0, state["audit_seq"]))
        if entries:
            last = entries[-1]
            killswitch.post("/anchor", {"seq": last.seq, "hash": last.hash})
            state["audit_seq"] = last.seq

    def anomaly_job() -> None:
        fired = detect_anomalies(
            audit,
            list(cfg.killswitch.anomaly_rules),
            clock,
            lambda rule, action: killswitch.post("/anomaly", {"rule": rule, "action": action}),
        )
        for rule in fired:
            log.warning("anomaly rule fired: %s", rule)

    def heartbeat_job() -> None:
        with db.connection() as conn:
            rows = conn.execute(
                "select service, extract(epoch from now() - last_seen) from core.heartbeats"
            ).fetchall()
        with guest_db.connection() as conn:
            rows += conn.execute(
                "select 'agents-lola', extract(epoch from now() - last_seen) "
                "from guest.lola_heartbeat"
            ).fetchall()
        seen = {str(s): float(age) for s, age in rows}
        for service in ("agents-charlie", "agents-lola", "executor"):
            if seen.get(service, 1e9) > cfg.heartbeat.stale_after_s:
                alert(f"{service} har ikke gitt livstegn på over {cfg.heartbeat.stale_after_s} s.")

    def weekly_report_job() -> None:
        now = datetime.now(OSLO)
        key = now.strftime("%G-W%V")
        if now.weekday() == 0 and now.hour >= 7 and state["last_report"] != key:
            executor.post(
                "/proposals",
                {
                    "proposal": {
                        "schema": "proposal/v1",
                        "agent": "charlie",
                        "tool": "report.generate",
                        "params": {"kind": "weekly_projects"},
                    }
                },
            )
            state["last_report"] = key

    every(30, queue_job, "lola-queue")
    every(60, lambda: extract_transcripts(transcripts, extractor, clock, audit), "extracts")
    every(3600, lambda: purge_transcripts(transcripts, clock, audit), "retention")
    every(300, export_job, "audit-export")
    every(30, anomaly_job, "anomalies")
    every(300, anchor_job, "audit-anchor")
    every(120, heartbeat_job, "heartbeats")
    every(600, weekly_report_job, "weekly-report")

    app = base_app("worker")

    def loopback_only(request: Request) -> None:
        # Only via `docker compose exec worker ...` (red team #18).
        if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
            raise HTTPException(status_code=403, detail="local access only")

    @app.get("/costs", dependencies=[Depends(loopback_only)])
    def costs() -> dict[str, Any]:
        """Cost per agent and channel this month (Lola's from her own ledger)."""
        month = oslo_month(clock)
        rows = PgCostLedger(db).breakdown(month)
        with guest_db.connection() as conn:
            lola = conn.execute(
                "select channel, coalesce(sum(cost_nok),0) from guest.lola_usage "
                "where kind = 'llm' and month = %s group by channel",
                (month,),
            ).fetchall()
        rows += [("lola", str(c), float(v)) for c, v in lola]
        return {
            "month": month,
            "rows": [{"agent": a, "channel": c, "nok": round(v, 2)} for a, c, v in rows],
            "budgets": {k: v.model_dump() for k, v in cfg.budgets.items()},
        }

    @app.get("/audit/verify", dependencies=[Depends(loopback_only)])
    def verify() -> dict[str, Any]:
        return {
            "ok": audit.verify(),
            "head": audit.head_hash(),
            "exported_to_seq": state["exported"],
        }

    return app
