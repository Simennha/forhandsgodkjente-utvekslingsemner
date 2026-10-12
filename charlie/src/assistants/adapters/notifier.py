"""Push notifications (e.g. ntfy.sh self-hosted) and a simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx


class Notifier(Protocol):
    def push(self, title: str, body: str, priority: str = "default") -> bool: ...


class NtfyNotifier:
    """ntfy (self-hosted recommended). NOT verified."""

    def __init__(self, topic_url: str, token: str, client: httpx.Client | None = None) -> None:
        self._url = topic_url
        self._client = client or httpx.Client(
            timeout=10.0, headers={"Authorization": f"Bearer {token}"}
        )

    def push(self, title: str, body: str, priority: str = "default") -> bool:
        try:
            resp = self._client.post(
                self._url, content=body.encode(), headers={"Title": title, "Priority": priority}
            )
        except httpx.HTTPError:
            return False
        return resp.is_success


@dataclass
class SimNotifier:
    sent: list[tuple[str, str, str]] = field(default_factory=list)

    def push(self, title: str, body: str, priority: str = "default") -> bool:
        self.sent.append((title, body, priority))
        return True
