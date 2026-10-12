"""Service-to-service auth and the gateway-signed call context."""

from __future__ import annotations

import secrets

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from assistants.adapters.secrets import MemorySecretStore
from assistants.common.clock import FakeClock
from assistants.common.crypto import b64d, b64e
from assistants.common.ctxsign import peek_ctx, sign_ctx, verify_ctx
from assistants.common.errors import Denied
from assistants.events import Mode, Trust
from assistants.executor.core import CallContext
from assistants.services.common import ServiceAuth


def test_ctx_roundtrip_and_tamper() -> None:
    key, clock = secrets.token_bytes(32), FakeClock()
    ctx = CallContext(Trust.OWNER_KNOWN, Mode.NORMAL, "phone", "s1")
    token = sign_ctx(ctx, key, clock)
    assert verify_ctx(token, key, clock) == ctx
    assert peek_ctx(token) == ctx
    body, sig = token.split(".")
    forged_body = b64e(b64d(body).replace(b"owner_known", b"owner_pin"))
    with pytest.raises(Denied):
        verify_ctx(f"{forged_body}.{sig}", key, clock)
    with pytest.raises(Denied):
        verify_ctx(token, secrets.token_bytes(32), clock)
    clock.advance(301)
    with pytest.raises(Denied):
        verify_ctx(token, key, clock)


def test_service_auth_only_listed_callers() -> None:
    store = MemorySecretStore(
        {"accept.charlie": "tok-FAKE-charlie-123456", "accept.worker": "tok-FAKE-worker-123456"}
    )
    auth = ServiceAuth(store)
    app = FastAPI()

    @app.post("/x")
    def x(caller: str = Depends(auth.require("charlie"))) -> dict[str, str]:
        return {"caller": caller}

    client = TestClient(app)
    assert client.post("/x").status_code == 401
    assert client.post("/x", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert (
        client.post("/x", headers={"Authorization": "Bearer tok-FAKE-worker-123456"}).status_code
        == 403
    )
    ok = client.post("/x", headers={"Authorization": "Bearer tok-FAKE-charlie-123456"})
    assert ok.json() == {"caller": "charlie"}
