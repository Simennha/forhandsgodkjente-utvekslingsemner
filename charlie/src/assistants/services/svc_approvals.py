"""approvals service: passkey (WebAuthn) PWA for level 3, approval box for large things.

Reachable only over WireGuard (published on 127.0.0.1). The page shows the code-rendered
confirmation text; the device signs the request; the attestation goes to the executor.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from assistants.adapters.secrets import SecretStore
from assistants.approvals.service import (
    ApprovalRequest,
    ApprovalService,
    DeviceVerifier,
    Ed25519DeviceVerifier,
)
from assistants.common.canonical import canonical_json
from assistants.common.clock import SystemClock
from assistants.common.crypto import (
    b64d,
    b64e,
    ed25519_private_from_str,
    ed25519_public_from_str,
)
from assistants.common.errors import Denied
from assistants.services.common import URLS, ServiceAuth, ServiceClient

PWA = (Path(__file__).resolve().parents[1] / "approvals" / "pwa.html").read_text(encoding="utf-8")


def build(store: SecretStore) -> FastAPI:
    from assistants.services.apps import base_app  # noqa: PLC0415

    clock = SystemClock()
    auth = ServiceAuth(store)
    key = ed25519_private_from_str(store.get("approvals.private_key"))
    verifiers: dict[str, DeviceVerifier] = {
        "passkey": Ed25519DeviceVerifier(
            ed25519_public_from_str(store.get("passkey.sim_public_key"))
        ),
        "approval_box": Ed25519DeviceVerifier(ed25519_public_from_str(store.get("box.public_key"))),
    }
    # A real phone passkey (WebAuthn) replaces the simulated one once registered (HANDOFF.md).
    service = ApprovalService(key, verifiers, clock)
    executor = ServiceClient(URLS["executor"], store.get("self.token"))
    login_nonces: dict[str, float] = {}

    def owner_session_ok(token: str) -> bool:
        try:
            body_b64, sig_b64 = token.split(".", 1)
            body = b64d(body_b64)
            key.public_key().verify(b64d(sig_b64), body)
            data = json.loads(body)
        except (ValueError, InvalidSignature):
            return False
        return data.get("typ") == "owner_session" and float(data.get("exp", 0)) > time.time()

    app = base_app("approvals")

    @app.get("/", response_class=HTMLResponse)
    def pwa() -> str:
        return PWA

    @app.post("/requests")
    def new_request(
        body: dict[str, Any], _c: str = Depends(auth.require("executor"))
    ) -> dict[str, str]:
        service.request(
            ApprovalRequest(
                proposal_id=str(body["proposal_id"]),
                params_hash=str(body["params_hash"]),
                challenge=str(body["challenge"]),
                method=body["method"],
                text=str(body["text"]),
                expires_at=datetime.fromisoformat(body["expires_at"]),
            )
        )
        return {"ok": "yes"}

    @app.get("/requests/{proposal_id}")
    def show(proposal_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not owner_session_ok((authorization or "").removeprefix("Bearer ").strip()):
            raise HTTPException(status_code=401, detail="owner session required")
        req = service.pending(proposal_id)
        if req is None:
            raise HTTPException(status_code=404, detail="ingen ventende godkjenning")
        import hashlib  # noqa: PLC0415

        from assistants.approvals.service import approval_message  # noqa: PLC0415

        msg = approval_message(
            req.proposal_id, req.params_hash, req.challenge, req.method, req.text
        )
        return {
            "proposal_id": req.proposal_id,
            "params_hash": req.params_hash,
            "challenge": req.challenge,
            "method": req.method,
            "text": req.text,
            "expires_at": req.expires_at.isoformat(),
            "rp_id": "charlie.local",
            "webauthn_challenge": b64e(hashlib.sha256(msg).digest()),
        }

    @app.post("/requests/{proposal_id}/approve")
    def approve(proposal_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            evidence = base64.b64decode(str(body["evidence_b64"]))
            token = service.approve(proposal_id, body["method"], evidence)
        except (Denied, ValueError, KeyError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return executor.post("/approve", {"attestation": token})

    # Owner login (passkey) -> short owner session for the PWA chat and mode switch.
    @app.post("/owner/login/nonce")
    def login_nonce() -> dict[str, str]:
        now = time.time()
        for old in [n for n, exp in login_nonces.items() if exp < now]:
            login_nonces.pop(old, None)
        if len(login_nonces) > 100:  # bounded (red team #15)
            raise HTTPException(status_code=429, detail="too many pending logins")
        nonce = secrets.token_urlsafe(16)
        login_nonces[nonce] = now + 120
        return {"nonce": nonce}

    @app.post("/owner/login")
    def login(body: dict[str, Any]) -> dict[str, str]:
        nonce = str(body.get("nonce", ""))
        if login_nonces.pop(nonce, 0) < time.time():
            raise HTTPException(status_code=403, detail="nonce")
        try:
            ed25519_public_from_str(store.get("passkey.sim_public_key")).verify(
                base64.b64decode(str(body["signature_b64"])), f"login|{nonce}".encode()
            )
        except (InvalidSignature, ValueError, KeyError) as exc:
            raise HTTPException(status_code=403, detail="bad signature") from exc
        claims = canonical_json(
            {"typ": "owner_session", "sub": "owner", "exp": time.time() + 900}
        ).encode()
        return {"session": f"{b64e(claims)}.{b64e(key.sign(claims))}"}

    return app
