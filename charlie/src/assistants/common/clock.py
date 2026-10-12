"""Injectable clocks so that every time-based rule is testable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC now."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Manually advanced clock for tests and simulators."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 12, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def set(self, when: datetime) -> None:
        self._now = when


def oslo_date(clock: Clock) -> str:
    """ISO date in Europe/Oslo; budgets reset at local midnight."""
    return clock.now().astimezone(OSLO).date().isoformat()


def oslo_month(clock: Clock) -> str:
    return clock.now().astimezone(OSLO).strftime("%Y-%m")
