"""The monthly drill (alerts, backup restore, duress code, kill switch) passes in simulation."""

from __future__ import annotations

from assistants.adapters.objectstore import MemoryObjectStore
from assistants.audit import export_batch
from assistants.ops.monthly import restore_from_export, run_sim
from assistants.sim import SimSystem


def test_monthly_drill_passes() -> None:
    report = run_sim()
    assert report.ok, report.steps
    names = " ".join(name for name, _, _ in report.steps)
    for word in ("varsel", "backup", "tvangskode", "kill switch"):
        assert word in names


def test_restore_detects_tampered_backup() -> None:
    sim = SimSystem()
    sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?"])
    store = MemoryObjectStore()
    export_batch(sim.audit, store, sim.clock, 0)
    key = store.list("audit/")[0]
    tampered = store.objects[key].data.replace(b"call.started", b"call.STARTED")
    store.objects[key] = type(store.objects[key])(key, tampered, store.objects[key].retain_until)
    ok, _ = restore_from_export(store)
    assert not ok
