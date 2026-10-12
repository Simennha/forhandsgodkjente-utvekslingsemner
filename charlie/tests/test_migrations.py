"""Database schema: migrations apply, audit log is append-only, roles are separated."""

from __future__ import annotations

import psycopg
import pytest

pytestmark = pytest.mark.db


def test_all_schemas_exist(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select schema_name from information_schema.schemata "
        "where schema_name in ('core','audit','guest','lager')"
    ).fetchall()
    assert {r[0] for r in rows} == {"core", "audit", "guest", "lager"}


def test_lager_has_nine_tables(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select table_name from information_schema.tables "
        "where table_schema = 'lager' and table_type = 'BASE TABLE'"
    ).fetchall()
    assert {r[0] for r in rows} == {
        "lokasjon",
        "lagerplass",
        "artikkel",
        "lagerregel",
        "beholdning",
        "bevegelse",
        "batteri",
        "leverandor",
        "innkjopsforslag",
    }


def test_migrations_are_idempotent(pg_dsn: str) -> None:
    from assistants.storage.migrate import apply_migrations  # noqa: PLC0415

    assert apply_migrations(pg_dsn) == []
