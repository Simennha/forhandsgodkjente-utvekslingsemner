"""End-to-end demo against the running Docker Compose stack.

Plays the owner's phone, the passkey device, the second device and outside callers -
all SIMULATED (keys from secrets/sim-devices.json). Every service, network boundary,
database role and signature check in between is the real code.

Steps: health + isolation -> call in -> level 1 -> PIN -> level 3 proposal -> passkey
approval -> execution -> audit chain -> Lola -> kill switch pause + manual restart.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assistants.adapters.telephony import twilio_signature  # noqa: E402
from assistants.approvals.service import approval_message  # noqa: E402
from assistants.common.crypto import ed25519_private_from_str  # noqa: E402
from assistants.killswitch.driver import resume_message  # noqa: E402

OWNER = "+4700000001"


class Demo:
    def __init__(self, base: str, approvals: str, killswitch: str) -> None:
        self.base = base
        self.approvals = approvals
        self.ks = killswitch
        self.sim = json.loads((ROOT / "secrets" / "sim-devices.json").read_text())
        creds = (ROOT / "secrets" / "dev-credentials.txt").read_text().splitlines()
        self.creds = dict(line.split("=", 1) for line in creds if "=" in line)
        self.http = httpx.Client(timeout=90)
        self.ok = True
        self.session = ""

    def step(self, title: str) -> None:
        print(f"\n== {title}")

    def check(self, cond: bool, label: str) -> None:
        print(f"  [{'OK' if cond else 'FEIL'}] {label}")
        self.ok &= cond

    def signed(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        sig = twilio_signature(self.sim["twilio.sim_signing_key"], self.base + path, params)
        r = self.http.post(self.base + path, json=params, headers={"X-Twilio-Signature": sig})
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    def voice(self, line: str, call: str, event: str, **params: str) -> str:
        reply = self.signed(f"/sim/voice/{line}/{event}", {"call_id": call, **params})
        text = str(reply.get("say", ""))
        print(f"    {event:7} -> {text}")
        return text

    def ks_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self.http.post(
            self.ks + path,
            json=body,
            headers={"Authorization": f"Bearer {self.sim['killswitch.sim_token']}"},
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    def psql(self, sql: str) -> str:
        return subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                "assistants",
                "-tAc",
                sql,
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=ROOT,
        ).stdout.strip()

    # ------------------------------------------------------------ scenario
    def isolation(self) -> None:
        self.step("1) Helse og nettsegmentering")
        self.check(self.http.get(self.base + "/health").json()["status"] == "ok", "gateway frisk")
        for target in (
            "executor:8000",
            "agents-charlie:8000",
            "extractor:8000",
            "rules:8000",
            "worker:8000",
            "db:5432",
        ):
            host, port = target.split(":")
            code = (
                f"import socket,sys\ntry:\n socket.create_connection(('{host}',{port}),timeout=2)"
                "; sys.exit(1)\nexcept OSError: sys.exit(0)\n"
            )
            res = subprocess.run(
                ["docker", "compose", "exec", "-T", "agents-lola", "python", "-c", code],
                check=False,
                cwd=ROOT,
            )
            self.check(res.returncode == 0, f"Lola når ikke {target}")

    def owner_mode(self) -> None:
        self.step("1b) Eier logger inn med passkey (simulert) og setter Vanlig modus")
        nonce = self.http.post(f"{self.approvals}/owner/login/nonce").json()["nonce"]
        key = ed25519_private_from_str(self.sim["phone_passkey.private_key"])
        sig = base64.b64encode(key.sign(f"login|{nonce}".encode())).decode()
        session = self.http.post(
            f"{self.approvals}/owner/login", json={"nonce": nonce, "signature_b64": sig}
        ).json()["session"]
        self.session = session
        r = self.http.post(
            self.base + "/owner/mode",
            json={"mode": "normal"},
            headers={"Authorization": f"Bearer {session}"},
        )
        self.check(
            r.json().get("mode") == "normal", "modus satt av eier (start er alltid mikrofoner av)"
        )
        time.sleep(1)

    def call(self) -> str:
        self.step("2) Samtale inn fra eierens nummer (simulert telefon)")
        call = f"CA{uuid.uuid4().hex[:12]}"
        self.check(
            "transkribert" in self.voice("charlie", call, "start", **{"from": OWNER}),
            "samtykkemelding spilles før opptak",
        )
        self.voice("charlie", call, "consent", answer="1")
        self.step("3) Nivå 1-svar med kjent nummer")
        self.check(
            "Klokka er" in self.voice("charlie", call, "audio", audio="Hva er klokka?"),
            "nivå 1 besvart",
        )
        self.check(
            "12 stk"
            in self.voice("charlie", call, "audio", audio="Hvor mange MOT-NEMA17 har vi på lager?"),
            "lager lest fra Postgres (nivå 1)",
        )
        self.step("4) PIN (DTMF)")
        self.check(
            self.voice("charlie", call, "dtmf", digits=self.creds["PIN"]) == "Takk, PIN godkjent.",
            "PIN godkjent",
        )
        self.step("5) Forslag på nivå 3")
        reply = self.voice(
            "charlie",
            call,
            "audio",
            audio="Send e-post til ola@example.no om Møte: Hei Ola, ses torsdag.",
        )
        self.check("nivå 3" in reply and "passkey" in reply, "forslag venter på passkey")
        self.voice("charlie", call, "end")
        return call

    def approve(self) -> None:
        self.step("6) Godkjenning med passkey (simulert telefon) og utføring")
        pid = self.psql(
            "select id from core.proposals where status='pending' order by created_at desc limit 1"
        )
        req = self.http.get(
            f"{self.approvals}/requests/{pid}", headers={"Authorization": f"Bearer {self.session}"}
        ).json()
        print(f"    telefonen viser:\n      {req['text'].replace(chr(10), chr(10) + '      ')}")
        key = ed25519_private_from_str(self.sim["phone_passkey.private_key"])
        sig = key.sign(
            approval_message(
                req["proposal_id"], req["params_hash"], req["challenge"], req["method"], req["text"]
            )
        )
        r = self.http.post(
            f"{self.approvals}/requests/{pid}/approve",
            json={"method": req["method"], "evidence_b64": base64.b64encode(sig).decode()},
        )
        self.check(
            r.status_code == 200 and r.json()["status"] == "executed", "utført etter godkjenning"
        )
        replay = self.http.post(
            f"{self.approvals}/requests/{pid}/approve",
            json={"method": req["method"], "evidence_b64": base64.b64encode(sig).decode()},
        )
        self.check(replay.status_code == 403, "samme godkjenning kan ikke spilles av igjen")

    def audit(self) -> None:
        self.step("7) Uforanderlig logg")
        kinds = self.psql("select string_agg(kind, ', ' order by seq) from audit.log")
        print(f"    {kinds[:400]}")
        self.check(self.psql("select ok from audit.verify_chain()") == "t", "hash-kjeden er gyldig")
        out = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "postgres",
                "-d",
                "assistants",
                "-c",
                "delete from audit.log",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=ROOT,
        )
        self.check("append-only" in out.stderr, "sletting i loggen blokkeres")

    def lola(self) -> None:
        self.step("8) Lola (gjest)")
        r = self.http.post(
            self.base + "/chat/guest", json={"text": "Når er det åpent?", "session": "demo"}
        )
        print(f"    Lola: {r.json()['reply']}")
        self.check("torsdager" in r.json()["reply"], "Lola svarer fra gjesteområdet")
        r = self.http.post(
            self.base + "/chat/guest",
            json={"text": "Hva står i kalenderen til Charlie?", "session": "demo"},
        )
        print(f"    Lola: {r.json()['reply']}")
        self.check("dessverre ikke" in r.json()["reply"], "Lola avslører ingenting om Charlie")

    def killswitch(self) -> None:
        self.step("9) Kill switch: ett ring fra kjent nummer = PAUSED")
        state = self.ks_post("/sim/ring", {"number": OWNER})
        self.check(state["state"] == "PAUSED", "PAUSED")
        time.sleep(1)
        call = f"CA{uuid.uuid4().hex[:12]}"
        self.voice("charlie", call, "start", **{"from": OWNER})
        self.voice("charlie", call, "consent", answer="1")
        self.check(
            "ikke tilgjengelig" in self.voice("charlie", call, "audio", audio="Hva er klokka?"),
            "Charlie stoppet",
        )
        self.voice("charlie", call, "end")
        self.step("10) Manuell gjenoppstart: PIN + annen enhet + logg gjennomgått + nøkler byttet")
        nonce = self.ks_post("/resume/nonce", {})["nonce"]
        head = self.psql("select hash from audit.log order by seq desc limit 1")
        second = ed25519_private_from_str(self.sim["second_device.private_key"])
        attempt = {
            "pin": self.creds["PIN"],
            "nonce": nonce,
            "audit_head": head,
            "signature_b64": base64.b64encode(second.sign(resume_message(nonce, head))).decode(),
        }
        self.check(
            self.ks_post("/resume", attempt)["state"] == "PAUSED",
            "uten nøkkelbytte: fortsatt PAUSED",
        )
        self.ks_post("/sim/owner-replaced-keys", {})
        nonce = self.ks_post("/resume/nonce", {})["nonce"]
        head = self.psql("select hash from audit.log order by seq desc limit 1")
        attempt = {
            "pin": self.creds["PIN"],
            "nonce": nonce,
            "audit_head": head,
            "signature_b64": base64.b64encode(second.sign(resume_message(nonce, head))).decode(),
        }
        self.check(self.ks_post("/resume", attempt)["state"] == "ACTIVE", "ACTIVE igjen")

    def restart(self) -> None:
        self.step("11) Kill switch-enheten startes på nytt i PAUSED: den blir i PAUSED")
        self.ks_post("/sim/app", {"action": "PAUSE"})
        subprocess.run(
            ["docker", "compose", "restart", "killswitch-sim"],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
        for _ in range(60):
            try:
                state = self.ks_post("/sim/state", {})["state"]
                break
            except httpx.HTTPError:
                time.sleep(1)
        self.check(state == "PAUSED", "fortsatt PAUSED etter omstart")
        self.ks_post("/sim/owner-replaced-keys", {})
        nonce = self.ks_post("/resume/nonce", {})["nonce"]
        head = self.psql("select hash from audit.log order by seq desc limit 1")
        second = ed25519_private_from_str(self.sim["second_device.private_key"])
        attempt = {
            "pin": self.creds["PIN"],
            "nonce": nonce,
            "audit_head": head,
            "signature_b64": base64.b64encode(second.sign(resume_message(nonce, head))).decode(),
        }
        self.check(
            self.ks_post("/resume", attempt)["state"] == "ACTIVE", "manuell gjenoppstart etterpå"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--approvals", default="http://127.0.0.1:8443")
    parser.add_argument("--killswitch", default="http://127.0.0.1:8090")
    args = parser.parse_args()
    demo = Demo(args.base, args.approvals, args.killswitch)
    demo.isolation()
    demo.owner_mode()
    demo.call()
    demo.approve()
    demo.audit()
    demo.lola()
    demo.killswitch()
    demo.restart()
    print("\nDemo OK" if demo.ok else "\nDemo FEILET")
    return 0 if demo.ok else 1


if __name__ == "__main__":
    sys.exit(main())
