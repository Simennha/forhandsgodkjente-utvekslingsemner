"""Kill-switch device + lease + server integration (simulated hardware)."""

from __future__ import annotations

import secrets
from typing import Any

import pytest

from assistants.killswitch.core import KSState
from assistants.killswitch.driver import ResumeAttempt
from assistants.sim import DEMO_KS_SMS, DEMO_PIN, SimSystem

EMAIL = {"to": ["ola@example.no"], "subject": "Hei", "body": "Hallo"}


def proposal(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "proposal/v1", "agent": "charlie", "tool": tool, "params": params}


@pytest.fixture
def sim() -> SimSystem:
    return SimSystem()


def ring(sim: SimSystem, number: str, seconds_later: float = 0) -> None:
    sim.advance(seconds_later)
    sim.ks_hal.modem_queue.append(("ring", number, ""))
    sim.killswitch.poll()


def test_two_unknown_rings_pause_through_modem(sim: SimSystem) -> None:
    ring(sim, "+4799999999")
    assert sim.killswitch.machine.state == KSState.ACTIVE
    ring(sim, "+4799999999", 60)
    assert sim.killswitch.machine.state == KSState.PAUSED
    assert sim.ks_executor_view.state() == KSState.PAUSED
    assert sim.ks_hal.led == KSState.PAUSED


def test_known_ring_pauses_and_charlie_stops_answering(sim: SimSystem) -> None:
    ring(sim, sim.cfg.identity.owner_numbers[0])
    said = sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?"])
    assert "ikke tilgjengelig" in said[-1]
    r = sim.executor.submit(proposal("time.read", {}), sim.owner_ctx())
    assert r.reason == "killswitch"


def test_third_ring_stops_revokes_and_cuts_egress(sim: SimSystem) -> None:
    for i in range(3):
        ring(sim, f"+47900000{i:02d}", 10)
    assert sim.killswitch.machine.state == KSState.STOPPED
    assert all(r.revoked for r in sim.revokers)
    assert sim.egress.cut
    # Alarm, fire and machine emergency stops are separate circuits: nothing here touches them.
    assert all(call[0] not in ("alarm_control_panel", "siren") for call in sim.ha.calls)


def test_sms_code_stops(sim: SimSystem) -> None:
    sim.ks_hal.modem_queue.append(("sms", "+4791111111", "feil kode"))
    sim.killswitch.poll()
    assert sim.killswitch.machine.state == KSState.ACTIVE
    sim.ks_hal.modem_queue.append(("sms", "+4791111111", DEMO_KS_SMS))
    sim.killswitch.poll()
    assert sim.killswitch.machine.state == KSState.STOPPED


def test_physical_switch_works_without_network(sim: SimSystem) -> None:
    sim.ks_hal.switch = True
    sim.killswitch.poll()
    assert sim.killswitch.machine.state == KSState.STOPPED
    assert sim.ks_hal.led == KSState.STOPPED  # local indicator, no network needed


