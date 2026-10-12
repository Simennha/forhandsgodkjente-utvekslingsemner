"""Signed state leases from the kill-switch device to the server.

The device signs ``{state, lola, seq, issued_at, expires_at}`` with an Ed25519 key that
only exists on the device. Every service that can act (executor, agents, gateway) checks
the lease before each action and FAILS CLOSED: a missing, expired, malformed or
wrongly-signed lease counts as PAUSED. Unplugging the device or its network therefore
pauses Charlie; it never resumes him - when a valid ACTIVE lease returns, frozen
proposals stay frozen and must be proposed again.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from assistants.common.canonical import canonical_json
from assistants.common.clock import Clock
from assistants.killswitch.core import KSState


@dataclass(frozen=True)
class Lease:
    state: KSState
    lola_enabled: bool
    seq: int
    issued_at: float
    expires_at: float


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LeaseSigner:
    def __init__(self, key: Ed25519PrivateKey) -> None:
        self._key = key
        self._seq = 0

    def sign(self, state: KSState, lola_enabled: bool, now: datetime, ttl_s: int) -> str:
        self._seq += 1
        body = {
            "state": state.value,
            "lola": lola_enabled,
            "seq": self._seq,
            "iat": now.timestamp(),
            "exp": now.timestamp() + ttl_s,
        }
        raw = canonical_json(body).encode()
        return f"{_b64(raw)}.{_b64(self._key.sign(raw))}"


class LeaseVerifier:
    """Fail-closed view of the kill-switch state for one service."""

    def __init__(self, public_key: Ed25519PublicKey, clock: Clock) -> None:
        self._pub = public_key
        self._clock = clock
        self._last_seq = 0
        self._lease: Lease | None = None

    def accept(self, token: str) -> bool:
        try:
            body_b64, sig_b64 = token.split(".", 1)
            raw = _unb64(body_b64)
            self._pub.verify(_unb64(sig_b64), raw)
            data = json.loads(raw)
            lease = Lease(
                state=KSState(data["state"]),
                lola_enabled=bool(data["lola"]),
                seq=int(data["seq"]),
                issued_at=float(data["iat"]),
                expires_at=float(data["exp"]),
            )
        except (ValueError, KeyError, TypeError, InvalidSignature):
            return False
        if lease.seq <= self._last_seq:  # replay of an old (e.g. ACTIVE) lease
            return False
        self._last_seq = lease.seq
        self._lease = lease
        return True

    def state(self) -> KSState:
        lease = self._lease
        if lease is None or self._clock.now().timestamp() >= lease.expires_at:
            return (
                KSState.PAUSED if lease is None or lease.state != KSState.STOPPED else lease.state
            )
        return lease.state

    def charlie_may_act(self) -> bool:
        return self.state() == KSState.ACTIVE

    def lola_may_act(self) -> bool:
        lease = self._lease
        if lease is None or self._clock.now().timestamp() >= lease.expires_at:
            return False
        return lease.state != KSState.STOPPED and lease.lola_enabled
