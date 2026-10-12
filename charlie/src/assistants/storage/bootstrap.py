"""One-shot container job: apply migrations + seed, then give each role its login password."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql

from assistants.adapters.secrets import FileSecretStore
from assistants.storage.migrate import apply_migrations

ROLES = ("charlie_agent", "lola_agent", "executor_svc", "gateway_svc", "worker_svc")


def main() -> None:
    store = FileSecretStore(Path(os.environ["SECRETS_FILE"]))
    dsn = store.get("db.admin_dsn")
    for name in apply_migrations(dsn, seed=True):
        print(f"applied {name}")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for role in ROLES:
            conn.execute(
                sql.SQL("alter role {} login password {}").format(
                    sql.Identifier(role), sql.Literal(store.get(f"db.password.{role}"))
                )
            )
    print("roles configured")


if __name__ == "__main__":
    main()
