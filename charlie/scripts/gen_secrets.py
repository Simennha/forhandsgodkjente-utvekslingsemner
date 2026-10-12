"""Generate LOCAL development secrets for the demo stack (``make secrets``).

Writes one JSON file per service into ./secrets (directory chmod 700). Every service gets
ONLY the secrets it needs - e.g. agents-charlie gets no provider keys at all, Lola gets her
own LLM key and nothing of Charlie's.

Provider credentials (Anthropic, Twilio, Google, Home Assistant, ...) are written as
obviously fake placeholders. Replace them by hand for a real deployment - see HANDOFF.md.

Also writes:
* secrets/dev-credentials.txt - demo PIN, duress code, test word and kill-switch SMS code
  (read it, then delete it; real codes should only live in your head)
* secrets/sim-devices.json   - private keys of SIMULATED devices (phone passkey, approval
  box, second device). Never used in production.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistants.common.crypto import (
    hash_credential,
    new_ed25519,
    random_token,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SECRETS_DIR", ROOT / "secrets"))
FAKE = "FAKE-REPLACE-ME"

ROLES = ["executor_svc", "gateway_svc", "worker_svc"]  # Lola: guest DB only
SERVICES = [
    "gateway",
    "rules",
    "executor",
    "charlie",
    "lola",
    "extractor",
    "approvals",
    "worker",
    "killswitch",
]


def credential(code: str, pepper: bytes = b"") -> dict[str, str]:
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": hash_credential(code, salt, pepper).hex()}


def main() -> None:
    force = "--force" in sys.argv
    if (OUT / "gateway.json").exists() and not force:
        print(f"{OUT} already has secrets; use --force to regenerate")
        return
    OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(OUT, 0o700)

    tokens = {s: random_token() for s in [*SERVICES, "migrate"]}
    db_pw = {r: random_token(24) for r in ROLES}
    db_admin = random_token(24)
    db_admin_guest = random_token(24)
    guest_pw = {r: random_token(24) for r in ("lola_agent", "worker_svc")}

    def dsn(role: str, host: str = "db") -> str:
        return f"postgresql://{role}:{db_pw[role]}@{host}:5432/assistants"

    def guest_dsn(role: str) -> str:
        return f"postgresql://{role}:{guest_pw[role]}@db-guest:5432/guest"

    lease_priv, lease_pub = new_ed25519()
    appr_priv, appr_pub = new_ed25519()
    phone_priv, phone_pub = new_ed25519()
    box_priv, box_pub = new_ed25519()
    second_priv, second_pub = new_ed25519()

    # 8 digits: with the pepper stolen too, brute force is ~100x harder than 6 (red team #6).
    pin = f"{secrets.randbelow(10**8):08d}"
    duress = f"{secrets.randbelow(10**8):08d}"
    while duress == pin:
        duress = f"{secrets.randbelow(10**8):08d}"
    words = ["blåbær", "tunfisk", "kompass", "fyrlykt", "nordlys", "lakris", "skiferstein"]
    test_word = f"{secrets.choice(words)} {secrets.choice(words)}"
    ks_sms_code = f"STOPP-{random_token(6)}"
    pepper = secrets.token_bytes(32)
    twilio_sim = random_token()
    action_key = random_token(32)
    sim_control = random_token()

    ctx_key = random_token(32)
    files: dict[str, dict[str, str]] = {
        "gateway": {
            "self.token": tokens["gateway"],
            "accept.killswitch": tokens["killswitch"],
            "twilio.auth_token": twilio_sim,
            "twilio.account_sid": FAKE,
            "ctx.hmac_key": ctx_key,
            "approvals.public_key": appr_pub,
            "identity.pepper": pepper.hex(),
            "identity.pin": json.dumps(credential(pin, pepper)),
            "identity.duress": json.dumps(credential(duress, pepper)),
            "identity.test_word": json.dumps(credential(test_word, pepper)),
            "lease.public_key": lease_pub,
            "db.dsn": dsn("gateway_svc"),
        },
        "rules": {"self.token": tokens["rules"], "accept.executor": tokens["executor"]},
        "executor": {
            "self.token": tokens["executor"],
            "accept.charlie": tokens["charlie"],
            "accept.extractor": tokens["extractor"],
            "accept.worker": tokens["worker"],
            "accept.approvals": tokens["approvals"],
            "accept.gateway": tokens["gateway"],
            "accept.killswitch": tokens["killswitch"],
            "action.hmac_key": action_key,
            "ctx.hmac_key": ctx_key,
            "approvals.public_key": appr_pub,
            "lease.public_key": lease_pub,
            "db.dsn": dsn("executor_svc"),
            "anthropic.charlie_api_key": FAKE,
            "twilio.account_sid": FAKE,
            "twilio.auth_token": twilio_sim,
            "google.access_token": FAKE,
            "homeassistant.url": "http://homeassistant.local:8123",
            "homeassistant.token": FAKE,
        },
        "charlie": {
            "self.token": tokens["charlie"],
            "accept.gateway": tokens["gateway"],
            "accept.killswitch": tokens["killswitch"],
            "lease.public_key": lease_pub,
        },
        "lola": {
            "self.token": tokens["lola"],
            "accept.gateway": tokens["gateway"],
            "accept.killswitch": tokens["killswitch"],
            "anthropic.lola_api_key": FAKE,
            "lease.public_key": lease_pub,
            "db.dsn": guest_dsn("lola_agent"),
        },
        "extractor": {
            "self.token": tokens["extractor"],
            "accept.charlie": tokens["charlie"],
            "accept.executor": tokens["executor"],
            "accept.worker": tokens["worker"],
        },
        "approvals": {
            "self.token": tokens["approvals"],
            "accept.executor": tokens["executor"],
            "approvals.private_key": appr_priv,
            "passkey.sim_public_key": phone_pub,
            "box.public_key": box_pub,
        },
        "worker": {
            "self.token": tokens["worker"],
            "db.dsn": dsn("worker_svc"),
            "db.guest_dsn": guest_dsn("worker_svc"),
            "objectstore.access_key": FAKE,
        },
        "killswitch": {
            "self.token": tokens["killswitch"],
            "accept.gateway": tokens["gateway"],
            "accept.worker": tokens["worker"],
            "accept.sim": sim_control,
            "lease.private_key": lease_priv,
            "pin": json.dumps(credential(pin)),
            "sms_code": json.dumps(credential(ks_sms_code)),
            "second_device.public_key": second_pub,
            "anthropic.admin_key": FAKE,
            "router.token": FAKE,
        },
        "migrate": {
            "db.admin_dsn": f"postgresql://postgres:{db_admin}@db:5432/assistants",
            **{f"db.password.{r}": pw for r, pw in db_pw.items()},
            "db.guest_admin_dsn": f"postgresql://postgres:{db_admin_guest}@db-guest:5432/guest",
            **{f"db.guest_password.{r}": pw for r, pw in guest_pw.items()},
        },
    }
    for name, content in files.items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")
        # Readable by the container user; the directory itself is 700.
        os.chmod(path, 0o644)
    for fname, value in (("db_admin.txt", db_admin), ("db_admin_guest.txt", db_admin_guest)):
        (OUT / fname).write_text(value, encoding="utf-8")
        os.chmod(OUT / fname, 0o644)

    sim = {
        "phone_passkey.private_key": phone_priv,
        "approval_box.private_key": box_priv,
        "second_device.private_key": second_priv,
        "twilio.sim_signing_key": twilio_sim,
        "killswitch.sim_token": sim_control,
    }
    (OUT / "sim-devices.json").write_text(json.dumps(sim, indent=2), encoding="utf-8")
    os.chmod(OUT / "sim-devices.json", 0o600)
    creds = (
        "DEV ONLY - demo credentials. Read, then delete this file.\n"
        f"PIN={pin}\nDURESS_CODE={duress}\nTEST_WORD={test_word}\n"
        f"KILLSWITCH_SMS_CODE={ks_sms_code}\n"
    )
    (OUT / "dev-credentials.txt").write_text(creds, encoding="utf-8")
    os.chmod(OUT / "dev-credentials.txt", 0o600)
    print(f"wrote secrets to {OUT}")


if __name__ == "__main__":
    main()
