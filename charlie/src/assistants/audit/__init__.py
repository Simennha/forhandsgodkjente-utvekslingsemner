"""Immutable audit log: hash chain, append only, redacted, exported to WORM storage.

``MemoryAuditLog`` mirrors the database implementation (supabase/migrations/..._audit_log.sql)
for tests and the simulator. Neither has an update or delete method.
Everything is passed through ``redact`` before it is written: secrets never reach the log.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from assistants.adapters.objectstore import ObjectStore
from assistants.common.canonical import canonical_json, sha256_hex
from assistants.common.clock import Clock
from assistants.common.redact import redact

GENESIS = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    ts: datetime
    actor: str
    kind: str
    data: dict[str, Any]
    prev_hash: str
    hash: str


class AuditLog(Protocol):
    def append(self, actor: str, kind: str, data: dict[str, Any] | None = None) -> AuditEntry: ...
    def verify(self) -> bool: ...
    def head_hash(self) -> str: ...
    def entries(self, since_seq: int = 0) -> list[AuditEntry]: ...


def _hash(prev: str, seq: int, ts: datetime, actor: str, kind: str, data: dict[str, Any]) -> str:
    return sha256_hex(f"{prev}|{seq}|{ts.isoformat()}|{actor}|{kind}|{canonical_json(data)}")


class MemoryAuditLog:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: list[AuditEntry] = []

    def append(self, actor: str, kind: str, data: dict[str, Any] | None = None) -> AuditEntry:
        clean: dict[str, Any] = redact(data or {})
        prev = self._entries[-1].hash if self._entries else GENESIS
        seq = len(self._entries) + 1
        ts = self._clock.now()
        entry = AuditEntry(
            seq,
            ts,
            actor[:64],
            kind[:64],
            clean,
            prev,
            _hash(prev, seq, ts, actor[:64], kind[:64], clean),
        )
        self._entries.append(entry)
        return entry

    def verify(self) -> bool:
        prev = GENESIS
        for i, e in enumerate(self._entries, start=1):
            if e.seq != i or e.prev_hash != prev:
                return False
            if e.hash != _hash(e.prev_hash, e.seq, e.ts, e.actor, e.kind, e.data):
                return False
            prev = e.hash
        return True

    def head_hash(self) -> str:
        return self._entries[-1].hash if self._entries else GENESIS

    def entries(self, since_seq: int = 0) -> list[AuditEntry]:
        return [e for e in self._entries if e.seq > since_seq]

    def kinds(self) -> list[str]:
        return [e.kind for e in self._entries]


class PgAuditLog:
    """Writes go to audit.log; the trigger computes the hash. Verification runs in SQL."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def append(self, actor: str, kind: str, data: dict[str, Any] | None = None) -> AuditEntry:
        clean: dict[str, Any] = redact(data or {})
        with self._pool.connection() as conn:
            row = conn.execute(
                "select seq, ts, actor, kind, data, prev_hash, hash from audit.append(%s, %s, %s)",
                (actor[:64], kind[:64], Jsonb(clean)),
            ).fetchone()
        if row is None:
            raise RuntimeError("audit insert returned no row")
        return AuditEntry(*row)

    def verify(self) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute("select ok from audit.verify_chain()").fetchone()
        return bool(row and row[0])

    def head_hash(self) -> str:
        with self._pool.connection() as conn:
            row = conn.execute("select hash from audit.log order by seq desc limit 1").fetchone()
        return str(row[0]) if row else GENESIS

    def entries(self, since_seq: int = 0) -> list[AuditEntry]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select seq, ts, actor, kind, data, prev_hash, hash from audit.log "
                "where seq > %s order by seq",
                (since_seq,),
            ).fetchall()
        return [AuditEntry(*r) for r in rows]


def export_batch(
    log: AuditLog, store: ObjectStore, clock: Clock, since_seq: int, retain_days: int = 400
) -> int:
    """Export new entries as one JSONL object under object lock. Returns last exported seq."""
    entries = log.entries(since_seq)
    if not entries:
        return since_seq
    lines = [
        json.dumps(
            {
                "seq": e.seq,
                "ts": e.ts.isoformat(),
                "actor": e.actor,
                "kind": e.kind,
                "data": e.data,
                "prev_hash": e.prev_hash,
                "hash": e.hash,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for e in entries
    ]
    last = entries[-1].seq
    key = f"audit/{entries[0].seq:012d}-{last:012d}.jsonl"
    store.put(key, ("\n".join(lines) + "\n").encode(), clock.now() + timedelta(days=retain_days))
    return last
