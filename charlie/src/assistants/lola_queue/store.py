"""Guest area and Lola's one-way request queue.

Lola's side (``GuestArea``) can: read guest info, append messages/requests. Nothing else -
no read-back of the queue, no access to any Charlie data. The PostgreSQL version connects
as ``lola_agent``, whose grants enforce exactly this (tests/test_db_roles.py).

Charlie's side (``QueueReader``) reads queue items as UNTRUSTED DATA. Items are never
executed and never shown raw to a model that can act: they go through the isolated
extractor first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from psycopg_pool import ConnectionPool

from assistants.storage.stores import CostEntry

Channel = Literal["phone_lola", "door", "chat_guest", "sms_lola"]
MAX_TEXT = 2000


@dataclass(frozen=True)
class GuestInfo:
    key: str
    title: str
    body: str


@dataclass(frozen=True)
class QueueItem:
    id: uuid.UUID
    created_at: datetime
    channel: str
    from_number: str | None
    kind: str
    untrusted_text: str


class GuestArea(Protocol):
    def info(self) -> list[GuestInfo]: ...
    def add_request(
        self,
        channel: Channel,
        from_number: str | None,
        kind: Literal["message", "request"],
        text: str,
    ) -> None: ...
    def add_message(self, for_whom: str, from_name: str | None, text: str) -> None: ...


class QueueReader(Protocol):
    def unprocessed(self, limit: int) -> list[QueueItem]: ...
    def mark_processed(self, item_id: uuid.UUID, now: datetime) -> None: ...


@dataclass
class MemoryGuestStore:
    """Backs both sides in tests. Lola only ever receives ``lola_view()``."""

    guest_info: list[GuestInfo] = field(default_factory=list)
    queue: list[tuple[QueueItem, datetime | None]] = field(default_factory=list)
    messages: list[tuple[str, str | None, str]] = field(default_factory=list)
    now: datetime | None = None

    def lola_view(self) -> _LolaView:
        return _LolaView(self)

    def unprocessed(self, limit: int) -> list[QueueItem]:
        return [item for item, done in self.queue if done is None][:limit]

    def mark_processed(self, item_id: uuid.UUID, now: datetime) -> None:
        self.queue = [(i, now if i.id == item_id else d) for i, d in self.queue]


class _LolaView:
    """The only object Lola's code gets. Exposes exactly the GuestArea methods."""

    __slots__ = ("_store",)

    def __init__(self, store: MemoryGuestStore) -> None:
        self._store = store

    def info(self) -> list[GuestInfo]:
        return list(self._store.guest_info)

    def add_request(
        self,
        channel: Channel,
        from_number: str | None,
        kind: Literal["message", "request"],
        text: str,
    ) -> None:
        item = QueueItem(
            uuid.uuid4(),
            self._store.now or datetime.now().astimezone(),
            channel,
            from_number,
            kind,
            text[:MAX_TEXT],
        )
        self._store.queue.append((item, None))

    def add_message(self, for_whom: str, from_name: str | None, text: str) -> None:
        self._store.messages.append(
            (for_whom[:80], from_name[:80] if from_name else None, text[:MAX_TEXT])
        )


class PgGuestArea:
    """Connects with the lola_agent role (its own DSN, its own password)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def info(self) -> list[GuestInfo]:
        with self._pool.connection() as conn:
            rows = conn.execute("select key, title, body from guest.info order by key").fetchall()
        return [GuestInfo(*r) for r in rows]

    def add_request(
        self,
        channel: Channel,
        from_number: str | None,
        kind: Literal["message", "request"],
        text: str,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into guest.lola_requests (channel, from_number, kind, untrusted_text) "
                "values (%s,%s,%s,%s)",
                (channel, from_number, kind, text[:MAX_TEXT]),
            )

    def add_message(self, for_whom: str, from_name: str | None, text: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into guest.messages (for_whom, from_name, untrusted_text) "
                "values (%s,%s,%s)",
                (for_whom[:80], from_name[:80] if from_name else None, text[:MAX_TEXT]),
            )


class PgQueueReader:
    """Connects with the worker_svc role (reads + marks processed)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def unprocessed(self, limit: int) -> list[QueueItem]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id, created_at, channel, from_number, kind, untrusted_text "
                "from guest.lola_requests where processed_at is null order by created_at limit %s",
                (limit,),
            ).fetchall()
        return [QueueItem(*r) for r in rows]

    def mark_processed(self, item_id: uuid.UUID, now: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "update guest.lola_requests set processed_at = %s where id = %s", (now, item_id)
            )


class PgLolaLedger:
    """Lola's own cost ledger + tool-call log (guest.lola_usage), used for HER caps only."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # CostLedger
    def add(self, entry: CostEntry) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into guest.lola_usage (ts, kind, channel, model, input_tokens, "
                "output_tokens, cost_nok, day, month) values (%s,'llm',%s,%s,%s,%s,%s,%s,%s)",
                (
                    entry.ts,
                    entry.channel,
                    entry.model,
                    entry.input_tokens,
                    entry.output_tokens,
                    entry.cost_nok,
                    entry.day,
                    entry.month,
                ),
            )

    def spent(self, agent: str, day: str | None = None, month: str | None = None) -> float:
        if agent != "lola":
            return 0.0
        with self._pool.connection() as conn:
            row = conn.execute(
                "select coalesce(sum(cost_nok), 0) from guest.lola_usage where kind = 'llm' "
                "and (%s::date is null or day = %s::date) and (%s::text is null or month = %s)",
                (day, day, month, month),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def breakdown(self, month: str) -> list[tuple[str, str, float]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select channel, sum(cost_nok) from guest.lola_usage where month = %s "
                "and kind = 'llm' group by channel order by channel",
                (month,),
            ).fetchall()
        return [("lola", str(c), float(v)) for c, v in rows]

    # ToolCallLog
    def record(self, agent: str, tool: str, ts: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into guest.lola_usage (ts, kind, tool, channel, day, month) "
                "values (%s,'tool',%s,'-',%s,%s)",
                (ts, tool, ts.date(), ts.strftime("%Y-%m")),
            )

    def count_since(self, agent: str, tool: str, since: datetime) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) from guest.lola_usage where kind = 'tool' and tool = %s "
                "and ts >= %s",
                (tool, since),
            ).fetchone()
        return int(row[0]) if row else 0

    def heartbeat(self, now: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into guest.lola_heartbeat (id, last_seen) values (1, %s) "
                "on conflict (id) do update set last_seen = excluded.last_seen",
                (now,),
            )
