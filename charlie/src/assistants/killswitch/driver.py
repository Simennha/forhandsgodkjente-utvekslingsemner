"""Driver for the independent kill-switch device (Raspberry Pi Zero 2 W + SIM HAT).

The device has its own battery, its own power and its own (cellular) network. It runs this
driver, feeds hardware events into the pure state machine (core.py) and performs effects:

* publishes signed leases to the server (fail-closed: no lease == PAUSED),
* revokes the Claude API key and tool keys at the providers (STOPPED),
* asks the router to cut the server VLAN's internet access (STOPPED),
* sends SMS/push to the owner and to the second alarm number (duress: silent alarm).

The PIN hash, the SMS stop-code hash, the lease signing key, the provider admin
credentials and the second-device public key live ONLY on this device.
Hardware access goes through ``KillSwitchHAL``; ``SimHAL`` is the simulator.
"""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from assistants.adapters.notifier import Notifier
from assistants.adapters.revocation import EgressController, KeyRevoker, RevocationResult
from assistants.common.clock import Clock
from assistants.common.config import KillSwitchConfig
from assistants.common.crypto import credential_matches
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
from assistants.killswitch.lease import LeaseSigner


def rules_from_config(cfg: KillSwitchConfig) -> Rules:
    return Rules(
        pause_window_s=cfg.pause_window_s,
        unknown_rings_to_pause=cfg.unknown_rings_to_pause,
        known_rings_to_pause=cfg.known_rings_to_pause,
        stop_window_s=cfg.stop_window_s,
        rings_to_stop=cfg.rings_to_stop,
        physical_switch_action=cfg.physical_switch_action,
        max_rings_tracked=cfg.max_rings_tracked,
    )


# ---------------------------------------------------------------- hardware abstraction


class KillSwitchHAL(Protocol):
    def switch_engaged(self) -> bool:
        """True while the physical switch at the entrance is in the STOP position."""
        ...

    def poll_modem(self) -> list[tuple[str, str, str]]:
        """Pending modem events: ("ring"|"sms", from_number, sms_body)."""
        ...

    def send_sms(self, to_number: str, body: str) -> bool: ...
    def set_led(self, state: KSState) -> None: ...


@dataclass
class SimHAL:
    switch: bool = False
    modem_queue: list[tuple[str, str, str]] = field(default_factory=list)
    sms_sent: list[tuple[str, str]] = field(default_factory=list)
    led: KSState = KSState.ACTIVE

    def switch_engaged(self) -> bool:
        return self.switch

    def poll_modem(self) -> list[tuple[str, str, str]]:
        events, self.modem_queue = self.modem_queue, []
        return events

    def send_sms(self, to_number: str, body: str) -> bool:
        self.sms_sent.append((to_number, body))
        return True

    def set_led(self, state: KSState) -> None:
        self.led = state


# ---------------------------------------------------------------- secrets on the device


@dataclass(frozen=True)
class DeviceSecrets:
    pin_hash: bytes
    pin_salt: bytes
    sms_code_hash: bytes
    sms_code_salt: bytes
    second_device_key: Ed25519PublicKey

    def pin_ok(self, pin: str) -> bool:
        return credential_matches(pin, self.pin_salt, self.pin_hash)

    def sms_code_ok(self, body: str) -> bool:
        return credential_matches(body, self.sms_code_salt, self.sms_code_hash)


class KeyInventory(Protocol):
    def active_key_fingerprints(self) -> frozenset[str]:
        """Fingerprints (e.g. key IDs) of credentials currently active at the providers."""
        ...


class AuditHead(Protocol):
    def head_hash(self) -> str: ...


@dataclass(frozen=True)
class ResumeAttempt:
    """What the owner submits on the device's local resume page (over VPN)."""

    pin: str
    nonce: str
    reviewed_audit_head: str
    second_device_signature: bytes


def resume_message(nonce: str, reviewed_audit_head: str) -> bytes:
    return f"resume|{nonce}|{reviewed_audit_head}".encode()


# ---------------------------------------------------------------- durable state


class DeviceStateStore(Protocol):
    def load(self) -> dict[str, object] | None:
        """Saved state, None if never saved. Raises ValueError if corrupt."""
        ...

    def save(self, data: dict[str, object]) -> None: ...


@dataclass
class MemoryDeviceState:
    data: dict[str, object] | None = None
    corrupt: bool = False

    def load(self) -> dict[str, object] | None:
        if self.corrupt:
            raise ValueError("corrupt state")
        return None if self.data is None else dict(self.data)

    def save(self, data: dict[str, object]) -> None:
        self.data = dict(data)
        self.corrupt = False


