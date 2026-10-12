"""rules service: POST /evaluate. Pure code, no database, no LLM, no outbound calls."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI

from assistants.adapters.secrets import SecretStore
from assistants.common.config import load_config
from assistants.policy import default_policy
from assistants.rules.engine import evaluate
from assistants.services.clients import facts_from_json
from assistants.services.common import ServiceAuth


def build(store: SecretStore) -> FastAPI:
    from assistants.services.apps import base_app  # noqa: PLC0415

    app = base_app("rules")
    auth = ServiceAuth(store)
    cfg = load_config()
    policy = default_policy()

    @app.post("/evaluate")
    def evaluate_route(
        body: dict[str, Any], _caller: str = Depends(auth.require("executor"))
    ) -> dict[str, Any]:
        d = evaluate(facts_from_json(body), policy, cfg)
        return {
            "outcome": d.outcome,
            "reason": d.reason,
            "level": d.level,
            "confirm_method": d.confirm_method,
        }

    return app
