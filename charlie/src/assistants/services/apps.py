"""FastAPI app per service.

Each builder reads ONLY its own secrets file (SECRETS_FILE) and wires the same classes as
``assistants.sim.SimSystem``, but with PostgreSQL stores and HTTP clients between services.
Provider credentials that are still the placeholder ``FAKE-REPLACE-ME`` are replaced by the
simulator for that provider, and the service logs it (see HANDOFF.md).

Without a secrets file (unit tests) the apps only serve /health.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from assistants.services.common import load_secrets


def base_app(name: str, ready: Callable[[], bool] | None = None) -> FastAPI:
    app = FastAPI(title=name, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        status = "ok" if ready is None or ready() else "degraded"
        return {"service": name, "status": status}

    return app


def build_app(name: str) -> FastAPI:
    store = load_secrets()
    if store is None:
        return base_app(name)
    builders: dict[str, Callable[[Any], FastAPI]] = {}
    from assistants.services import (  # noqa: PLC0415 - import only what a service needs
        svc_agents,
        svc_approvals,
        svc_executor,
        svc_gateway,
        svc_killswitch,
        svc_rules,
        svc_worker,
    )

    builders = {
        "gateway": svc_gateway.build,
        "rules": svc_rules.build,
        "executor": svc_executor.build,
        "agents-charlie": svc_agents.build_charlie,
        "agents-lola": svc_agents.build_lola,
        "extractor": svc_agents.build_extractor,
        "workroom": svc_agents.build_workroom,
        "approvals": svc_approvals.build,
        "worker": svc_worker.build,
        "killswitch-sim": svc_killswitch.build,
    }
    return builders[name](store)
