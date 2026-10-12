"""Property tests (hypothesis) for the kill switch state machine."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from assistants.killswitch.core import (
    Anomaly,
    AppButton,
    DuressSignal,
    Effect,
    Event,
    KSState,
    LolaSwitch,
    Machine,
    PhysicalSwitch,
    ResumeRequest,
    Ring,
    Rules,
    SmsCode,
    step,
)

RANK = {KSState.ACTIVE: 0, KSState.PAUSED: 1, KSState.STOPPED: 2}
times = st.floats(min_value=0, max_value=10_000, allow_nan=False)
numbers = st.sampled_from(["+4700000001", "+4791111111", "+4792222222", "+41790000000"])

events: st.SearchStrategy[Event] = st.one_of(
    st.builds(Ring, number=numbers, known=st.booleans(), at=times),
    st.builds(SmsCode, number=numbers, code_valid=st.booleans(), at=times),
    st.builds(PhysicalSwitch, at=times),
    st.builds(AppButton, action=st.sampled_from(["PAUSE", "STOP"]), at=times),
    st.builds(
        Anomaly, rule=st.text(max_size=8), action=st.sampled_from(["PAUSED", "STOPPED"]), at=times
    ),
    st.builds(DuressSignal, at=times),
    st.builds(LolaSwitch, enabled=st.booleans(), at=times),
    st.builds(
        ResumeRequest,
        pin_ok=st.booleans(),
        second_device_confirmed=st.booleans(),
        log_reviewed=st.booleans(),
        keys_rotated=st.booleans(),
        at=times,
    ),
)
rules = st.builds(
    Rules,
    pause_window_s=st.integers(1, 600),
    unknown_rings_to_pause=st.integers(1, 5),
    known_rings_to_pause=st.integers(1, 3),
    stop_window_s=st.integers(1, 3600),
    rings_to_stop=st.integers(1, 6),
    physical_switch_action=st.sampled_from(["PAUSED", "STOPPED"]),
    max_rings_tracked=st.integers(1, 64),
)


@settings(max_examples=400)
@given(st.lists(events, max_size=40), rules)
def test_active_only_reachable_through_full_resume(evs: list[Event], r: Rules) -> None:
    m = Machine()
    for ev in evs:
        before = m.state
        m, _ = step(m, ev, r)
        if before != KSState.ACTIVE and m.state == KSState.ACTIVE:
            assert isinstance(ev, ResumeRequest)
            assert ev.pin_ok
            assert ev.second_device_confirmed
            assert ev.log_reviewed
            assert ev.keys_rotated


@settings(max_examples=400)
@given(st.lists(events, max_size=40), rules)
def test_only_resume_lowers_the_state(evs: list[Event], r: Rules) -> None:
    m = Machine()
    for ev in evs:
        before = m.state
        m, _ = step(m, ev, r)
        if RANK[m.state] < RANK[before]:
            assert isinstance(ev, ResumeRequest)


@settings(max_examples=400)
@given(st.lists(events, max_size=40), rules)
def test_stopped_always_comes_with_revocation_and_egress_cut(evs: list[Event], r: Rules) -> None:
    m = Machine()
    for ev in evs:
        before = m.state
        m, effects = step(m, ev, r)
        if before != KSState.STOPPED and m.state == KSState.STOPPED:
            assert Effect.REVOKE_KEYS in effects
            assert Effect.CUT_EGRESS in effects
            assert Effect.STOP_CHARLIE in effects
        if before == KSState.ACTIVE and m.state == KSState.PAUSED:
            assert Effect.FREEZE_PROPOSALS in effects


@settings(max_examples=300)
@given(st.lists(events, max_size=40), rules)
def test_lola_switch_never_changes_charlie_state(evs: list[Event], r: Rules) -> None:
    m = Machine()
    for ev in evs:
        before = m.state
        m, _ = step(m, ev, r)
        if isinstance(ev, LolaSwitch | DuressSignal):
            assert m.state == before


@settings(max_examples=300)
@given(st.lists(events, max_size=60), rules)
def test_ring_history_bounded_and_step_is_deterministic(evs: list[Event], r: Rules) -> None:
    m1 = m2 = Machine()
    for ev in evs:
        m1, e1 = step(m1, ev, r)
        m2, e2 = step(m2, ev, r)
        assert (m1, e1) == (m2, e2)
        assert len(m1.rings) <= r.max_rings_tracked
