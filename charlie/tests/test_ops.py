"""Operations: anomaly rules trigger the kill switch, heartbeats, transcript retention."""

from __future__ import annotations

from datetime import timedelta

from assistants.killswitch.core import KSState
from assistants.ops.worker import (
    Heartbeats,
    check_heartbeats,
    detect_anomalies,
    purge_transcripts,
)
from assistants.sim import DEMO_PIN, SimSystem


def test_level4_attempts_trigger_pause() -> None:
    sim = SimSystem(compromised=True)
    owner = sim.cfg.identity.owner_numbers[0]
    sim.call("charlie", owner, ["Slett backup", "Slå av alarmen", "Start CNC-fresen"], pin=DEMO_PIN)
    fired = detect_anomalies(
        sim.audit, list(sim.cfg.killswitch.anomaly_rules), sim.clock, sim.killswitch.anomaly
    )
    assert "level4-attempts" in fired
    assert sim.killswitch.machine.state == KSState.PAUSED


def test_pin_bruteforce_triggers_pause() -> None:
    sim = SimSystem()
    owner = sim.cfg.identity.owner_numbers[0]
    for i in range(6):
        sim.advance(sim.cfg.rate_limits.pin_lockout_s + 1)
        call = f"CA{i}"
        sim.gateway.call_started("charlie", call, owner)
        sim.gateway.consent(call, True)
        sim.gateway.dtmf(call, "111111")
    detect_anomalies(
        sim.audit, list(sim.cfg.killswitch.anomaly_rules), sim.clock, sim.killswitch.anomaly
    )
    # 6 failures spread over > 1 hour do not all fall in the window; a burst does:
    for _ in range(6):
        sim.gateway.dtmf("CA5", "111111")
    fired = detect_anomalies(
        sim.audit, list(sim.cfg.killswitch.anomaly_rules), sim.clock, sim.killswitch.anomaly
    )
    assert "pin-bruteforce" in fired
    assert sim.killswitch.machine.state == KSState.PAUSED


def test_normal_use_triggers_nothing() -> None:
    sim = SimSystem()
    sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?", "status"])
    assert (
        detect_anomalies(
            sim.audit, list(sim.cfg.killswitch.anomaly_rules), sim.clock, sim.killswitch.anomaly
        )
        == []
    )
    assert sim.killswitch.machine.state == KSState.ACTIVE


def test_heartbeat_alerts_when_agent_goes_quiet() -> None:
    sim = SimSystem()
    hb = Heartbeats(stale_after_s=sim.cfg.heartbeat.stale_after_s)
    hb.beat("agents-charlie", sim.clock.now())
    hb.beat("agents-lola", sim.clock.now())
    alerts: list[str] = []
    assert check_heartbeats(hb, sim.clock, alerts.append) == []
    sim.clock.advance(60)
    hb.beat("agents-lola", sim.clock.now())
    sim.clock.advance(90)
    assert check_heartbeats(hb, sim.clock, alerts.append) == ["agents-charlie"]
    assert "agents-charlie" in alerts[0]


def test_raw_transcripts_deleted_after_7_days_extracts_kept() -> None:
    sim = SimSystem()
    sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?"])
    sim.transcripts.add_extract("CA1", "charlie", {"summary": "klokka"}, sim.clock.now())
    assert sim.transcripts.raw_count() == 1
    sim.clock.advance(timedelta(days=6, hours=23).total_seconds())
    assert purge_transcripts(sim.transcripts, sim.clock, sim.audit) == 0
    sim.clock.advance(timedelta(hours=2).total_seconds())
    assert purge_transcripts(sim.transcripts, sim.clock, sim.audit) == 1
    assert sim.transcripts.raw_count() == 0
    assert len(sim.transcripts.extracts("charlie", None, 10)) == 1


def test_call_transcripts_become_extracts_for_les_samtale() -> None:
    from assistants.ops.worker import extract_transcripts  # noqa: PLC0415

    sim = SimSystem()
    owner = sim.cfg.identity.owner_numbers[0]
    sim.call("charlie", owner, ["Husk at Kari kommer torsdag kl 18"])
    assert extract_transcripts(sim.transcripts, sim.extractor, sim.clock, sim.audit) == 1
    assert extract_transcripts(sim.transcripts, sim.extractor, sim.clock, sim.audit) == 0
    said = sim.call("charlie", owner, ["Hva snakket vi om forrige samtale?"])
    assert "Forrige samtale" in said[-1]
    assert "Kari" in said[-1]
    [extract] = sim.transcripts.extracts("charlie", None, 5)
    assert "Kari" in str(extract.extract)
