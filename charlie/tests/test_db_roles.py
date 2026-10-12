"""Phase 2: separated database roles. Lola's role reaches the guest area only.

Runs as each role with SET ROLE inside a fresh database (migrations + seed applied).
"""

from __future__ import annotations

import psycopg
import pytest

pytestmark = pytest.mark.db

CHARLIE_TABLES = [
    "core.notes",
    "core.projects",
    "core.proposals",
    "core.events",
    "core.cost_ledger",
    "core.transcripts_raw",
    "core.transcript_extracts",
    "core.consent_log",
    "core.used_tokens",
    "lager.artikkel",
    "lager.beholdning",
    "lager.bevegelse",
    "audit.log",
]


def as_role(conn: psycopg.Connection, role: str) -> None:
    conn.execute("reset role")
    conn.execute(f"set role {role}")


@pytest.mark.parametrize("table", CHARLIE_TABLES)
def test_lola_cannot_read_charlie_data(pg: psycopg.Connection, table: str) -> None:
    as_role(pg, "lola_agent")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute(f"select * from {table} limit 1")


@pytest.mark.parametrize(
    "table", ["core.notes", "core.proposals", "core.events", "lager.bevegelse"]
)
def test_lola_cannot_write_charlie_data(pg: psycopg.Connection, table: str) -> None:
    as_role(pg, "lola_agent")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute(f"delete from {table}")


def test_lola_cannot_call_charlie_functions(pg: psycopg.Connection) -> None:
    as_role(pg, "lola_agent")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("select * from lager.generer_innkjopsforslag('NO')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("select * from audit.append('lola', 'x', '{}')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("select * from audit.verify_chain()")


def test_lola_guest_area_is_exactly_what_she_gets(pg: psycopg.Connection) -> None:
    as_role(pg, "lola_agent")
    assert pg.execute("select count(*) from guest.info").fetchone() == (5,)
    pg.execute(
        "insert into guest.lola_requests (channel, kind, untrusted_text) "
        "values ('door','request','hei')"
    )
    pg.execute("insert into guest.messages (for_whom, untrusted_text) values ('Verkstedet','hei')")
    # One-way queue: Lola cannot read back, change or delete what she queued.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("select * from guest.lola_requests")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("update guest.lola_requests set processed_at = now()")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("delete from guest.lola_requests")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("update guest.info set body = 'x'")


def test_lola_queue_rows_are_always_untrusted(pg: psycopg.Connection) -> None:
    as_role(pg, "lola_agent")
    with pytest.raises(psycopg.errors.Error):
        pg.execute(
            "insert into guest.lola_requests (channel, kind, untrusted_text, trust) "
            "values ('door','request','x','trusted')"
        )


def test_charlie_agent_role_is_read_only(pg: psycopg.Connection) -> None:
    as_role(pg, "charlie_agent")
    assert pg.execute("select count(*) from lager.artikkel").fetchone()[0] >= 40  # type: ignore[index]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute(
            "insert into lager.bevegelse (artikkel_id, lagerplass_id, endring, arsak, utfort_av) "
            "values (1, 1, 1, 'mottak', 'x')"
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("select * from core.proposals")


def test_extractor_role_has_nothing(pg: psycopg.Connection) -> None:
    as_role(pg, "extractor_svc")
    for table in ("guest.info", "core.notes", "lager.artikkel", "audit.log"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg.execute(f"select 1 from {table} limit 1")


def test_gateway_can_write_log_but_not_read_it(pg: psycopg.Connection) -> None:
    as_role(pg, "gateway_svc")
    row = pg.execute("select seq from audit.append('gateway', 'test', '{}')").fetchone()
    assert row is not None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg.execute("select * from audit.log")
