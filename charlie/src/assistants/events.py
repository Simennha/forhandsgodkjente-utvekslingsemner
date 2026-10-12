"""Events: everything that enters the system becomes an Event with source, time and trust.

Trust is decided by the gateway from *how* something arrived (signed webhook, caller ID,
PIN), never from what the content claims.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Trust(StrEnum):
    OWNER_PIN = "owner_pin"  # known number + PIN (or passkey-authenticated chat)
    OWNER_KNOWN = "owner_known"  # known number only
    DURESS = "duress"  # duress code: looks like owner_pin, only harmless level 1
    GUEST = "guest"  # Lola's callers, door, guest chat
    UNKNOWN = "unknown"  # unknown number on Charlie's line
    SYSTEM = "system"  # scheduled jobs inside the system
    UNTRUSTED = "untrusted"  # content from outside: e-mail, queue items, transcripts of others


class Mode(StrEnum):
    NORMAL = "normal"
    GUEST = "guest"
    OPEN_WORKSHOP = "open_workshop"


class Source(StrEnum):
    PHONE_CHARLIE = "phone_charlie"
    PHONE_LOLA = "phone_lola"
    SMS_CHARLIE = "sms_charlie"
    SMS_LOLA = "sms_lola"
    CHAT = "chat"
    CHAT_GUEST = "chat_guest"
    EMAIL = "email"
    DOOR = "door"
    MIC = "mic"
    SYSTEM = "system"


AgentTarget = Literal["charlie", "lola", "system"]

# Doorbell/door phone and guest channels can never be routed to Charlie.
LOLA_ONLY_SOURCES = frozenset({Source.PHONE_LOLA, Source.SMS_LOLA, Source.CHAT_GUEST, Source.DOOR})


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    ts: datetime
    source: Source
    trust: Trust
    agent: AgentTarget
    mode: Mode
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, _context: Any) -> None:
        if self.source in LOLA_ONLY_SOURCES and self.agent != "lola":
            raise ValueError(f"{self.source} may only be routed to lola")
        if self.agent == "lola" and self.trust not in (Trust.GUEST, Trust.UNTRUSTED):
            raise ValueError("lola events always carry guest/untrusted trust")


class EventQueue(Protocol):
    def put(self, event: Event) -> None: ...
    def claim(self, agent: AgentTarget) -> Event | None: ...
    def pending(self, agent: AgentTarget) -> int: ...


class MemoryEventQueue:
    def __init__(self) -> None:
        self._queues: dict[str, deque[Event]] = {
            "charlie": deque(),
            "lola": deque(),
            "system": deque(),
        }
        self.history: list[Event] = []

    def put(self, event: Event) -> None:
        self._queues[event.agent].append(event)
        self.history.append(event)

    def claim(self, agent: AgentTarget) -> Event | None:
        q = self._queues[agent]
        return q.popleft() if q else None

    def pending(self, agent: AgentTarget) -> int:
        return len(self._queues[agent])
