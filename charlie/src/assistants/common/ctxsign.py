"""Signed call context: trust is decided by the gateway and cannot be raised downstream.

The gateway signs {trust, mode, channel, session_id, exp} with a key shared only with the
executor. agents-charlie passes the token through unchanged; the executor verifies it. A
compromised agent container therefore cannot claim PIN-level trust it never had.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from assistants.common.canonical import canonical_json
from assistants.common.clock import Clock
from assistants.common.crypto import b64d, b64e
from assistants.common.errors import Denied
from assistants.events import Mode, Trust
from assistants.executor.core import CallContext

TTL_S = 120  # one conversational turn; reuse is also capped by the executor


def sign_ctx(ctx: CallContext, key: bytes, clock: Clock) -> str:
    body = canonical_json(
        {
            "trust": ctx.trust.value,
            "mode": ctx.mode.value,
            "channel": ctx.channel,
            "sid": ctx.session_id,
            "exp": clock.now().timestamp() + TTL_S,
        }
    ).encode()
    return f"{b64e(body)}.{b64e(hmac.new(key, body, hashlib.sha256).digest())}"


def verify_ctx(token: str, key: bytes, clock: Clock) -> CallContext:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = b64d(body_b64)
        if not hmac.compare_digest(b64d(sig_b64), hmac.new(key, body, hashlib.sha256).digest()):
            raise Denied("ctx_bad_signature")
        data = json.loads(body)
        if clock.now().timestamp() >= float(data["exp"]):
            raise Denied("ctx_expired")
        return CallContext(
            Trust(data["trust"]), Mode(data["mode"]), str(data["channel"]), data["sid"]
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise Denied("ctx_malformed") from exc


def peek_ctx(token: str) -> CallContext:
    """Read WITHOUT verifying (agents-charlie only uses it to phrase replies)."""
    data = json.loads(b64d(token.split(".", 1)[0]))
    return CallContext(Trust(data["trust"]), Mode(data["mode"]), str(data["channel"]), data["sid"])
