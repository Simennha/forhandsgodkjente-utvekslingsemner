"""Kill switch state machine: every ring rule, every trigger, restart conditions."""

from __future__ import annotations

import pytest

from assistants.killswitch.core import (
    Anomaly,
    AppButton,
    DuressSignal,
    Effect,
    KSState,
    LolaSwitch,
    Machine,
    PhysicalSwitch,
    ResumeRequest,
    Ring,
    Rules,
    SmsCode,
    charlie_may_run,
    lola_may_run,
    step,
)

R = Rules()
ALL_OK = {
    "pin_ok": True,
    "second_device_confirmed": True,
    "log_reviewed": True,
    "keys_rotated": True,
}


def run(events: list[object], m: Machine | None = None, rules: Rules = R) -> Machine:
    machine = m or Machine()
    for ev in events:
        machine, _ = step(machine, ev, rules)  # type: ignore[arg-type]
    return machine


# ------------------------------------------------------------------ ring rules


def test_one_ring_from_unknown_does_nothing() -> None:
    m, effects = step(Machine(), Ring("+4791111111", known=False, at=0), R)
    assert m.state == KSState.ACTIVE
    assert effects == ()


def test_two_rings_from_unknown_within_two_minutes_pauses() -> None:
    m = run([Ring("+4791111111", False, 0), Ring("+4791111111", False, 119)])
    assert m.state == KSState.PAUSED
    assert m.reason == "ring:unknown"


def test_two_rings_from_unknown_more_than_two_minutes_apart_do_not_pause() -> None:
    m = run([Ring("+4791111111", False, 0), Ring("+4791111111", False, 121)])
    assert m.state == KSState.ACTIVE


def test_rings_from_two_different_unknown_numbers_do_not_pause() -> None:
    m = run([Ring("+4791111111", False, 0), Ring("+4792222222", False, 10)])
    assert m.state == KSState.ACTIVE


def test_one_ring_from_known_number_pauses() -> None:
    m, effects = step(Machine(), Ring("+4700000001", known=True, at=0), R)
    assert m.state == KSState.PAUSED
    assert m.reason == "ring:known"
    assert Effect.STOP_CHARLIE in effects
    assert Effect.FREEZE_PROPOSALS in effects
    assert Effect.REVOKE_KEYS not in effects


def test_third_ring_stops() -> None:
    m = run([Ring("+4700000001", True, 0), Ring("+4700000001", True, 30)])
    assert m.state == KSState.PAUSED
    m, effects = step(m, Ring("+4700000001", True, 60), R)
    assert m.state == KSState.STOPPED
    assert {Effect.REVOKE_KEYS, Effect.CUT_EGRESS, Effect.STOP_CHARLIE} <= set(effects)


def test_third_ring_from_mixed_numbers_stops() -> None:
    m = run([Ring("+1", False, 0), Ring("+2", False, 5), Ring("+3", False, 10)])
    assert m.state == KSState.STOPPED


def test_third_ring_outside_stop_window_does_not_stop() -> None:
    m = run([Ring("+1", True, 0), Ring("+2", False, 300), Ring("+3", False, 601)])
    assert m.state == KSState.PAUSED


def test_sms_with_correct_code_stops() -> None:
    m, effects = step(Machine(), SmsCode("+4799", code_valid=True, at=0), R)
    assert m.state == KSState.STOPPED
    assert Effect.REVOKE_KEYS in effects


def test_sms_with_wrong_code_does_nothing() -> None:
    m, effects = step(Machine(), SmsCode("+4799", code_valid=False, at=0), R)
    assert m.state == KSState.ACTIVE
    assert effects == ()


def test_ring_rules_come_from_config() -> None:
    rules = Rules(unknown_rings_to_pause=3, pause_window_s=10, rings_to_stop=5)
    m = run([Ring("+1", False, 0), Ring("+1", False, 5)], rules=rules)
    assert m.state == KSState.ACTIVE
    m = run([Ring("+1", False, 0), Ring("+1", False, 5), Ring("+1", False, 9)], rules=rules)
    assert m.state == KSState.PAUSED


def test_old_rings_are_forgotten() -> None:
    m = run([Ring("+1", False, 0), Ring("+1", False, 10_000)])
    assert len(m.rings) == 1


def test_ring_history_is_bounded() -> None:
    rules = Rules(max_rings_tracked=4, rings_to_stop=1000, unknown_rings_to_pause=1000)
    m = run([Ring(f"+{i}", False, i) for i in range(20)], rules=rules)
    assert len(m.rings) == 4


