"""Phase 1 acceptance: call in, level 1 answer, PIN, proposal, approval, execution, log."""

from __future__ import annotations

from assistants.sim import DEMO_PIN, SimSystem


def test_end_to_end_scenario() -> None:
    sim = SimSystem()
    owner = sim.cfg.identity.owner_numbers[0]
    call = "CA-demo"
    # 1. Call in: consent notice is played before anything is transcribed.
    first = sim.gateway.call_started("charlie", call, owner)
    assert "transkribert" in first
    assert sim.stt.calls == 0
    sim.gateway.consent(call, True)
    # 2. Level 1 answer on known number alone.
    assert "Klokka er" in sim.gateway.audio(call, b"Hva er klokka?")
    assert "Kalenderen er tom" in sim.gateway.audio(call, b"Hva skjer i kalenderen?")
    # Level 2 alone.
    assert "nivå 2" in sim.gateway.audio(call, b"Sett opp verkstedtid i morgen kl 10")
    assert len(sim.calendar.events) == 1
    # 3. PIN.
    assert sim.gateway.dtmf(call, DEMO_PIN) == "Takk, PIN godkjent."
    # 4. Proposal (level 3): code-built confirmation, nothing sent yet.
    reply = sim.gateway.audio(call, "Inviter kari@example.no til møte i morgen kl 14".encode())
    assert "nivå 3" in reply
    assert "passkey" in reply
    [pending] = sim.proposals.list_status("pending")
    assert len(sim.calendar.events) == 1
    sim.gateway.call_ended(call)
    # 5. Approval on the phone (passkey, simulated) -> 6. execution.
    result = sim.approve_on_phone(str(pending.id))
    assert result.status == "executed"
    assert sim.calendar.events[-1].attendees == ("kari@example.no",)
    # 7. Log: complete, chained, verifiable; consent and transcript stored.
    kinds = sim.audit.kinds()
    for kind in (
        "call.started",
        "consent",
        "auth.pin_verified",
        "action.pending",
        "action.executed",
        "call.ended",
    ):
        assert kind in kinds
    assert sim.audit.verify()
    assert sim.transcripts.consent[0].answer == "accepted"
    assert sim.transcripts.raw_count() == 1


def test_declined_consent_means_no_transcription() -> None:
    sim = SimSystem()
    said = sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?"], consent=False)
    assert "avslutter" in said[1]
    assert sim.stt.calls == 0
    assert sim.transcripts.raw_count() == 0
    assert sim.transcripts.consent[0].answer == "declined"
