"""Kill switch: a pure, deterministic state machine.

No I/O, no clocks, no randomness: ``step(machine, event, rules)`` returns the next machine
and the effects the driver must carry out. 100 % line and branch coverage plus property
tests (tests/killswitch/).

States: ACTIVE -> PAUSED -> STOPPED. Triggers only ever escalate. The ONLY transition back
to ACTIVE is a ResumeRequest where every condition holds: correct PIN, confirmation from a
second device, audit log reviewed and keys rotated. Nothing restarts automatically.

Alarm, fire alarm and machine emergency stops are separate circuits: there is no effect
in this module that can touch them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal, assert_never


class KSState(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


_RANK = {KSState.ACTIVE: 0, KSState.PAUSED: 1, KSState.STOPPED: 2}


class Effect(StrEnum):
    STOP_CHARLIE = "STOP_CHARLIE"
    FREEZE_PROPOSALS = "FREEZE_PROPOSALS"
    REVOKE_KEYS = "REVOKE_KEYS"
    CUT_EGRESS = "CUT_EGRESS"
    RESUME_CHARLIE = "RESUME_CHARLIE"
    STOP_LOLA = "STOP_LOLA"
    START_LOLA = "START_LOLA"
    NOTIFY_OWNER = "NOTIFY_OWNER"
    SILENT_ALARM = "SILENT_ALARM"


@dataclass(frozen=True)
class Rules:
    pause_window_s: float = 120
    unknown_rings_to_pause: int = 2
    known_rings_to_pause: int = 1
    stop_window_s: float = 600
    rings_to_stop: int = 3
    physical_switch_action: Literal["PAUSED", "STOPPED"] = "STOPPED"
    max_rings_tracked: int = 256


# ---------------------------------------------------------------- events


@dataclass(frozen=True)
class Ring:
    """A call to the secret kill-switch number (from ANY number)."""

    number: str
    known: bool
    at: float


@dataclass(frozen=True)
class SmsCode:
    """An SMS to the secret number. ``code_valid`` is decided by the driver (hash compare)."""

    number: str
    code_valid: bool
    at: float


@dataclass(frozen=True)
class PhysicalSwitch:
    """Physical switch at the entrance. Works without any network."""

    at: float


@dataclass(frozen=True)
class AppButton:
    action: Literal["PAUSE", "STOP"]
    at: float


@dataclass(frozen=True)
class Anomaly:
    """Unusual activity detected in the audit log by configured rules."""

    rule: str
    action: Literal["PAUSED", "STOPPED"]
    at: float


@dataclass(frozen=True)
class DuressSignal:
    """Duress code used on Charlie: silent alarm via the independent channel."""

    at: float


@dataclass(frozen=True)
class LolaSwitch:
    """Turn Lola off/on alone, without touching Charlie."""

    enabled: bool
    at: float


@dataclass(frozen=True)
class ResumeRequest:
    """Manual restart. Each flag is verified by the driver, never by a model."""

    pin_ok: bool
    second_device_confirmed: bool
    log_reviewed: bool
    keys_rotated: bool
    at: float

    @property
    def all_conditions(self) -> bool:
        return (
            self.pin_ok and self.second_device_confirmed and self.log_reviewed and self.keys_rotated
        )


Event = (
    Ring
    | SmsCode
    | PhysicalSwitch
    | AppButton
    | Anomaly
    | DuressSignal
    | LolaSwitch
    | ResumeRequest
)


# ---------------------------------------------------------------- machine


@dataclass(frozen=True)
class Machine:
    state: KSState = KSState.ACTIVE
    lola_enabled: bool = True
    rings: tuple[Ring, ...] = ()
    reason: str = "start"


_ESCALATION_EFFECTS = {
    KSState.PAUSED: (Effect.STOP_CHARLIE, Effect.FREEZE_PROPOSALS, Effect.NOTIFY_OWNER),
    KSState.STOPPED: (
        Effect.STOP_CHARLIE,
        Effect.FREEZE_PROPOSALS,
        Effect.REVOKE_KEYS,
        Effect.CUT_EGRESS,
        Effect.NOTIFY_OWNER,
    ),
}


def _escalate(m: Machine, target: KSState, reason: str) -> tuple[Machine, tuple[Effect, ...]]:
    if _RANK[target] <= _RANK[m.state]:
        return m, ()
    return replace(m, state=target, reason=reason), _ESCALATION_EFFECTS[target]


def _on_ring(m: Machine, ev: Ring, rules: Rules) -> tuple[Machine, tuple[Effect, ...]]:
    horizon = max(rules.pause_window_s, rules.stop_window_s)
    recent = (*tuple(r for r in m.rings if 0 <= ev.at - r.at <= horizon), ev)
    recent = recent[-rules.max_rings_tracked :]
    m = replace(m, rings=recent)

    in_stop_window = [r for r in recent if ev.at - r.at <= rules.stop_window_s]
    if len(in_stop_window) >= rules.rings_to_stop:
        return _escalate(m, KSState.STOPPED, "ring:stop")

    same_number = [
        r for r in recent if r.number == ev.number and ev.at - r.at <= rules.pause_window_s
    ]
    needed = rules.known_rings_to_pause if ev.known else rules.unknown_rings_to_pause
    if len(same_number) >= needed:
        return _escalate(m, KSState.PAUSED, "ring:known" if ev.known else "ring:unknown")
    return m, ()


def _on_resume(m: Machine, ev: ResumeRequest) -> tuple[Machine, tuple[Effect, ...]]:
    if m.state == KSState.ACTIVE:
        return m, ()
    if not ev.all_conditions:
        return m, (Effect.NOTIFY_OWNER,)
    return (
        replace(m, state=KSState.ACTIVE, rings=(), reason="manual-resume"),
        (Effect.RESUME_CHARLIE, Effect.NOTIFY_OWNER),
    )


def step(m: Machine, ev: Event, rules: Rules) -> tuple[Machine, tuple[Effect, ...]]:
    """Pure transition function."""
    match ev:
        case Ring():
            return _on_ring(m, ev, rules)
        case SmsCode(code_valid=True):
            return _escalate(m, KSState.STOPPED, "sms:code")
        case SmsCode():
            return m, ()
        case PhysicalSwitch():
            return _escalate(m, KSState(rules.physical_switch_action), "physical-switch")
        case AppButton(action="PAUSE"):
            return _escalate(m, KSState.PAUSED, "app:pause")
        case AppButton():
            return _escalate(m, KSState.STOPPED, "app:stop")
        case Anomaly():
            return _escalate(m, KSState(ev.action), f"anomaly:{ev.rule}")
        case DuressSignal():
            return m, (Effect.SILENT_ALARM,)
        case LolaSwitch(enabled=False):
            return replace(m, lola_enabled=False), (Effect.STOP_LOLA,)
        case LolaSwitch():
            if m.state == KSState.STOPPED:
                return m, ()
            return replace(m, lola_enabled=True), (Effect.START_LOLA,)
        case ResumeRequest():
            return _on_resume(m, ev)
        case _:
            assert_never(ev)


def charlie_may_run(m: Machine) -> bool:
    return m.state == KSState.ACTIVE


def lola_may_run(m: Machine) -> bool:
    return m.state != KSState.STOPPED and m.lola_enabled
