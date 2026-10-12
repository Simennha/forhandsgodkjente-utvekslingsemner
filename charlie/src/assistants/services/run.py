"""Entry point: ``python -m assistants.services.run <service>``."""

from __future__ import annotations

import os
import sys

import uvicorn
from fastapi import FastAPI

SERVICES = (
    "gateway",
    "rules",
    "executor",
    "agents-charlie",
    "agents-lola",
    "extractor",
    "workroom",
    "approvals",
    "worker",
    "killswitch-sim",
)


def build(name: str) -> FastAPI:
    from assistants.services import apps  # noqa: PLC0415 - keep import cost per service

    return apps.build_app(name)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in SERVICES:
        sys.exit(f"usage: run <{'|'.join(SERVICES)}>")
    name = sys.argv[1]
    host = os.environ.get("BIND_HOST", "0.0.0.0")  # noqa: S104 - inside a container network
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(build(name), host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