class FileDeviceState:
    """Atomic JSON file (write temp, fsync, rename) on the device's own storage."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, object] | None:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("corrupt state")
        return data

    def save(self, data: dict[str, object]) -> None:
        tmp = self._path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)


MAX_ANCHORS = 2000


def _encode(
    m: Machine, seq: int, incident: frozenset[str], anchors: list[tuple[int, str]]
) -> dict[str, object]:
    return {
        "state": m.state.value,
        "lola_enabled": m.lola_enabled,
        "reason": m.reason,
        "rings": [[r.number, r.known, r.at] for r in m.rings],
        "seq": seq,
        "incident": sorted(incident),
        "anchors": [[s, h] for s, h in anchors[-MAX_ANCHORS:]],
    }


def _decode(
    d: dict[str, object],
) -> tuple[Machine, int, frozenset[str], list[tuple[int, str]]]:
    rings_raw = d["rings"]
    incident_raw = d["incident"]
    anchors_raw = d.get("anchors", [])
    if not isinstance(anchors_raw, list):
        raise ValueError("corrupt state")
    if not isinstance(rings_raw, list) or not isinstance(incident_raw, list):
        raise ValueError("corrupt state")
    machine = Machine(
        state=KSState(str(d["state"])),
        lola_enabled=bool(d["lola_enabled"]),
        rings=tuple(Ring(str(n), bool(k), float(a)) for n, k, a in rings_raw),
        reason=str(d["reason"]),
    )
    anchors = [(int(s), str(h)) for s, h in anchors_raw]
    return machine, int(str(d["seq"])), frozenset(str(x) for x in incident_raw), anchors


# ---------------------------------------------------------------- device


@dataclass
class KillSwitchDevice:
    hal: KillSwitchHAL
    secrets: DeviceSecrets
    rules: Rules
    clock: Clock
    signer: LeaseSigner
    lease_ttl_s: int
    owner_numbers: frozenset[str]
    second_alarm_number: str
    known_numbers: frozenset[str]
    revokers: Sequence[KeyRevoker]
    egress: EgressController
    notifier: Notifier
    keys: KeyInventory
    audit: AuditHead
    publish_lease: Callable[[str], None]
    machine: Machine = field(default_factory=Machine)
    incident_fingerprints: frozenset[str] = frozenset()
    revocations: list[RevocationResult] = field(default_factory=list)
    _switch_was_engaged: bool = False
    _issued_nonces: set[str] = field(default_factory=set)
    log: list[str] = field(default_factory=list)
    state_store: DeviceStateStore | None = None
    anchors: list[tuple[int, str]] = field(default_factory=list)

    def anchor(self, seq: int, head: str) -> bool:
        """Keep the audit log's head hash off the server. Only ever grows."""
        if self.anchors and seq <= self.anchors[-1][0]:
            return False
        self.anchors.append((seq, head))
        self.anchors = self.anchors[-MAX_ANCHORS:]
        self._persist()
        return True

    # -- boot ----------------------------------------------------------------
    def boot(self, allow_first_boot: bool = False) -> KSState:
        """Restore the saved state. Never comes back ACTIVE by itself after a STOP:
        a missing file is only accepted on a provisioned first boot, and a corrupt file
        boots into STOPPED."""
        if self.state_store is None:
            return self.machine.state
        try:
            saved = self.state_store.load()
            if saved is None:
                if not allow_first_boot:
                    raise ValueError("no saved state and not a first boot")
                self._persist()
            else:
                self.machine, seq, self.incident_fingerprints, self.anchors = _decode(saved)
                self.signer.resume_from(seq)
        except (ValueError, KeyError, TypeError):
            self.machine = Machine(state=KSState.STOPPED, reason="boot:state-unreadable")
            self._persist()
        self.hal.set_led(self.machine.state)
        self.publish()
        return self.machine.state

    def _persist(self) -> None:
        if self.state_store is not None:
            self.state_store.save(
                _encode(self.machine, self.signer.seq, self.incident_fingerprints, self.anchors)
            )

    # -- event intake ------------------------------------------------------
    def feed(self, event: Event) -> tuple[Effect, ...]:
        self.machine, effects = step(self.machine, event, self.rules)
        self.log.append(
            f"{self.clock.now().isoformat()} {type(event).__name__} -> "
            f"{self.machine.state} {[e.value for e in effects]}"
        )
        for effect in effects:
            self._perform(effect)
        self._persist()  # durable BEFORE the new lease goes out
        self.hal.set_led(self.machine.state)
        self.publish()
        return effects

    def poll(self) -> None:
        """One iteration of the device main loop (called every ~200 ms)."""
        now = self.clock.now().timestamp()
        engaged = self.hal.switch_engaged()
        if engaged and not self._switch_was_engaged:
            self.feed(PhysicalSwitch(at=now))
        self._switch_was_engaged = engaged
        for kind, number, body in self.hal.poll_modem():
            known = number in self.known_numbers or number in self.owner_numbers
            if kind == "ring":
                self.feed(Ring(number=number, known=known, at=now))
            elif kind == "sms":
                self.feed(SmsCode(number=number, code_valid=self.secrets.sms_code_ok(body), at=now))
        self.publish()

    def app_button(self, action: str) -> None:
        if action in ("PAUSE", "STOP"):
            self.feed(
                AppButton(
                    action="PAUSE" if action == "PAUSE" else "STOP", at=self.clock.now().timestamp()
                )
            )

    def anomaly(self, rule: str, action: str) -> None:
        self.feed(
            Anomaly(
                rule=rule,
                action="STOPPED" if action == "STOPPED" else "PAUSED",
                at=self.clock.now().timestamp(),
            )
        )

    def duress(self) -> None:
        self.feed(DuressSignal(at=self.clock.now().timestamp()))

    def lola(self, enabled: bool) -> None:
        self.feed(LolaSwitch(enabled=enabled, at=self.clock.now().timestamp()))

    # -- resume ----------------------------------------------------------
    def new_resume_nonce(self, nonce: str) -> str:
        self._issued_nonces.add(nonce)
        return nonce

    def resume(self, attempt: ResumeAttempt) -> KSState:
        """Verify every condition HERE, in code; the state machine only sees booleans."""
        pin_ok = self.secrets.pin_ok(attempt.pin)
        nonce_ok = attempt.nonce in self._issued_nonces
        self._issued_nonces.discard(attempt.nonce)  # single use, even on failure
        try:
            self.secrets.second_device_key.verify(
                attempt.second_device_signature,
                resume_message(attempt.nonce, attempt.reviewed_audit_head),
            )
            second_ok = nonce_ok
        except InvalidSignature:
            second_ok = False
        log_ok = hmac.compare_digest(attempt.reviewed_audit_head, self.audit.head_hash())
        # Keys count as rotated when every credential active now differs from every
        # credential that was active when the incident started (PAUSED or STOPPED).
        active = self.keys.active_key_fingerprints()
        keys_ok = bool(active) and not (active & self.incident_fingerprints)
        self.feed(
            ResumeRequest(
                pin_ok=pin_ok,
                second_device_confirmed=second_ok,
                log_reviewed=log_ok,
                keys_rotated=keys_ok,
                at=self.clock.now().timestamp(),
            )
        )
        return self.machine.state

    # -- effects -----------------------------------------------------------
    def _perform(self, effect: Effect) -> None:
        if effect == Effect.REVOKE_KEYS:
            self.incident_fingerprints |= self.keys.active_key_fingerprints()
            for revoker in self.revokers:
                self.revocations.append(revoker.revoke())
        elif effect == Effect.CUT_EGRESS:
            self.egress.cut_server_egress()
        elif effect == Effect.STOP_CHARLIE:
            # Stopping itself is enforced by the server through the lease (fail-closed).
            self.incident_fingerprints |= self.keys.active_key_fingerprints()
        elif effect == Effect.RESUME_CHARLIE:
            self.incident_fingerprints = frozenset()
        elif effect == Effect.NOTIFY_OWNER:
            msg = f"Kill switch: {self.machine.state} ({self.machine.reason})"
            for number in sorted(self.owner_numbers):
                self.hal.send_sms(number, msg)
            self.notifier.push("Kill switch", msg, priority="high")
        elif effect == Effect.SILENT_ALARM:
            # Independent channel: the device's own modem and push. Nothing is shown on the
            # server, in Charlie's replies or in the server-side audit log.
            self.hal.send_sms(
                self.second_alarm_number, "STILLE ALARM: tvangskode brukt mot Charlie."
            )
            self.notifier.push("Stille alarm", "Tvangskode brukt", priority="urgent")
        # FREEZE_PROPOSALS, START_LOLA and STOP_LOLA are carried by the lease.

    def publish(self) -> None:
        token = self.signer.sign(
            self.machine.state, self.machine.lola_enabled, self.clock.now(), self.lease_ttl_s
        )
        self.publish_lease(token)
