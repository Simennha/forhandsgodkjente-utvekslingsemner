"""Short-lived, single-use action tokens.

A token is HMAC-SHA256 signed and binds: tool, SHA-256 of the canonical parameters,
proposal id, idempotency key, a random jti and an expiry (default 60 s). A tool handler
runs only if a token verifies for exactly that tool and exactly those parameters, has not
expired, and its jti has never been used before.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from assistants.common.canonical import canonical_json
from assistants.common.clock import Clock
from assistants.common.crypto import b64d, b64e
from assistants.common.errors import Denied
from assistants.storage.stores import TokenLedger


@dataclass(frozen=True)
class TokenClaims:
    jti: str
    tool: str
    params_hash: str
    proposal_id: str
    idem: str
    exp: float


class ActionTokens:
    def __init__(self, key: bytes, clock: Clock, ledger: TokenLedger, ttl_s: int = 60) -> None:
        if len(key) < 32:
            raise ValueError("action token key must be at least 32 bytes")
        self._key = key
        self._clock = clock
        self._ledger = ledger
        self._ttl = ttl_s

    def _sign(self, body: bytes) -> bytes:
        return hmac.new(self._key, body, hashlib.sha256).digest()

    def mint(self, tool: str, params_hash: str, proposal_id: str, idem: str) -> str:
        claims = {
            "jti": secrets.token_urlsafe(16),
            "tool": tool,
            "ph": params_hash,
            "pid": proposal_id,
            "idem": idem,
            "exp": self._clock.now().timestamp() + self._ttl,
        }
        body = canonical_json(claims).encode()
        return f"{b64e(body)}.{b64e(self._sign(body))}"

    def verify(self, token: str, tool: str, params_hash: str) -> TokenClaims:
        try:
            body_b64, sig_b64 = token.split(".", 1)
            body = b64d(body_b64)
            sig = b64d(sig_b64)
        except ValueError as exc:
            raise Denied("token_malformed") from exc
        if not hmac.compare_digest(sig, self._sign(body)):
            raise Denied("token_bad_signature")
        data = json.loads(body)
        claims = TokenClaims(
            jti=str(data["jti"]),
            tool=str(data["tool"]),
            params_hash=str(data["ph"]),
            proposal_id=str(data["pid"]),
            idem=str(data["idem"]),
            exp=float(data["exp"]),
        )
        if claims.tool != tool:
            raise Denied("token_wrong_tool")
        if not hmac.compare_digest(claims.params_hash, params_hash):
            raise Denied("token_wrong_params")
        if self._clock.now().timestamp() >= claims.exp:
            raise Denied("token_expired")
        if not self._ledger.use_once(claims.jti):
            raise Denied("token_replayed")
        return claims