def test_pause_freezes_pending_proposals(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    assert r.status == "pending"
    sim.killswitch.app_button("PAUSE")
    assert [p.status for p in sim.proposals.list_status("frozen")] == ["frozen"]


def test_lease_fail_closed_when_device_silent(sim: SimSystem) -> None:
    assert sim.ks_executor_view.charlie_may_act()
    sim.clock.advance(sim.cfg.killswitch.lease_ttl_s + 1)  # device unplugged: no new lease
    assert not sim.ks_executor_view.charlie_may_act()
    assert sim.executor.submit(proposal("time.read", {}), sim.owner_ctx()).reason == "killswitch"


def test_old_lease_cannot_be_replayed(sim: SimSystem) -> None:
    tokens: list[str] = []
    sim.killswitch.publish_lease = tokens.append  # capture what the device sends
    sim.killswitch.publish()
    active_token = tokens[-1]
    sim.killswitch.publish_lease = sim._on_lease
    sim.killswitch.app_button("STOP")
    assert not sim.ks_executor_view.accept(active_token)
    assert sim.ks_executor_view.state() == KSState.STOPPED


def test_forged_lease_rejected(sim: SimSystem) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415

    from assistants.killswitch.lease import LeaseSigner  # noqa: PLC0415

    sim.killswitch.app_button("PAUSE")
    forged = LeaseSigner(Ed25519PrivateKey.generate())
    for _ in range(5):
        token = forged.sign(KSState.ACTIVE, True, sim.clock.now(), 30)
    assert not sim.ks_executor_view.accept(token)
    assert sim.ks_executor_view.state() == KSState.PAUSED


def test_lola_can_be_turned_off_alone(sim: SimSystem) -> None:
    sim.killswitch.lola(False)
    assert sim.ks_executor_view.charlie_may_act()
    reply = sim.gateway.guest_chat("Når er det åpent?", "g1")
    assert "ikke tilgjengelig" in reply
    sim.killswitch.lola(True)
    assert "torsdager" in sim.gateway.guest_chat("Når er det åpent?", "g1")


def test_lola_keeps_working_while_charlie_paused(sim: SimSystem) -> None:
    sim.killswitch.app_button("PAUSE")
    assert "torsdager" in sim.gateway.guest_chat("Når er det åpent?", "g2")


def test_duress_sends_silent_alarm_on_independent_channel(sim: SimSystem) -> None:
    sim.killswitch.duress()
    assert (
        sim.cfg.identity.second_alarm_number,
        "STILLE ALARM: tvangskode brukt mot Charlie.",
    ) in sim.ks_hal.sms_sent
    assert any(t == "Stille alarm" for t, _, _ in sim.ks_notifier.sent)
    assert sim.killswitch.machine.state == KSState.ACTIVE


# ---------------------------------------------------------------- restart


@pytest.mark.parametrize("start", ["PAUSE", "STOP"])
def test_resume_with_all_conditions(sim: SimSystem, start: str) -> None:
    sim.killswitch.app_button(start)
    assert sim.resume_killswitch() == KSState.ACTIVE
    assert sim.ks_executor_view.charlie_may_act()


@pytest.mark.parametrize("start", ["PAUSE", "STOP"])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"pin": "000000"},
        {"second_device": False},
        {"reviewed": False},
        {"keys_replaced": False},
    ],
)
def test_resume_fails_without_any_condition(
    sim: SimSystem, start: str, kwargs: dict[str, Any]
) -> None:
    sim.killswitch.app_button(start)
    assert sim.resume_killswitch(**kwargs) != KSState.ACTIVE
    assert not sim.ks_executor_view.charlie_may_act()


def test_resume_nonce_is_single_use(sim: SimSystem) -> None:
    sim.killswitch.app_button("STOP")
    sim.key_inventory.active = {"anthropic:new-1"}
    nonce = sim.killswitch.new_resume_nonce(secrets.token_urlsafe(8))
    head = sim.audit.head_hash()
    attempt = ResumeAttempt("000000", nonce, head, sim.devices.sign_resume(nonce, head))
    assert sim.killswitch.resume(attempt) == KSState.STOPPED  # wrong PIN burns the nonce
    retry = ResumeAttempt(DEMO_PIN, nonce, head, sim.devices.sign_resume(nonce, head))
    assert sim.killswitch.resume(retry) == KSState.STOPPED


def test_frozen_proposals_stay_frozen_after_resume(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    sim.killswitch.app_button("PAUSE")
    sim.resume_killswitch()
    req = sim.approvals.pending(r.proposal_id or "")
    assert req is not None
    token = sim.approvals.approve(req.proposal_id, req.method, sim.devices.sign_approval(req))
    assert sim.executor.approve(token).status == "denied"
    assert sim.email.sent == []


def test_nothing_restarts_automatically(sim: SimSystem) -> None:
    sim.killswitch.app_button("STOP")
    for _ in range(50):
        sim.advance(600)
        sim.killswitch.poll()
    assert sim.killswitch.machine.state == KSState.STOPPED
