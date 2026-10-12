"""Consent log, raw transcripts (deleted after N days) and extracts (kept).

Only phone calls are transcribed. There is no ambient transcription anywhere.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ConsentAnswer = Literal["accepted", "declined", "no_answer"]


@dataclass(frozen=True)
class ConsentRecord:
    ts: datetime
    call_id: str
    agent: str
    number: str
    answer: ConsentAnswer


@dataclass(frozen=True)
class RawTranscript:
    id: uuid.UUID
    call_id: str
    agent: str
    created_at: datetime
    delete_after: datetime
    text: str


@dataclass(frozen=True)
class Extract:
    id: uuid.UUID
    call_id: str
    agent: str
    created_at: datetime
    extract: dict[str, Any]


class TranscriptStore(Protocol):
    def log_consent(self, rec: ConsentRecord) -> None: ...
    def add_raw(self, call_id: str, agent: str, text: str, now: datetime, days: int) -> None: ...
    def add_extract(
        self, call_id: str, agent: str, extract: dict[str, Any], now: datetime
    ) -> None: ...
    def extracts(self, agent: str, call_id: str | None, limit: int) -> list[Extract]: ...
    def purge_expired(self, now: datetime) -> int: ...
    def raw_count(self) -> int: ...
    def raw_without_extract(self, limit: int) -> list[RawTranscript]: ...


@dataclass
class MemoryTranscripts:
    consent: list[ConsentRecord] = field(default_factory=list)
    raw: list[RawTranscript] = field(default_factory=list)
    extracted: list[Extract] = field(default_factory=list)

    def log_consent(self, rec: ConsentRecord) -> None:
        self.consent.append(rec)

    def add_raw(self, call_id: str, agent: str, text: str, now: datetime, days: int) -> None:
        self.raw.append(
            RawTranscript(uuid.uuid4(), call_id, agent, now, now + timedelta(days=days), text)
        )

    def add_extract(self, call_id: str, agent: str, extract: dict[str, Any], now: datetime) -> None:
        self.extracted.append(Extract(uuid.uuid4(), call_id, agent, now, extract))

    def extracts(self, agent: str, call_id: str | None, limit: int) -> list[Extract]:
        hits = [
            e
            for e in reversed(self.extracted)
            if e.agent == agent and (call_id is None or e.call_id == call_id)
        ]
        return hits[:limit]

    def purge_expired(self, now: datetime) -> int:
        before = len(self.raw)
        self.raw = [r for r in self.raw if r.delete_after > now]
        return before - len(self.raw)

    def raw_count(self) -> int:
        return len(self.raw)

    def raw_without_extract(self, limit: int) -> list[RawTranscript]:
        done = {e.call_id for e in self.extracted}
        return [r for r in self.raw if r.call_id not in done][:limit]


class PgTranscripts:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def log_consent(self, rec: ConsentRecord) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.consent_log (ts, call_id, agent, number, answer) "
                "values (%s,%s,%s,%s,%s)",
                (rec.ts, rec.call_id, rec.agent, rec.number, rec.answer),
            )

    def add_raw(self, call_id: str, agent: str, text: str, now: datetime, days: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.transcripts_raw "
                "(id, call_id, agent, created_at, delete_after, text) values (%s,%s,%s,%s,%s,%s)",
                (uuid.uuid4(), call_id, agent, now, now + timedelta(days=days), text),
            )

    def add_extract(self, call_id: str, agent: str, extract: dict[str, Any], now: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.transcript_extracts (id, call_id, agent, created_at, extract) "
                "values (%s,%s,%s,%s,%s)",
                (uuid.uuid4(), call_id, agent, now, Jsonb(extract)),
            )

    def extracts(self, agent: str, call_id: str | None, limit: int) -> list[Extract]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select id, call_id, agent, created_at, extract from core.transcript_extracts "
                "where agent = %s and (%s::text is null or call_id = %s) "
                "order by created_at desc limit %s",
                (agent, call_id, call_id, limit),
            ).fetchall()
        return [Extract(*r) for r in rows]

    def purge_expired(self, now: datetime) -> int:
        with self._pool.connection() as conn:
            cur = conn.execute("delete from core.transcripts_raw where delete_after <= %s", (now,))
            return cur.rowcount

    def raw_count(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("select count(*) from core.transcripts_raw").fetchone()
        return int(row[0]) if row else 0

    def raw_without_extract(self, limit: int) -> list[RawTranscript]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select r.id, r.call_id, r.agent, r.created_at, r.delete_after, r.text "
                "from core.transcripts_raw r where not exists (select 1 from "
                "core.transcript_extracts e where e.call_id = r.call_id) "
                "order by r.created_at limit %s",
                (limit,),
            ).fetchall()
        return [RawTranscript(*r) for r in rows]
