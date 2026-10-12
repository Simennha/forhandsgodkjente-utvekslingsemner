"""Calendar adapter: Google Calendar and a local mock.

Own events (no attendees) are level 2. Invitations (attendees) are level 3 - the two are
separate methods so that the executor can bind each to its own tool and level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    attendees: tuple[str, ...] = ()


class CalendarProvider(Protocol):
    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]: ...
    def create_own_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent: ...
    def invite(
        self, title: str, start: datetime, end: datetime, attendees: list[str]
    ) -> CalendarEvent: ...


class GoogleCalendar:
    """Google Calendar REST v3. NOT verified against the real service."""

    BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

    def __init__(self, access_token: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=15.0, headers={"Authorization": f"Bearer {access_token}"}
        )

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        resp = self._client.get(
            self.BASE,
            params={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
            },
        )
        resp.raise_for_status()
        return [
            CalendarEvent(
                event_id=str(item["id"]),
                title=str(item.get("summary", "")),
                start=datetime.fromisoformat(item["start"]["dateTime"]),
                end=datetime.fromisoformat(item["end"]["dateTime"]),
                attendees=tuple(a["email"] for a in item.get("attendees", [])),
            )
            for item in resp.json().get("items", [])
            if "dateTime" in item.get("start", {})
        ]

    def _insert(
        self, title: str, start: datetime, end: datetime, attendees: list[str]
    ) -> CalendarEvent:
        body: dict[str, object] = {
            "summary": title,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        params = {}
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
            params["sendUpdates"] = "all"
        resp = self._client.post(self.BASE, json=body, params=params)
        resp.raise_for_status()
        return CalendarEvent(str(resp.json()["id"]), title, start, end, tuple(attendees))

    def create_own_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        return self._insert(title, start, end, [])

    def invite(
        self, title: str, start: datetime, end: datetime, attendees: list[str]
    ) -> CalendarEvent:
        return self._insert(title, start, end, attendees)


@dataclass
class MockCalendar:
    events: list[CalendarEvent] = field(default_factory=list)

    def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return [e for e in self.events if e.start < end and e.end > start]

    def create_own_event(self, title: str, start: datetime, end: datetime) -> CalendarEvent:
        event = CalendarEvent(f"e{len(self.events) + 1}", title, start, end)
        self.events.append(event)
        return event

    def invite(
        self, title: str, start: datetime, end: datetime, attendees: list[str]
    ) -> CalendarEvent:
        event = CalendarEvent(f"e{len(self.events) + 1}", title, start, end, tuple(attendees))
        self.events.append(event)
        return event
