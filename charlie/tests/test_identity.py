"""Identity: known number, PIN, duress code, test word, lockout."""

from __future__ import annotations

import pytest

from assistants.events import Trust
from assistants.identity import LIVENESS_REPLY, PIN_ACCEPTED_REPLY
from assistants.sim import DEMO_DURESS, DEMO_PIN, DEMO_TEST_WORD, SimSystem


@pytest.fixture
def sim() -> SimSystem:
    return SimSystem()


def owner(sim: SimSystem) -> str:
    return sim.cfg.identity.owner_numbers[0]


def test_known_number_alone_gives_levels_1_2(sim: SimSystem) -> None:
    said = sim.call(
        "charlie",
        owner(sim),
        ["Husk at loddebolten må byttes", "Send e-post til ola@example.no om test: hei"],
    )
    assert "Gjort: notes.remember (nivå 2)" in said[2]
    assert "nivå 3" in said[3]
    assert "PIN" in said[3]
    assert sim.proposals.list_status("pending") == []


def test_number_plus_pin_gives_level3(sim: SimSystem) -> None:
    said = sim.call(
        "charlie", owner(sim), ["Send e-post til ola@example.no om test: hei"], pin=DEMO_PIN
    )
    assert said[2] == PIN_ACCEPTED_REPLY
    assert "passkey" in said[3]
    assert len(sim.proposals.list_status("pending")) == 1


def test_unknown_number_gets_nothing(sim: SimSystem) -> None:
    said = sim.call("charlie", "+4791234567", ["Hva er klokka?"], pin=DEMO_PIN)
    assert len(said) == 1  # no consent prompt, no transcription, nothing opened
    assert sim.stt.calls == 0
    assert sim.costs.entries == []


def test_duress_looks_normal_restricts_and_alarms_silently(sim: SimSystem) -> None:
    utterances = [
        "Hva er klokka?",
        "Send e-post til ola@example.no om penger: send alt",
        "Lås opp døra",
        "Slå på lys i verkstedet",
    ]
    normal = SimSystem().call("charlie", owner(sim), utterances, pin=DEMO_PIN)
    said = sim.call("charlie", owner(sim), utterances, pin=DEMO_DURESS)
    # Red team #3: every reply is word for word what a correct PIN would produce.
    assert said == normal
    assert said[2] == PIN_ACCEPTED_REPLY
    # ...but nothing happened.
    assert sim.proposals.list_status("pending") == []
    assert sim.email.sent == []
    assert sim.ha.calls == []
    # Silent alarm went out on the kill-switch device's own modem to the second number.
    assert any(n == sim.cfg.identity.second_alarm_number for n, _ in sim.ks_hal.sms_sent)
    # And the duress state is not visible in the server-side audit log as such.
    for e in sim.audit.entries():
        assert "duress" not in str(e.data).lower()
        assert "duress" not in e.kind
    assert "auth.pin_verified" in sim.audit.kinds()


def test_codes_never_reach_logs_models_or_events(sim: SimSystem) -> None:
    sim.call("charlie", owner(sim), ["status"], pin=DEMO_PIN)
    sim.call("charlie", owner(sim), ["status"], pin=DEMO_DURESS)
    sim.call("charlie", owner(sim), [DEMO_TEST_WORD])
    blob = " ".join(str(e) for e in sim.audit.entries()) + " ".join(
        str(e) for e in sim.queue.history
    )
    blob += " ".join(c.user_content for c in sim.raw_llm.calls)  # type: ignore[attr-defined]
    for code in (DEMO_PIN, DEMO_DURESS):
        assert code not in blob
    assert DEMO_TEST_WORD not in " ".join(c.user_content for c in sim.raw_llm.calls)  # type: ignore[attr-defined]


def test_test_word_gives_liveness_without_opening_anything(sim: SimSystem) -> None:
    said = sim.call("charlie", owner(sim), ["Blåbær, kompass!"])
    assert said[2] == LIVENESS_REPLY
    assert sim.raw_llm.calls == []  # type: ignore[attr-defined]
    assert sim.executor._proposals.list_status("executed") == []
    reply = sim.gateway.sms("charlie", owner(sim), "blåbær kompass")
    assert reply == LIVENESS_REPLY


def test_pin_lockout_after_three_failures(sim: SimSystem) -> None:
    call_id = "CA1"
    sim.gateway.call_started("charlie", call_id, owner(sim))
    sim.gateway.consent(call_id, True)
    for _ in range(3):
        sim.gateway.dtmf(call_id, "000000")
    assert "For mange" in sim.gateway.dtmf(call_id, DEMO_PIN)
    assert sim.gateway.calls[call_id].session.trust == Trust.OWNER_KNOWN
    sim.advance(sim.cfg.rate_limits.pin_lockout_s + 1)
    assert sim.gateway.dtmf(call_id, DEMO_PIN) == PIN_ACCEPTED_REPLY


def test_sms_never_reaches_level3(sim: SimSystem) -> None:
    reply = sim.gateway.sms("charlie", owner(sim), "Lås opp døra")
    assert "PIN" in reply
    assert sim.ha.calls == []


def test_spoofed_number_is_limited_to_reversible_levels(sim: SimSystem) -> None:
    # Caller ID can be forged. A forged owner number still never reaches level 3 without PIN.
    said = sim.call("charlie", owner(sim), ["Bestill 10 DRV-TMC2209 fra elfa", "Lås opp døra"])
    assert sim.proposals.list_status("pending") == []
    assert all("passkey" not in s for s in said)
