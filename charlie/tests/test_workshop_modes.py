"""Phase 3: Normal, Guest and Open workshop modes; microphone pipeline (simulated)."""

from __future__ import annotations

from assistants.adapters.llm import MockLLM
from assistants.events import Mode
from assistants.sim import DEMO_PIN, SimSystem


def llm_calls(sim: SimSystem) -> int:
    llm = sim.raw_llm
    assert isinstance(llm, MockLLM)
    return len(llm.calls)


def test_normal_mode_mic_answers_after_wake_word_only() -> None:
    sim = SimSystem()
    assert sim.relay.is_connected()
    assert sim.mic.process(b"hva er klokka") is None  # no wake word: dropped
    assert sim.stt.calls == 0
    reply = sim.mic.process(b"Charlie, hva er klokka?")
    assert reply is not None
    assert "Klokka er" in reply
    assert sim.stt.calls == 1
    assert sim.transcripts.raw_count() == 0  # mic utterances are never stored as transcripts


def test_mic_voice_is_limited_to_levels_1_and_2() -> None:
    sim = SimSystem()
    reply = sim.mic.process("Charlie, lås opp døra".encode())
    assert reply is not None
    assert "PIN" in reply
    assert sim.ha.calls == []
    assert sim.proposals.list_status("pending") == []
    reply = sim.mic.process("Charlie, slå på lys i verkstedet".encode())
    assert reply is not None
    assert "nivå 2" in reply


def test_guest_mode_only_harmless_answers() -> None:
    sim = SimSystem()
    sim.workshop.set_mode(Mode.GUEST)
    assert "Klokka er" in (sim.mic.process(b"Charlie, hva er klokka?") or "")
    reply = sim.mic.process("Charlie, hvor mange MOT-NEMA17 har vi på lager?".encode()) or ""
    assert "MOT-NEMA17" not in reply
    assert "12 stk" not in reply
    assert "Gjestemodus" in reply


def test_open_workshop_runs_no_audio_processing_at_all() -> None:
    sim = SimSystem()
    sim.workshop.set_mode(Mode.OPEN_WORKSHOP)
    # Physical: relay cut, camera audio off.
    assert not sim.relay.is_connected()
    assert not sim.camera.audio_enabled()
    before_llm = llm_calls(sim)
    owner = sim.cfg.identity.owner_numbers[0]
    for frame in (b"Charlie, hva er klokka?", b"charlie hjelp", b"bakgrunnsprat i verkstedet"):
        assert sim.mic.process(frame) is None
    said = sim.call("charlie", owner, ["Hva er klokka?"], pin=DEMO_PIN)
    said += sim.call("lola", "+4790000000", ["Når er det åpent?"])
    said.append(sim.gateway.door(b"Hei, pakke til dere"))
    # No wake-word detection, no speech-to-text, no model call from any audio source.
    assert sim.wake.calls == 0
    assert sim.stt.calls == 0
    assert llm_calls(sim) == before_llm
    assert sim.transcripts.raw_count() == 0
    assert any("åpen modus" in s for s in said)


def test_open_workshop_charlie_works_in_background_only() -> None:
    sim = SimSystem()
    sim.workshop.set_mode(Mode.OPEN_WORKSHOP)
    owner = sim.cfg.identity.owner_numbers[0]
    reply = sim.gateway.sms("charlie", owner, "Hva bør vi bestille i Sveits?")
    assert "innkjøpsforslag for CH" in reply
    reply = sim.gateway.sms("charlie", owner, "Hva skjer i kalenderen?")
    assert "Åpent verksted" in reply


def test_leaving_open_workshop_reconnects_relay() -> None:
    sim = SimSystem()
    sim.workshop.set_mode(Mode.OPEN_WORKSHOP)
    sim.workshop.set_mode(Mode.NORMAL)
    assert sim.relay.history[-2:] == [False, True]
    assert sim.mic.process(b"Charlie, status") is not None


def test_charlie_has_no_tool_to_change_mode() -> None:
    assert not [t for t in sim_tools() if "mode" in t or "relay" in t or "mic" in t]


def sim_tools() -> list[str]:
    from assistants.policy import default_policy  # noqa: PLC0415

    return list(default_policy().tools)


def test_mode_survives_restart_and_unknown_means_mics_cut() -> None:
    """Red team #2: a crash during open workshop must not turn the microphones back on."""
    from assistants.adapters.hardware import SimCameraAudio, SimMicRelay  # noqa: PLC0415
    from assistants.audit import MemoryAuditLog  # noqa: PLC0415
    from assistants.common.clock import FakeClock  # noqa: PLC0415
    from assistants.workshop.modes import MemoryModeStore, WorkshopModeController  # noqa: PLC0415

    sim = SimSystem()
    sim.workshop.set_mode(Mode.OPEN_WORKSHOP)
    relay = SimMicRelay(connected=True)
    restarted = WorkshopModeController(relay, SimCameraAudio(), sim.audit, store=sim.mode_store)
    assert restarted.mode() == Mode.OPEN_WORKSHOP
    assert not relay.is_connected()
    fresh_relay = SimMicRelay(connected=True)
    fresh = WorkshopModeController(
        fresh_relay, SimCameraAudio(), MemoryAuditLog(FakeClock()), store=MemoryModeStore()
    )
    assert fresh.mode() == Mode.OPEN_WORKSHOP
    assert not fresh_relay.is_connected()


def test_file_mode_store(tmp_path: object) -> None:
    from pathlib import Path  # noqa: PLC0415

    from assistants.workshop.modes import FileModeStore  # noqa: PLC0415

    store = FileModeStore(Path(str(tmp_path)) / "mode.json")
    assert store.load() is None
    store.save(Mode.GUEST)
    assert store.load() == Mode.GUEST
    (Path(str(tmp_path)) / "mode.json").write_text("garbage")
    assert store.load() is None
