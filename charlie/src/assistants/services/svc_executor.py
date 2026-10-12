"""executor service: holds every Charlie-side key, accepts only validated proposals."""

from __future__ import annotations

import dataclasses
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from assistants.adapters.llm import LLMRequest
from assistants.adapters.secrets import SecretStore
from assistants.approvals.service import ApprovalRequest, AttestationVerifier
from assistants.audit import PgAuditLog
from assistants.common.clock import SystemClock
from assistants.common.config import load_config
from assistants.common.crypto import b64d, ed25519_public_from_str
from assistants.common.ctxsign import verify_ctx
from assistants.common.errors import Denied
from assistants.events import Mode, Trust
from assistants.executor.core import CallContext, Executor
from assistants.executor.handlers import ToolContext, ToolRunner
from assistants.executor.tokens import ActionTokens
from assistants.inventory.repo import PgInventory
from assistants.killswitch.core import KSState
from assistants.notes.store import PgNotes, PgProjects
from assistants.ops.costs import MeteredLLM
from assistants.policy import default_policy
from assistants.services.clients import HttpExtractor, RulesClient
from assistants.services.common import (
    URLS,
    ServiceAuth,
    ServiceClient,
    every,
    lease_view,
    pool,
)
from assistants.services.wiring import llm_backend, providers
from assistants.storage.stores import (
    CostEntry,
    PgCostLedger,
    PgProposalStore,
    PgTokenLedger,
    PgToolCallLog,
)
from assistants.storage.transcripts import PgTranscripts


def build(store: SecretStore) -> FastAPI:  # noqa: PLR0915 - one wiring function per service
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    policy = default_policy()
    clock = SystemClock()
    db = pool(store.get("db.dsn"))
    auth = ServiceAuth(store)
    audit = PgAuditLog(db)
    me = store.get("self.token")
    prov = providers(cfg, store)
    ks = lease_view(store, clock)
    mode = {"value": Mode.NORMAL}

    extractor = HttpExtractor(ServiceClient(URLS["extractor"], me))
    approvals = ServiceClient(URLS["approvals"], me)
    costs = PgCostLedger(db)

    def log_llm(e: CostEntry) -> None:
        audit.append(
            "ops",
            "llm.call",
            {"agent": e.agent, "channel": e.channel, "model": e.model, "cost_nok": e.cost_nok},
        )

    llm = MeteredLLM(
        llm_backend(cfg, store, "anthropic.charlie_api_key"),
        "charlie",
        cfg,
        costs,
        clock,
        on_call=log_llm,
    )
    inventory = PgInventory(db)
    ctx = ToolContext(
        cfg=cfg,
        clock=clock,
        calendar=prov.calendar,
        email=prov.email,
        notes=PgNotes(db),
        projects=PgProjects(db),
        inventory=inventory,
        ha=prov.ha,
        notifier=prov.notifier,
        telephony=prov.telephony,
        supplier=prov.supplier,
        transcripts=PgTranscripts(db),
        extract=lambda src, text: extractor.extract(src, text, channel="email").model_dump(),
    )
    tokens = ActionTokens(
        b64d(store.get("action.hmac_key")), clock, PgTokenLedger(db), cfg.action_tokens.ttl_s
    )

    def request_approval(req: ApprovalRequest) -> None:
        payload = dataclasses.asdict(req)
        payload["expires_at"] = req.expires_at.isoformat()
        approvals.post("/requests", payload)

    executor = Executor(
        policy=policy,
        cfg=cfg,
        clock=clock,
        rules=RulesClient(ServiceClient(URLS["rules"], me)),
        killswitch=lambda: (ks.state(), True),
        mode_view=lambda: mode["value"],
        proposals=PgProposalStore(db),
        costs=costs,
        calls=PgToolCallLog(db),
        audit=audit,
        tokens=tokens,
        runner=ToolRunner(ctx, tokens),
        inventory=inventory,
        request_approval=request_approval,
        attestations=AttestationVerifier(
            ed25519_public_from_str(store.get("approvals.public_key")), clock
        ),
    )
    ctx_key = b64d(store.get("ctx.hmac_key"))
    app = base_app("executor", ready=lambda: True)
    app.state.simulated = prov.simulated

    def heartbeat(service: str) -> None:
        with db.connection() as conn:
            conn.execute(
                "insert into core.heartbeats (service, last_seen) values (%s, now()) "
                "on conflict (service) do update set last_seen = excluded.last_seen",
                (service,),
            )

    every(30, lambda: heartbeat("executor"), "heartbeat")

    @app.post("/proposals")
    def proposals_route(
        body: dict[str, Any], caller: str = Depends(auth.require("charlie", "worker"))
    ) -> dict[str, Any]:
        if caller == "worker":
            call_ctx = CallContext(Trust.SYSTEM, mode["value"], "system")
        else:
            try:
                call_ctx = verify_ctx(str(body.get("ctx_token", "")), ctx_key, clock)
            except Denied as exc:
                raise HTTPException(status_code=403, detail=exc.reason) from exc
            if mode["value"] == Mode.OPEN_WORKSHOP:
                call_ctx = dataclasses.replace(call_ctx, mode=Mode.OPEN_WORKSHOP)
        return dataclasses.asdict(executor.submit(body.get("proposal"), call_ctx))

    @app.post("/approve")
    def approve_route(
        body: dict[str, Any], _c: str = Depends(auth.require("approvals"))
    ) -> dict[str, Any]:
        try:
            return dataclasses.asdict(executor.approve(str(body["attestation"])))
        except Denied as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc

    @app.post("/llm")
    def llm_route(
        body: dict[str, Any], caller: str = Depends(auth.require("charlie", "extractor"))
    ) -> dict[str, Any]:
        req = LLMRequest(
            **{
                k: body[k]
                for k in (
                    "agent",
                    "purpose",
                    "model",
                    "system",
                    "user_content",
                    "max_tokens",
                    "json_schema",
                    "channel",
                )
                if k in body
            }
        )
        if req.agent != "charlie":
            raise HTTPException(status_code=403, detail="executor proxies Charlie only")
        if caller == "extractor" and req.purpose != "extractor":
            raise HTTPException(status_code=403, detail="extractor may only extract")
        if not ks.charlie_may_act():
            raise HTTPException(status_code=423, detail="killswitch")
        try:
            resp = llm.complete(req)
        except Denied as exc:
            raise HTTPException(status_code=429, detail=exc.reason) from exc
        return {
            "text": resp.text,
            "usage": dataclasses.asdict(resp.usage),
            "model": resp.model,
            "stop_reason": resp.stop_reason,
        }

    @app.post("/internal/lease")
    def lease_route(
        body: dict[str, Any], _c: str = Depends(auth.require("killswitch"))
    ) -> dict[str, str]:
        before = ks.state()
        ks.accept(str(body.get("token", "")))
        if before == KSState.ACTIVE and ks.state() != KSState.ACTIVE:
            executor.freeze_all()
        return {"state": ks.state().value}

    @app.post("/internal/mode")
    def mode_route(
        body: dict[str, Any], _c: str = Depends(auth.require("gateway"))
    ) -> dict[str, str]:
        mode["value"] = Mode(body["mode"])
        return {"mode": mode["value"].value}

    @app.post("/heartbeat")
    def heartbeat_route(_c: str = Depends(auth.require("charlie"))) -> dict[str, str]:
        heartbeat("agents-charlie")
        return {"ok": "yes"}

    @app.get("/internal/audit-head")
    def audit_head(_c: str = Depends(auth.require("killswitch"))) -> dict[str, str]:
        return {"head": audit.head_hash()}

    return app
