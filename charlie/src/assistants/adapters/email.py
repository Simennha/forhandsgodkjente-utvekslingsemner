"""E-mail adapter: Gmail API and a local mock.

There is deliberately no delete method. Sending is level 3 and only reachable from the
executor with a valid action token.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from email.message import EmailMessage as MimeMessage
from typing import Protocol

import httpx


@dataclass(frozen=True)
class InboundEmail:
    message_id: str
    sender: str
    subject: str
    body: str


@dataclass(frozen=True)
class Draft:
    draft_id: str
    to: tuple[str, ...]
    subject: str
    body: str


class EmailProvider(Protocol):
    def list_unread(self, limit: int = 20) -> list[InboundEmail]: ...
    def create_draft(self, to: list[str], subject: str, body: str) -> Draft: ...
    def send_draft(self, draft_id: str) -> str: ...


class GmailEmail:
    """Gmail REST API with an OAuth access token held by the executor. NOT verified."""

    BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

    def __init__(self, access_token: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=15.0, headers={"Authorization": f"Bearer {access_token}"}
        )

    def list_unread(self, limit: int = 20) -> list[InboundEmail]:
        resp = self._client.get(
            f"{self.BASE}/messages", params={"q": "is:unread", "maxResults": limit}
        )
        resp.raise_for_status()
        out: list[InboundEmail] = []
        for item in resp.json().get("messages", []):
            msg = self._client.get(f"{self.BASE}/messages/{item['id']}", params={"format": "full"})
            msg.raise_for_status()
            data = msg.json()
            headers = {h["name"].lower(): h["value"] for h in data["payload"].get("headers", [])}
            out.append(
                InboundEmail(
                    message_id=str(data["id"]),
                    sender=headers.get("from", ""),
                    subject=headers.get("subject", ""),
                    body=str(data.get("snippet", "")),
                )
            )
        return out

    def create_draft(self, to: list[str], subject: str, body: str) -> Draft:
        mime = MimeMessage()
        mime["To"] = ", ".join(to)
        mime["Subject"] = subject
        mime.set_content(body)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        resp = self._client.post(f"{self.BASE}/drafts", json={"message": {"raw": raw}})
        resp.raise_for_status()
        return Draft(str(resp.json()["id"]), tuple(to), subject, body)

    def send_draft(self, draft_id: str) -> str:
        resp = self._client.post(f"{self.BASE}/drafts/send", json={"id": draft_id})
        resp.raise_for_status()
        return str(resp.json()["id"])


@dataclass
class MockEmail:
    inbox: list[InboundEmail] = field(default_factory=list)
    drafts: dict[str, Draft] = field(default_factory=dict)
    sent: list[Draft] = field(default_factory=list)

    def list_unread(self, limit: int = 20) -> list[InboundEmail]:
        return self.inbox[:limit]

    def create_draft(self, to: list[str], subject: str, body: str) -> Draft:
        draft = Draft(f"d{len(self.drafts) + 1}", tuple(to), subject, body)
        self.drafts[draft.draft_id] = draft
        return draft

    def send_draft(self, draft_id: str) -> str:
        draft = self.drafts.pop(draft_id)
        self.sent.append(draft)
        return f"m{len(self.sent)}"