# ------------------------------------------------------------------ other triggers


def test_physical_switch_stops_by_default() -> None:
    m, effects = step(Machine(), PhysicalSwitch(at=0), R)
    assert m.state == KSState.STOPPED
    assert Effect.CUT_EGRESS in effects


def test_physical_switch_action_configurable() -> None:
    m, _ = step(Machine(), PhysicalSwitch(at=0), Rules(physical_switch_action="PAUSED"))
    assert m.state == KSState.PAUSED


@pytest.mark.parametrize(
    ("action", "state"), [("PAUSE", KSState.PAUSED), ("STOP", KSState.STOPPED)]
)
def test_app_button(action: str, state: KSState) -> None:
    m, _ = step(Machine(), AppButton(action=action, at=0), R)  # type: ignore[arg-type]
    assert m.state == state


@pytest.mark.parametrize(
    ("action", "state"), [("PAUSED", KSState.PAUSED), ("STOPPED", KSState.STOPPED)]
)
def test_anomaly(action: str, state: KSState) -> None:
    m, _ = step(Machine(), Anomaly(rule="many-denials", action=action, at=0), R)  # type: ignore[arg-type]
    assert m.state == state
    assert m.reason == "anomaly:many-denials"


def test_triggers_never_downgrade() -> None:
    m = run([SmsCode("+1", True, 0)])
    m2, effects = step(m, AppButton(action="PAUSE", at=1), R)
    assert m2.state == KSState.STOPPED
    assert effects == ()


def test_duress_sends_silent_alarm_without_state_change() -> None:
    m, effects = step(Machine(), DuressSignal(at=0), R)
    assert m.state == KSState.ACTIVE
    assert effects == (Effect.SILENT_ALARM,)


# ------------------------------------------------------------------ Lola


def test_lola_can_be_turned_off_alone() -> None:
    m, effects = step(Machine(), LolaSwitch(enabled=False, at=0), R)
    assert m.state == KSState.ACTIVE
    assert charlie_may_run(m)
    assert not lola_may_run(m)
    assert effects == (Effect.STOP_LOLA,)
    m, effects = step(m, LolaSwitch(enabled=True, at=1), R)
    assert lola_may_run(m)
    assert effects == (Effect.START_LOLA,)


def test_lola_runs_while_charlie_paused_but_not_when_stopped() -> None:
    paused = run([AppButton(action="PAUSE", at=0)])
    assert lola_may_run(paused)
    assert not charlie_may_run(paused)
    stopped = run([AppButton(action="STOP", at=0)])
    assert not lola_may_run(stopped)


def test_lola_cannot_be_started_when_stopped() -> None:
    m = run([LolaSwitch(enabled=False, at=0), AppButton(action="STOP", at=1)])
    m, effects = step(m, LolaSwitch(enabled=True, at=2), R)
    assert not m.lola_enabled
    assert effects == ()


# ------------------------------------------------------------------ restart


@pytest.mark.parametrize("missing", list(ALL_OK))
@pytest.mark.parametrize("start", [KSState.PAUSED, KSState.STOPPED])
def test_resume_requires_every_condition(missing: str, start: KSState) -> None:
    m = Machine(state=start)
    flags = {**ALL_OK, missing: False}
    m2, effects = step(m, ResumeRequest(**flags, at=0), R)
    assert m2.state == start
    assert effects == (Effect.NOTIFY_OWNER,)


@pytest.mark.parametrize("start", [KSState.PAUSED, KSState.STOPPED])
def test_resume_with_all_conditions(start: KSState) -> None:
    m2, effects = step(
        Machine(state=start, rings=(Ring("+1", True, 0),)), ResumeRequest(**ALL_OK, at=0), R
    )
    assert m2.state == KSState.ACTIVE
    assert m2.rings == ()
    assert Effect.RESUME_CHARLIE in effects


def test_resume_when_active_is_noop() -> None:
    m, effects = step(Machine(), ResumeRequest(**ALL_OK, at=0), R)
    assert m == Machine()
    assert effects == ()


def test_no_effect_touches_alarm_fire_or_estop() -> None:
    names = {e.value for e in Effect}
    for word in ("ALARM_OFF", "DISARM", "FIRE", "ESTOP", "EMERGENCY", "MACHINE"):
        assert not any(word in n for n in names)


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(AssertionError):
        step(Machine(), object(), R)  # type: ignore[arg-type]
