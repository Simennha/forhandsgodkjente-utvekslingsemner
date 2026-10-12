"""Redaction applied to everything that leaves a process: logs, audit entries, replies.

Two mechanisms:
1. Pattern-based: well-known secret formats (API keys, private keys, JWTs, auth headers).
2. Value-based: every secret loaded from a SecretStore is registered here and replaced
   wherever it appears verbatim.
Dict keys that name a credential (pin, code, token, ...) always have their values replaced.

PIN, duress code and test word are additionally never placed into any text flow at all:
they are consumed by assistants.identity and only a boolean outcome continues.
"""

from __future__ import annotations

import re
import threading
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{8,}"),
    re.compile(r"\bAC[0-9a-fA-F]{32}\b"),  # Twilio account SID
    re.compile(r"\bSK[0-9a-fA-F]{32}\b"),  # Twilio API key SID
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}"),
    re.compile(r"\bAGE-SECRET-KEY-1[0-9A-Z]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}"),
)

SENSITIVE_KEYS = frozenset(
    {
        "pin",
        "pin_code",
        "duress",
        "duress_code",
        "code",
        "test_word",
        "password",
        "passphrase",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth_token",
        "private_key",
        "hmac_key",
        "action_token",
        "x-twilio-signature",
        "cookie",
    }
)

_MIN_REGISTERED_LEN = 8


class _Registry:
    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str) -> None:
        if len(value) >= _MIN_REGISTERED_LEN:
            with self._lock:
                self._values.add(value)

    def snapshot(self) -> list[str]:
        with self._lock:
            # Longest first so that overlapping secrets are fully removed.
            return sorted(self._values, key=len, reverse=True)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


SECRET_VALUES = _Registry()


def register_secret(value: str) -> None:
    SECRET_VALUES.add(value)


def redact_text(text: str) -> str:
    out = text
    for value in SECRET_VALUES.snapshot():
        if value in out:
            out = out.replace(value, REDACTED)
    for pattern in _PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def redact(value: Any) -> Any:
    """Recursively redact a JSON-like value."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, inner in value.items():
            skey = str(key)
            if skey.lower() in SENSITIVE_KEYS:
                result[skey] = REDACTED
            else:
                result[skey] = redact(inner)
        return result
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def contains_secret(text: str) -> bool:
    return redact_text(text) != text
