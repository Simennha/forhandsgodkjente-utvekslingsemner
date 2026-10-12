"""PostgreSQL event queue (core.events). The gateway only inserts; the worker claims."""

from __future__ import annotations

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from assistants.events import AgentTarget, Event, Trust


class PgEventQueue:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def put(self, event: Event) -> None:
        # Duress is never persisted: it is stored exactly like a PIN session.
        trust = Trust.OWNER_PIN if event.trust == Trust.DURESS else event.trust
        with self._pool.connection() as conn:
            conn.execute(
                "insert into core.events (id, ts, source, trust, agent, mode, payload) "
                "values (%s,%s,%s,%s,%s,%s,%s)",
                (
                    event.id,
                    event.ts,
                    event.source.value,
                    trust.value,
                    event.agent,
                    event.mode.value,
                    Jsonb({**event.payload, "session": event.session_id}),
                ),
            )

    def claim(self, agent: AgentTarget) -> Event | None:
        raise NotImplementedError("the gateway never claims events")

    def pending(self, agent: AgentTarget) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) from core.events where agent = %s and done_at is null", (agent,)
            ).fetchone()
        return int(row[0]) if row else 0
