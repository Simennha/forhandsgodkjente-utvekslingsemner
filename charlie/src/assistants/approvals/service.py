"""Approval verification and attestations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from assistants.common.canonical import canonical_json, sha256_hex
from assistants.common.clock import Clock
from assistants.common.crypto import b64d, b64e
from assistants.common.errors import Denied

Method = Literal["passkey", "approval_box"]


def approval_message(
    proposal_id: str, params_hash: str, challenge: str, method: str, text: str
) -> bytes:
    """What a device signs. The text hash is included so the box cannot show one thing and
    approve another."""
    return f"approve|{proposal_id}|{params_hash}|{challenge}|{method}|{sha256_hex(text)}".encode()


@dataclass(frozen=True)
class ApprovalRequest:
    proposal_id: str
    params_hash: str
    challenge: str
    method: Method
    text: str
    expires_at: datetime


@dataclass(frozen=True)
class Attestation:
    proposal_id: str
    params_hash: str
    challenge: str
    method: Method
    approved_at: float


class DeviceVerifier(Protocol):
    def verify(self, request: ApprovalRequest, evidence: bytes) -> bool: ...


class Ed25519DeviceVerifier:
    """Simulated phone passkey and the approval box both sign with a registered Ed25519 key.

    The real approval box (docs/architecture.md) does exactly this with a key in its secure
    element. The real phone uses WebAuthn: see ``WebAuthnVerifier``.
    """

    def __init__(self, public_key: Ed25519PublicKey) -> None:
        self._pub = public_key

    def verify(self, request: ApprovalRequest, evidence: bytes) -> bool:
        msg = approval_message(
            request.proposal_id,
            request.params_hash,
            request.challenge,
            request.method,
            request.text,
        )
        try:
            self._pub.verify(evidence, msg)
        except InvalidSignature:
            return False
        return True


class WebAuthnVerifier:
    """Real passkey verification with py_webauthn. NOT verified against a real phone here.

    The WebAuthn challenge is SHA-256 of ``approval_message(...)``, so the passkey assertion
    is bound to the exact proposal. ``evidence`` is the JSON authentication response from the
    PWA (approvals/pwa.html).
    """

    def __init__(self, rp_id: str, origin: str, credential_public_key: bytes, sign_count: int = 0):
        self._rp_id = rp_id
        self._origin = origin
        self._cred_pub = credential_public_key
        self._sign_count = sign_count

    def challenge_for(self, request: ApprovalRequest) -> bytes:
        import hashlib  # noqa: PLC0415

        return hashlib.sha256(
            approval_message(
                request.proposal_id,
                request.params_hash,
                request.challenge,
                request.method,
                request.text,
            )
        ).digest()

    def verify(self, request: ApprovalRequest, evidence: bytes) -> bool:
        from webauthn import verify_authentication_response  # noqa: PLC0415

        try:
            result = verify_authentication_response(
                credential=evidence.decode(),
                expected_challenge=self.challenge_for(request),
                expected_rp_id=self._rp_id,
                expected_origin=self._origin,
                credential_public_key=self._cred_pub,
                credential_current_sign_count=self._sign_count,
                require_user_verification=True,
            )
        except Exception:  # noqa: BLE001 - any failure is a rejection
            return False
        self._sign_count = int(result.new_sign_count)
        return True


class ApprovalService:
    def __init__(
        self,
        signing_key: Ed25519PrivateKey,
        verifiers: dict[str, DeviceVerifier],
        clock: Clock,
    ) -> None:
        self._key = signing_key
        self._verifiers = verifiers
        self._clock = clock
        self._pending: dict[str, ApprovalRequest] = {}

    def request(self, req: ApprovalRequest) -> None:
        self._pending[req.proposal_id] = req

    def pending(self, proposal_id: str) -> ApprovalRequest | None:
        return self._pending.get(proposal_id)

    def approve(self, proposal_id: str, method: Method, evidence: bytes) -> str:
        req = self._pending.get(proposal_id)
        if req is None:
            raise Denied("no_pending_request")
        if req.method != method:
            raise Denied("wrong_method")
        if self._clock.now() >= req.expires_at:
            self._pending.pop(proposal_id, None)
            raise Denied("request_expired")
        verifier = self._verifiers.get(method)
        if verifier is None or not verifier.verify(req, evidence):
            raise Denied("bad_device_signature")
        self._pending.pop(proposal_id, None)  # single use
        att = {
            "typ": "approval",  # never confusable with an owner session (red team #15)
            "pid": req.proposal_id,
            "ph": req.params_hash,
            "ch": req.challenge,
            "m": req.method,
            "at": self._clock.now().timestamp(),
        }
        body = canonical_json(att).encode()
        return f"{b64e(body)}.{b64e(self._key.sign(body))}"


class AttestationVerifier:
    def __init__(self, public_key: Ed25519PublicKey, clock: Clock, max_age_s: int = 600) -> None:
        self._pub = public_key
        self._clock = clock
        self._max_age = timedelta(seconds=max_age_s)

    def verify(self, token: str) -> Attestation:
        try:
            body_b64, sig_b64 = token.split(".", 1)
            body = b64d(body_b64)
            self._pub.verify(b64d(sig_b64), body)
            data = json.loads(body)
            if data.get("typ") != "approval":
                raise ValueError("wrong token type")
            att = Attestation(
                str(data["pid"]), str(data["ph"]), str(data["ch"]), data["m"], float(data["at"])
            )
        except (ValueError, KeyError, TypeError, InvalidSignature) as exc:
            raise Denied("bad_attestation") from exc
        if self._clock.now().timestamp() - att.approved_at > self._max_age.total_seconds():
            raise Denied("attestation_too_old")
        return att
