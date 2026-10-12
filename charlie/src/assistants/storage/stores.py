"""Stores used by the executor: proposals, single-use tokens, cost ledger, tool-call log.

Each store has an in-memory implementation (tests, simulator) and a PostgreSQL one
(compose stack). Status changes of proposals are compare-and-set, so a second approval
of the same proposal (replay) can never execute twice.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ProposalStatus = Literal["pending", "approved", "executed", "denied", "expired", "frozen", "failed"]


@dataclass(frozen=True)
class StoredProposal:
    id: uuid.UUID
    created_at: datetime
    agent: str
    tool: str
    params: dict[str, Any]
    params_hash: str
    level: int
    status: ProposalStatus
    confirm_method: str | None
    challenge: str | None
    amount_nok: float | None
    expires_at: datetime | None
    idempotency_key: str
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class CostEntry:
    ts: datetime
    agent: str
    channel: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    cost_nok: float
    day: str
    month: str


class ProposalStore(Protocol):
    def save(self, p: StoredProposal) -> None: ...
    def get(self, pid: uuid.UUID) -> StoredProposal | None: ...
    def transition(
        self,
        pid: uuid.UUID,
        from_status: ProposalStatus,
        to_status: ProposalStatus,
        result: dict[str, Any] | None = None,
    ) -> bool: ...
    def freeze_pending(self) -> int: ...
    def list_status(self, status: ProposalStatus) -> list[StoredProposal]: ...
    def orders_total(self, month: str) -> float: ...


class TokenLedger(Protocol):
    def use_once(self, jti: str) -> bool: ...


class CostLedger(Protocol):
    def add(self, entry: CostEntry) -> None: ...
    def spent(self, agent: str, day: str | None = None, month: str | None = None) -> float: ...
    def breakdown(self, month: str) -> list[tuple[str, str, float]]: ...


class ToolCallLog(Protocol):
    def record(self, agent: str, tool: str, ts: datetime) -> None: ...
    def count_since(self, agent: str, tool: str, since: datetime) -> int: ...


# ---------------------------------------------------------------- memory


class MemoryProposalStore:
    def __init__(self) -> None:
        self._items: dict[uuid.UUID, StoredProposal] = {}
        self._lock = threading.Lock()

    def save(self, p: StoredProposal) -> None:
        with self._lock:
            if any(x.idempotency_key == p.idempotency_key for x in self._items.values()):
                raise ValueError("duplicate idempotency key")
            self._items[p.id] = p

    def get(self, pid: uuid.UUID) -> StoredProposal | None:
        return self._items.get(pid)

    def transition(
        self,
        pid: uuid.UUID,
        from_status: ProposalStatus,
        to_status: ProposalStatus,
        result: dict[str, Any] | None = None,
    ) -> bool:
        with self._lock:
            p = self._items.get(pid)
            if p is None or p.status != from_status:
                return False
            self._items[pid] = replace(p, status=to_status, result=result or p.result)
            return True

    def freeze_pending(self) -> int:
        with self._lock:
            n = 0
            for pid, p in list(self._items.items()):
                if p.status in ("pending", "approved"):
                    self._items[pid] = replace(p, status="frozen")
                    n += 1
            return n

    def list_status(self, status: ProposalStatus) -> list[StoredProposal]:
        return [p for p in self._items.values() if p.status == status]

    def orders_total(self, month: str) -> float:
        return sum(
            p.amount_nok or 0.0
            for p in self._items.values()
            if p.tool == "inventory.order"
            and p.status in ("approved", "executed")
            and p.created_at.strftime("%Y-%m") == month
        )


class MemoryTokenLedger:
    def __init__(self) -> None:
        self._used: set[str] = set()
        self._lock = threading.Lock()

    def use_once(self, jti: str) -> bool:
        with self._lock:
            if jti in self._used:
                return False
            self._used.add(jti)
            return True


class MemoryCostLedger:
    def __init__(self) -> None:
        self.entries: list[CostEntry] = []

    def add(self, entry: CostEntry) -> None:
        self.entries.append(entry)

    def spent(self, agent: str, day: str | None = None, month: str | None = None) -> float:
        return sum(
            e.cost_nok
            for e in self.entries
            if e.agent == agent
            and (day is None or e.day == day)
            and (month is None or e.month == month)
        )

    def breakdown(self, month: str) -> list[tuple[str, str, float]]:
        totals: dict[tuple[str, str], float] = {}
        for e in self.entries:
            if e.month == month:
                totals[(e.agent, e.channel)] = totals.get((e.agent, e.channel), 0.0) + e.cost_nok
        return sorted((a, c, v) for (a, c), v in totals.items())


class MemoryToolCallLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, datetime]] = []

    def record(self, agent: str, tool: str, ts: datetime) -> None:
        self.calls.append((agent, tool, ts))

    def count_since(self, agent: str, tool: str, since: datetime) -> int:
        return sum(1 for a, t, ts in self.calls if a == agent and t == tool and ts >= since)


# ---------------------------------------------------------------- postgres


def _row_to_proposal(r: tuple[Any, ...]) -> StoredProposal:
    return StoredProposal(
        id=r[0],
        created_at=r[1],
        agent=r[2],
        tool=r[3],
        params=r[4],
        params_hash=r[5],
        level=r[6],
        status=r[7],
        confirm_method=r[8],
        challenge=r[9],
        amount_nok=float(r[10]) if r[10] is not None else None,
        expires_at=r[11],
        idempotency_key=r[12],
        result=r[13],
    )


_COLS = (
    "id, created_at, agent, tool, params, params_hash, level, status, confirm_method, "
    "challenge, amount_nok, expires_at, idempotency_key, result"
)


class PgProposalStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def save(self, p: StoredProposal) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"insert into core.proposals ({_COLS}) values "  # noqa: S608 - constant columns
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    p.id,
                    p.created_at,
                    p.agent,
                    p.tool,
                    Jsonb(p.params),
                    p.params_hash,
                    p.level,
                    p.status,
                    p.confirm_method,
                    p.challenge,
                    p.amount_nok,
                    p.expires_at,
                    p.idempotency_key,
                    Jsonb(p.result) if p.result is not None else None,
                ),
            )

    def get(self, pid: uuid.UUID) -> StoredProposal | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"select {_COLS} from core.proposals where id = %s",  # noqa: S608
                (pid,),
            ).fetchone()
        return _row_to_proposal(row) if row else None

    def transition(
        self,
        pid: uuid.UUID,
        from_status: ProposalStatus,
        to_status: ProposalStatus,
        result: dict[str, Any] | None = None,
    ) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "update core.proposals set status = %s, result = coalesce(%s, result) "
                "where id = %s and status = %s",
                (to_status, Jsonb(result) if result is not None else None, pid, from_status),
            )
            return cur.rowcount == 1

    def freeze_pending(self) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "update core.proposals set status = 'frozen' where status in ('pending','approved')"
            )
            return cur.rowcount

    def list_status(self, status: ProposalStatus) -> list[StoredProposal]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"select {_COLS} from core.proposals where status = %s order by created_at",  # noqa: S608
                (status,),
            ).fetchall()
        return [_row_to_proposal(r) for r in rows]

    def orders_total(self, month: str) -> float:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select coalesce(sum(amount_nok), 0) from core.proposals "
                "where tool = 'inventory.order' and status in ('approved','executed') "
                "and to_char(created_at at time zone 'Europe/Oslo', 'YYYY-MM') = %s",
                (month,),
            ).fetchone()
        return float(row[0]) if row else 0.0


class PgTokenLedger:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def use_once(self, jti: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "insert into core.used_tokens (jti) values (%s) on conflict do nothing", (jti,)
            )
            return cur.rowcount == 1


class PgCostLedger:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add(self, e: CostEntry) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.cost_ledger (ts, agent, channel, model, input_tokens, "
                "output_tokens, cache_read, cache_write, cost_nok, day, month) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    e.ts,
                    e.agent,
                    e.channel,
                    e.model,
                    e.input_tokens,
                    e.output_tokens,
                    e.cache_read,
                    e.cache_write,
                    e.cost_nok,
                    e.day,
                    e.month,
                ),
            )

    def spent(self, agent: str, day: str | None = None, month: str | None = None) -> float:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select coalesce(sum(cost_nok), 0) from core.cost_ledger where agent = %s "
                "and (%s::date is null or day = %s::date) and (%s::text is null or month = %s)",
                (agent, day, day, month, month),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def breakdown(self, month: str) -> list[tuple[str, str, float]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select agent, channel, sum(cost_nok) from core.cost_ledger where month = %s "
                "group by agent, channel order by agent, channel",
                (month,),
            ).fetchall()
        return [(str(a), str(c), float(v)) for a, c, v in rows]


class PgToolCallLog:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(self, agent: str, tool: str, ts: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.tool_calls (ts, agent, tool) values (%s,%s,%s)", (ts, agent, tool)
            )

    def count_since(self, agent: str, tool: str, since: datetime) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) from core.tool_calls where agent = %s and tool = %s and ts >= %s",
                (agent, tool, since),
            ).fetchone()
        return int(row[0]) if row else 0
