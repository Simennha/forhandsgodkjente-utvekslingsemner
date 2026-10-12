"""One-shot container job: apply migrations + seed to the main and the guest database,
then give each role its login password.

The guest database (db-guest) is a separate Postgres instance. Only the guest schema is used
there; the same migrations are applied so the roles and grants are identical.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql

from assistants.adapters.secrets import FileSecretStore
from assistants.storage.migrate import apply_migrations

MAIN_ROLES = ("executor_svc", "gateway_svc", "worker_svc")  # no login: charlie_agent, lola_agent
GUEST_ROLES = ("lola_agent", "worker_svc")


def _setup(dsn: str, passwords: dict[str, str]) -> None:
    for name in apply_migrations(dsn, seed=True):
        print(f"applied {name}")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for role, password in passwords.items():
            conn.execute(
                sql.SQL("alter role {} login password {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )


def main() -> None:
    store = FileSecretStore(Path(os.environ["SECRETS_FILE"]))
    # Lola has no login on the main database at all.
    _setup(store.get("db.admin_dsn"), {r: store.get(f"db.password.{r}") for r in MAIN_ROLES})
    _setup(
        store.get("db.guest_admin_dsn"),
        {r: store.get(f"db.guest_password.{r}") for r in GUEST_ROLES},
    )
    print("roles configured")


if __name__ == "__main__":
    main()
