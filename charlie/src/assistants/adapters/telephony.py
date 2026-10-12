"""Telephony (voice + SMS) adapter: Twilio implementation and a local simulator.

Webhook authenticity uses Twilio's documented scheme for both implementations:
base64(HMAC-SHA1(auth_token, url + concatenated sorted key/value pairs)).
The simulator signs with its own key using the same algorithm, so the gateway code path
under test is identical to production.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Protocol

import httpx


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def verify_twilio_signature(
    auth_token: str, url: str, params: dict[str, str], signature: str
) -> bool:
    expected = twilio_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class OutboundSms:
    from_number: str
    to_number: str
    body: str


class TelephonyProvider(Protocol):
    def send_sms(self, from_number: str, to_number: str, body: str) -> str:
        """Send an SMS, return provider message id."""
        ...

    def verify_webhook(self, url: str, params: dict[str, str], signature: str) -> bool: ...


class TwilioTelephony:
    """Twilio REST implementation. NOT verified against the real service."""

    def __init__(self, account_sid: str, auth_token: str, client: httpx.Client | None = None):
        self._sid = account_sid
        self._token = auth_token
        self._client = client or httpx.Client(timeout=10.0)

    def send_sms(self, from_number: str, to_number: str, body: str) -> str:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"
        resp = self._client.post(
            url,
            data={"From": from_number, "To": to_number, "Body": body},
            auth=(self._sid, self._token),
        )
        resp.raise_for_status()
        return str(resp.json()["sid"])

    def verify_webhook(self, url: str, params: dict[str, str], signature: str) -> bool:
        return verify_twilio_signature(self._token, url, params, signature)


@dataclass
class SimTelephony:
    """In-memory simulator. Records every SMS; signs/validates like Twilio."""

    signing_key: str
    outbox: list[OutboundSms] = field(default_factory=list)

    def send_sms(self, from_number: str, to_number: str, body: str) -> str:
        self.outbox.append(OutboundSms(from_number, to_number, body))
        return f"SMsim{len(self.outbox):06d}"

    def verify_webhook(self, url: str, params: dict[str, str], signature: str) -> bool:
        return verify_twilio_signature(self.signing_key, url, params, signature)

    def sign(self, url: str, params: dict[str, str]) -> str:
        return twilio_signature(self.signing_key, url, params)
