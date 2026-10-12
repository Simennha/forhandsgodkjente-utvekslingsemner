"""Audit log: hash chain, append only, redacted, WORM export."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from assistants.adapters.objectstore import MemoryObjectStore
from assistants.audit import MemoryAuditLog, PgAuditLog, export_batch
from assistants.common.clock import FakeClock
from assistants.common.errors import Denied
from assistants.common.redact import register_secret


def test_memory_chain_detects_tampering() -> None:
    log = MemoryAuditLog(FakeClock())
    for i in range(5):
        log.append("executor", "action.executed", {"i": i})
    assert log.verify()
    entries = log._entries
    entries[2] = dataclasses.replace(entries[2], data={"i": 99})
    assert not log.verify()


def test_memory_log_has_no_mutation_api() -> None:
    log = MemoryAuditLog(FakeClock())
    for name in ("update", "delete", "remove", "truncate", "clear", "edit", "disable"):
        assert not hasattr(log, name)


def test_secrets_are_redacted_before_writing() -> None:
    register_secret("sk-live-hemmelig-123456")
    log = MemoryAuditLog(FakeClock())
    e = log.append(
        "x",
        "y",
        {
            "pin": "482913",
            "note": "nøkkel sk-live-hemmelig-123456",
            "auth": "Bearer abcdefghijklmnopqrstu",
        },
    )
    text = str(e.data)
    assert "482913" not in text
    assert "sk-live-hemmelig-123456" not in text
    assert "abcdefghijklmnopqrstu" not in text


def test_export_is_write_once() -> None:
    clock = FakeClock()
    log = MemoryAuditLog(clock)
    store = MemoryObjectStore()
    log.append("a", "b")
    last = export_batch(log, store, clock, 0)
    assert last == 1
    assert store.list("audit/") == ["audit/000000000001-000000000001.jsonl"]
    assert export_batch(log, store, clock, last) == last
    with pytest.raises(Denied):
        store.put("audit/000000000001-000000000001.jsonl", b"x", datetime(2030, 1, 1, tzinfo=UTC))


@pytest.mark.db
def test_pg_log_append_verify_and_block_changes(pg_dsn: str) -> None:
    with ConnectionPool(pg_dsn, min_size=1, max_size=2, open=True) as pool:
        log = PgAuditLog(pool)
        a = log.append("executor", "action.executed", {"tool": "time.read"})
        b = log.append("gateway", "call.started", {"line": "charlie"})
        assert b.prev_hash == a.hash
        assert log.verify()
        assert log.head_hash() == b.hash
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for stmt in (
            "update audit.log set actor = 'x'",
            "delete from audit.log",
            "truncate audit.log",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)


@pytest.mark.db
def test_pg_roles_cannot_modify_log(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for role in ("charlie_agent", "executor_svc", "worker_svc", "gateway_svc", "lola_agent"):
            conn.execute(f"set role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("delete from audit.log")
            conn.execute("reset role")
        conn.execute("set role lola_agent")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into audit.log (actor, kind) values ('lola', 'x')")
        conn.execute("reset role")


@pytest.mark.db
def test_pg_caller_cannot_choose_hash(pg_dsn: str) -> None:
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        row = conn.execute(
            "insert into audit.log (actor, kind, prev_hash, hash, seq) "
            "values ('a', 'b', 'forged', 'forged', 999999) returning seq, prev_hash, hash"
        ).fetchone()
        assert row is not None
        assert row[0] != 999999
        assert row[1] != "forged"
        assert row[2] != "forged"
        ok = conn.execute("select ok from audit.verify_chain()").fetchone()
        assert ok is not None
        assert ok[0]
