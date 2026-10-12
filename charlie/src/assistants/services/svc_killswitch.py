"""killswitch-sim: the independent device, simulated. In production this code runs on the
Raspberry Pi Zero 2 W with the SIM HAT (docs/killswitch.md); only the HAL differs."""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI

from assistants.adapters.notifier import SimNotifier
from assistants.adapters.revocation import SimEgress, SimRevoker
from assistants.adapters.secrets import SecretStore
from assistants.common.clock import SystemClock
from assistants.common.config import load_config
from assistants.common.crypto import ed25519_private_from_str, ed25519_public_from_str
from assistants.killswitch.driver import (
    DeviceSecrets,
    FileDeviceState,
    KillSwitchDevice,
    ResumeAttempt,
    SimHAL,
    rules_from_config,
)
from assistants.killswitch.lease import LeaseSigner
from assistants.services.common import URLS, ServiceAuth, ServiceClient, every, log


class _Keys:
    def __init__(self) -> None:
        self.active = {"anthropic:charlie-1", "anthropic:lola-1", "twilio:1", "google:1", "ha:1"}

    def active_key_fingerprints(self) -> frozenset[str]:
        return frozenset(self.active)


class _AuditHead:
    def __init__(self, client: ServiceClient) -> None:
        self._c = client

    def head_hash(self) -> str:
        try:
            return str(self._c.get("/internal/audit-head")["head"])
        except Exception:  # noqa: BLE001 - unknown head => resume impossible (fail closed)
            return "unavailable"


def build(store: SecretStore) -> FastAPI:
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    clock = SystemClock()
    auth = ServiceAuth(store)
    me = store.get("self.token")
    targets = {
        name: ServiceClient(URLS[name], me, timeout=5)
        for name in ("gateway", "executor", "charlie", "lola")
    }

    def publish(token: str) -> None:
        for name, client in targets.items():
            try:
                client.post("/internal/lease", {"token": token})
            except Exception:  # noqa: BLE001 - a missing target simply stays fail-closed
                log.debug("lease to %s failed", name)

    pin = json.loads(store.get("pin"))
    sms = json.loads(store.get("sms_code"))
    hal = SimHAL()
    keys = _Keys()
    revokers = [
        SimRevoker("anthropic"),
        SimRevoker("twilio"),
        SimRevoker("google"),
        SimRevoker("homeassistant"),
    ]
    egress = SimEgress()
    device = KillSwitchDevice(
        hal=hal,
        secrets=DeviceSecrets(
            pin_hash=bytes.fromhex(pin["hash"]),
            pin_salt=bytes.fromhex(pin["salt"]),
            sms_code_hash=bytes.fromhex(sms["hash"]),
            sms_code_salt=bytes.fromhex(sms["salt"]),
            second_device_key=ed25519_public_from_str(store.get("second_device.public_key")),
        ),
        rules=rules_from_config(cfg.killswitch),
        clock=clock,
        signer=LeaseSigner(ed25519_private_from_str(store.get("lease.private_key"))),
        lease_ttl_s=cfg.killswitch.lease_ttl_s,
        owner_numbers=frozenset(cfg.identity.owner_numbers),
        second_alarm_number=cfg.identity.second_alarm_number,
        known_numbers=frozenset(cfg.identity.known_contacts),
        revokers=revokers,
        egress=egress,
        notifier=SimNotifier(),
        keys=keys,
        audit=_AuditHead(targets["executor"]),
        publish_lease=publish,
        state_store=FileDeviceState(
            Path(os.environ.get("KS_STATE_FILE", "/state/killswitch.json"))
        ),
    )
    device.boot(allow_first_boot=os.environ.get("KS_ALLOW_FIRST_BOOT") == "1")
    every(5, device.poll, "ks-poll")
    app = base_app("killswitch-sim")

    def view() -> dict[str, Any]:
        m = device.machine
        return {
            "state": m.state.value,
            "reason": m.reason,
            "lola_enabled": m.lola_enabled,
            "revoked": [r.provider for r in revokers if r.revoked],
            "egress_cut": egress.cut,
            "sms_sent": hal.sms_sent[-5:],
        }

    @app.get("/state")
    def state(_c: str = Depends(auth.require("sim"))) -> dict[str, Any]:
        return view()

    @app.post("/sim/{event}")
    def sim_event(
        event: str, body: dict[str, Any], _c: str = Depends(auth.require("sim"))
    ) -> dict[str, Any]:
        if event == "ring":
            hal.modem_queue.append(("ring", str(body["number"]), ""))
        elif event == "sms":
            hal.modem_queue.append(("sms", str(body["number"]), str(body["body"])))
        elif event == "switch":
            hal.switch = bool(body["engaged"])
        elif event == "app":
            device.app_button(str(body["action"]))
        elif event == "lola":
            device.lola(bool(body["enabled"]))
        elif event == "owner-replaced-keys":
            keys.active = {f"{k.split(':')[0]}:rotated-{secrets.token_hex(3)}" for k in keys.active}
        device.poll()
        return view()

    @app.post("/duress")
    def duress(_c: str = Depends(auth.require("gateway"))) -> dict[str, str]:
        device.duress()
        return {"ok": "yes"}

    @app.post("/anomaly")
    def anomaly(body: dict[str, Any], _c: str = Depends(auth.require("worker"))) -> dict[str, Any]:
        device.anomaly(str(body["rule"])[:60], str(body["action"]))
        return view()

    @app.post("/anchor")
    def anchor(body: dict[str, Any], _c: str = Depends(auth.require("worker"))) -> dict[str, bool]:
        return {"stored": device.anchor(int(body["seq"]), str(body["hash"])[:64])}

    @app.get("/anchors")
    def anchors(_c: str = Depends(auth.require("sim"))) -> dict[str, Any]:
        return {"anchors": device.anchors[-200:]}

    @app.post("/resume/nonce")
    def nonce(_c: str = Depends(auth.require("sim"))) -> dict[str, str]:
        return {"nonce": device.new_resume_nonce(secrets.token_urlsafe(16))}

    @app.post("/resume")
    def resume(body: dict[str, Any], _c: str = Depends(auth.require("sim"))) -> dict[str, Any]:
        attempt = ResumeAttempt(
            str(body["pin"]),
            str(body["nonce"]),
            str(body["audit_head"]),
            base64.b64decode(str(body["signature_b64"])),
        )
        device.resume(attempt)
        return view()

    return app
