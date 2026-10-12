"""Shared fixtures. Database tests get a fresh database with all migrations + seed."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from assistants.common.redact import SECRET_VALUES


def _admin_dsn() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    """DSN of a fresh database with migrations and seed applied (session scoped)."""
    admin = _admin_dsn()
    require = os.environ.get("CHARLIE_REQUIRE_DB") == "1"
    if not admin:
        if require:
            pytest.fail("CHARLIE_REQUIRE_DB=1 but TEST_DATABASE_URL is not set")
        pytest.skip("no TEST_DATABASE_URL")
    try:
        conn = psycopg.connect(admin, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if require:
            pytest.fail(f"test database not reachable: {exc}")
        pytest.skip("test database not reachable")
    name = f"t_{uuid.uuid4().hex[:12]}"
    with conn:
        conn.execute(f"create database {name}")
    dsn = admin.rsplit("/", 1)[0] + f"/{name}"
    from assistants.storage.migrate import apply_migrations  # noqa: PLC0415

    apply_migrations(dsn, seed=True)
    yield dsn
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"drop database if exists {name} with (force)")


@pytest.fixture
def pg(pg_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture(autouse=True)
def _clear_secret_registry() -> Iterator[None]:
    yield
    SECRET_VALUES.clear()
