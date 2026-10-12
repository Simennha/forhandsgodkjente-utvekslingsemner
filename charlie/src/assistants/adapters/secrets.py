"""SecretStore: read-only access to secrets.

There is deliberately NO write/update/delete method: changing keys is a level 4 action and
has no code path. Keys are created and rotated by the owner, offline, with
``scripts/gen_secrets.py`` or the provider consoles (see docs/runbook.md).

Implementations:
* MemorySecretStore - tests.
* FileSecretStore   - JSON file, or a SOPS/age-encrypted JSON file decrypted with ``sops -d``.
* SupabaseVaultStore - reads ``vault.decrypted_secrets`` with the executor's DB role.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import psycopg

from assistants.common.errors import SecretMissing
from assistants.common.redact import register_secret


class SecretStore(Protocol):
    def get(self, name: str) -> str: ...
    def has(self, name: str) -> bool: ...


class MemorySecretStore:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        for v in self._values.values():
            register_secret(v)

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError as exc:
            raise SecretMissing(name) from exc

    def has(self, name: str) -> bool:
        return name in self._values


class FileSecretStore(MemorySecretStore):
    def __init__(self, path: Path) -> None:
        if path.suffix == ".sops" or path.name.endswith(".sops.json"):
            sops = shutil.which("sops")
            if sops is None:
                raise FileNotFoundError("sops binary required for encrypted secrets")
            raw = subprocess.run(  # noqa: S603
                [sops, "-d", str(path)], check=True, capture_output=True, timeout=30
            ).stdout.decode()
        else:
            mode = path.stat().st_mode
            if mode & 0o002:
                raise PermissionError(f"{path} must not be world-writable")
            if mode & 0o044 and not (
                str(path).startswith("/run/secrets/") or not path.parent.stat().st_mode & 0o077
            ):
                raise PermissionError(f"{path}: readable by others outside a private directory")
            raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("secret file must contain a JSON object")
        super().__init__({str(k): str(v) for k, v in data.items()})


class SupabaseVaultStore:
    """Reads Supabase Vault. NOT verified against a hosted Supabase project."""

    def __init__(self, dsn: str, names: list[str]) -> None:
        self._values: dict[str, str] = {}
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select name, decrypted_secret from vault.decrypted_secrets where name = any(%s)",
                (names,),
            )
            for name, value in cur.fetchall():
                self._values[str(name)] = str(value)
                register_secret(str(value))

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError as exc:
            raise SecretMissing(name) from exc

    def has(self, name: str) -> bool:
        return name in self._values
