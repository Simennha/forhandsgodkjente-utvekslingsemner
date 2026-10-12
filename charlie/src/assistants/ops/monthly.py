"""Monthly drill: alerts, backup restore, duress code (and kill switch).

    python -m assistants.ops.monthly --sim       # everything in-process with simulators
    python -m assistants.ops.monthly --compose   # backup restore against the running stack

Every step is written to the audit log as ``drill.*``. The drill uses the SAME code paths as
real use; only the outside world is simulated (see HANDOFF.md for what that means).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from assistants.adapters.objectstore import MemoryObjectStore
from assistants.audit import GENESIS, _hash, check_anchors, export_batch
from assistants.common.config import ROOT
from assistants.killswitch.core import KSState
from assistants.ops.worker import Heartbeats, check_heartbeats
from assistants.sim import DEMO_DURESS, DEMO_PIN, SimSystem


@dataclass
class DrillReport:
    steps: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append((name, ok, detail))
        print(f"  [{'OK' if ok else 'FEIL'}] {name} {detail}")

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.steps)


def drill_alerts(sim: SimSystem, report: DrillReport) -> None:
    hb = Heartbeats(stale_after_s=sim.cfg.heartbeat.stale_after_s)
    hb.beat("agents-lola", sim.clock.now())  # Charlie deliberately silent
    alerts: list[str] = []
    stale = check_heartbeats(hb, sim.clock, alerts.append)
    report.add("varsel når Charlie stilner", stale == ["agents-charlie"] and bool(alerts))
    r = sim.executor.submit(
        {
            "schema": "proposal/v1",
            "agent": "charlie",
            "tool": "alert.notify_owner",
            "params": {"message": "Månedlig test av varsler", "priority": "high"},
        },
        sim.owner_ctx(),
    )
    sent = [m for m in sim.telephony.outbox if m.body == "Månedlig test av varsler"]
    report.add(
        "varsel når fram (SMS + push)",
        r.status == "executed" and bool(sent) and bool(sim.notifier.sent),
    )


def restore_from_export(store: MemoryObjectStore) -> tuple[bool, int]:
    """Rebuild the chain from the WORM export alone and verify every hash."""
    prev, n = GENESIS, 0
    for key in store.list("audit/"):
        for line in store.get(key).decode().splitlines():
            row = json.loads(line)
            ts = datetime.fromisoformat(row["ts"])
            ok = row["prev_hash"] == prev and row["hash"] == _hash(
                row["prev_hash"], row["seq"], ts, row["actor"], row["kind"], row["data"]
            )
            if not ok:
                return False, n
            prev, n = row["hash"], n + 1
    return True, n


def drill_anchors(sim: SimSystem, report: DrillReport) -> None:
    head = sim.audit.entries()[-1]
    sim.killswitch.anchor(head.seq, head.hash)
    report.add(
        "logg: forankret hash på nødstopp-enheten stemmer",
        check_anchors(sim.audit, sim.killswitch.anchors) == [],
    )


def drill_backup(sim: SimSystem, report: DrillReport) -> None:
    store = MemoryObjectStore()
    last = export_batch(sim.audit, store, sim.clock, 0)
    ok, n = restore_from_export(store)
    report.add("backup: eksport til WORM og gjenoppretting", ok and n == last, f"({n} rader)")
    try:
        store.put(store.list("audit/")[0], b"tuklet", sim.clock.now())
        report.add("backup: eksport kan ikke overskrives", False)
    except Exception:  # noqa: BLE001 - expected refusal
        report.add("backup: eksport kan ikke overskrives", True)


def drill_duress(sim: SimSystem, report: DrillReport) -> None:
    owner = sim.cfg.identity.owner_numbers[0]
    before = len(sim.ks_hal.sms_sent)
    said = sim.call("charlie", owner, ["Hva er klokka?", "Lås opp døra"], pin=DEMO_DURESS)
    normal = sim.call("charlie", owner, ["Hva er klokka?"], pin=DEMO_PIN)
    alarm = [
        s for s in sim.ks_hal.sms_sent[before:] if s[0] == sim.cfg.identity.second_alarm_number
    ]
    report.add("tvangskode: ser normal ut", said[2] == normal[2])
    report.add("tvangskode: stille alarm til andre nummer", len(alarm) == 1)
    report.add(
        "tvangskode: bare harmløse svar",
        sim.ha.calls == [] and not sim.proposals.list_status("pending"),
    )
    report.add(
        "tvangskode: ikke synlig i loggen",
        all(
            "duress" not in json.dumps(e.data) and "duress" not in e.kind
            for e in sim.audit.entries()
        ),
    )


def drill_killswitch(sim: SimSystem, report: DrillReport) -> None:
    sim.killswitch.app_button("PAUSE")
    report.add(
        "kill switch: knapp i appen gir PAUSED", sim.ks_executor_view.state() == KSState.PAUSED
    )
    report.add(
        "kill switch: gjenoppstart uten nøkkelbytte nektes",
        sim.resume_killswitch(keys_replaced=False) == KSState.PAUSED,
    )
    report.add("kill switch: manuell gjenoppstart", sim.resume_killswitch() == KSState.ACTIVE)


def run_sim() -> DrillReport:
    sim = SimSystem()
    report = DrillReport()
    steps: list[Callable[[SimSystem, DrillReport], None]] = [
        drill_alerts,
        drill_duress,
        drill_killswitch,
        drill_backup,
    ]
    for step in steps:
        print(f"- {step.__name__}")
        step(sim, report)
    sim.audit.append("ops", "drill.monthly", {"ok": report.ok, "steps": len(report.steps)})
    return report


def _compose(args: list[str], stdin: bytes | None = None) -> bytes:
    docker = shutil.which("docker")
    if docker is None:
        raise FileNotFoundError("docker")
    return subprocess.run(  # noqa: S603 - fixed binary and arguments
        [docker, "compose", "exec", "-T", "db", *args],
        input=stdin,
        capture_output=True,
        check=True,
        cwd=ROOT,
    ).stdout


def run_compose() -> DrillReport:
    """pg_dump of the running database -> restore into a scratch DB -> verify the chain."""
    report = DrillReport()
    scratch = f"restore_{uuid.uuid4().hex[:8]}"

    def psql(sql: str, db: str = "assistants") -> str:
        return _compose(["psql", "-U", "postgres", "-d", db, "-tAc", sql]).decode().strip()

    try:
        psql(f"create database {scratch}")
        dump = _compose(["pg_dump", "-U", "postgres", "assistants"])
        _compose(["psql", "-q", "-U", "postgres", "-d", scratch], stdin=dump)
        ok = psql("select ok from audit.verify_chain()", scratch) == "t"
        same = psql("select count(*) from audit.log") == psql(
            "select count(*) from audit.log", scratch
        )
        report.add("backup: pg_dump -> gjenoppretting -> kjeden verifisert", ok and same)
    finally:
        psql(f"drop database if exists {scratch} with (force)")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sim", action="store_true")
    parser.add_argument("--compose", action="store_true")
    args = parser.parse_args()
    print(
        "Månedlig test (simulerte enheter)" if not args.compose else "Månedlig backuptest (compose)"
    )
    report = run_compose() if args.compose else run_sim()
    print("Månedlig test OK" if report.ok else "Månedlig test FEILET")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
