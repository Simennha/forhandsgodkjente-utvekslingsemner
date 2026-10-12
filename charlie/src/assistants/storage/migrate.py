"""Minimal migration runner for supabase/migrations/*.sql (also usable with `supabase db push`)."""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from assistants.common.config import ROOT

MIGRATIONS = ROOT / "supabase" / "migrations"
SEED = ROOT / "supabase" / "seed"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def apply_migrations(dsn: str, seed: bool = False) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "create table if not exists public.schema_migrations "
            "(name text primary key, applied_at timestamptz not null default now())"
        )
        done = {r[0] for r in conn.execute("select name from public.schema_migrations")}
        for path in migration_files():
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into public.schema_migrations(name) values (%s)", (path.name,))
            applied.append(path.name)
        if seed:
            for path in sorted(SEED.glob("*.sql")):
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                applied.append(f"seed:{path.name}")
    return applied


def main() -> None:
    dsn = sys.argv[1]
    seed = "--seed" in sys.argv
    for name in apply_migrations(dsn, seed=seed):
        print(f"applied {name}")


if __name__ == "__main__":
    main()
