"""Shared plumbing for the HTTP services: secrets, service-to-service auth, HTTP clients."""

from __future__ import annotations

import hmac
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import Header, HTTPException
from psycopg_pool import ConnectionPool

from assistants.adapters.secrets import FileSecretStore, MemorySecretStore, SecretStore
from assistants.common.clock import Clock
from assistants.common.crypto import ed25519_public_from_str
from assistants.common.redact import redact_text
from assistants.killswitch.lease import LeaseVerifier

log = logging.getLogger("assistants")


class RedactingFilter(logging.Filter):
    """Applied to every handler: secrets never reach service logs (red team #18)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = tuple(redact_text(str(a)) for a in args)
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root.handlers:
        handler.addFilter(RedactingFilter())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "assistants"):
        logging.getLogger(name).addFilter(RedactingFilter())


FAKE_MARKER = "FAKE-REPLACE-ME"

URLS = {
    "gateway": os.environ.get("GATEWAY_URL", "http://gateway:8000"),
    "rules": os.environ.get("RULES_URL", "http://rules:8000"),
    "executor": os.environ.get("EXECUTOR_URL", "http://executor:8000"),
    "charlie": os.environ.get("CHARLIE_URL", "http://agents-charlie:8000"),
    "lola": os.environ.get("LOLA_URL", "http://agents-lola:8000"),
    "extractor": os.environ.get("EXTRACTOR_URL", "http://extractor:8000"),
    "approvals": os.environ.get("APPROVALS_URL", "http://approvals:8000"),
    "killswitch": os.environ.get("KILLSWITCH_URL", "http://killswitch-sim:8000"),
}


def load_secrets() -> SecretStore | None:
    path = os.environ.get("SECRETS_FILE")
    if not path or not Path(path).exists():
        return None
    return FileSecretStore(Path(path))


def is_fake(value: str) -> bool:
    return FAKE_MARKER in value


class ServiceAuth:
    """Bearer tokens per calling service. Each callee accepts only the callers it lists."""

    def __init__(self, store: SecretStore | None) -> None:
        self._tokens: dict[str, str] = {}
        if store is not None and isinstance(store, MemorySecretStore):
            for key in store.names():
                if key.startswith("accept."):
                    self._tokens[key.removeprefix("accept.")] = store.get(key)

    def caller(self, authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing token")
        presented = authorization.removeprefix("Bearer ").strip()
        for name, token in self._tokens.items():
            if hmac.compare_digest(presented, token):
                return name
        raise HTTPException(status_code=403, detail="unknown caller")

    def require(self, *allowed: str) -> Callable[..., str]:
        def dependency(authorization: str | None = Header(default=None)) -> str:
            name = self.caller(authorization)
            if name not in allowed:
                raise HTTPException(status_code=403, detail="caller not allowed here")
            return name

        return dependency


class ServiceClient:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {token}"}
        )

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(path, json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    def get(self, path: str) -> dict[str, Any]:
        resp = self._client.get(path)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data


def pool(dsn: str) -> ConnectionPool:
    return ConnectionPool(dsn, min_size=1, max_size=5, open=True, kwargs={"autocommit": True})


def lease_view(store: SecretStore, clock: Clock) -> LeaseVerifier:
    return LeaseVerifier(ed25519_public_from_str(store.get("lease.public_key")), clock)


def every(seconds: float, fn: Callable[[], object], name: str) -> threading.Thread:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(seconds):
            try:
                fn()
            except Exception:
                log.exception("background job %s failed", name)

    thread = threading.Thread(target=loop, name=name, daemon=True)
    thread.start()
    return thread
