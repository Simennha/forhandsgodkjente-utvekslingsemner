"""agents-charlie, agents-lola, extractor and workroom services."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from assistants.adapters.secrets import SecretStore
from assistants.agents.charlie import CharlieAgent
from assistants.agents.extractor import Extractor
from assistants.agents.lola import UNAVAILABLE, LolaAgent
from assistants.common.clock import SystemClock
from assistants.common.config import load_config
from assistants.common.ctxsign import peek_ctx
from assistants.executor.core import CallContext, SubmitResult
from assistants.lola_queue.store import PgGuestArea, PgLolaLedger
from assistants.ops.costs import MeteredLLM
from assistants.policy import default_policy
from assistants.prompts import load_prompt
from assistants.rules.engine import Facts, evaluate
from assistants.services.clients import HttpExtractor, HttpLLM
from assistants.services.common import URLS, ServiceAuth, ServiceClient, every, lease_view, pool
from assistants.services.wiring import llm_backend


def _lease_route(app: FastAPI, auth: ServiceAuth, view: Any) -> None:
    @app.post("/internal/lease")
    def lease_route(
        body: dict[str, Any], _c: str = Depends(auth.require("killswitch"))
    ) -> dict[str, str]:
        view.accept(str(body.get("token", "")))
        return {"state": view.state().value}


def build_charlie(store: SecretStore) -> FastAPI:
    """No provider keys, no database: LLM and actions only via the executor."""
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    clock = SystemClock()
    auth = ServiceAuth(store)
    me = store.get("self.token")
    executor = ServiceClient(URLS["executor"], me)
    ks = lease_view(store, clock)
    app = base_app("agents-charlie")
    _lease_route(app, auth, ks)
    every(30, lambda: executor.post("/heartbeat", {}), "heartbeat")

    @app.post("/turn")
    def turn(body: dict[str, Any], _c: str = Depends(auth.require("gateway"))) -> dict[str, str]:
        if not ks.charlie_may_act():
            return {"reply": "Charlie er ikke tilgjengelig akkurat nå."}
        token = str(body["ctx_token"])

        def submit(proposal: dict[str, Any], _ctx: CallContext) -> SubmitResult:
            # The signed context goes through untouched; the executor verifies it.
            try:
                d = executor.post("/proposals", {"proposal": proposal, "ctx_token": token})
            except Exception:  # noqa: BLE001
                return SubmitResult(
                    "denied", str(proposal.get("tool", "?")), 0, "executor_unavailable"
                )
            return SubmitResult(**d)

        agent = CharlieAgent(
            HttpLLM(executor),
            HttpExtractor(ServiceClient(URLS["extractor"], me)),
            submit,
            cfg,
            default_policy(),
            load_prompt("charlie"),
            clock,
        )
        return {"reply": agent.handle_text(str(body["text"])[:4000], peek_ctx(token)).reply}

    return app


def build_lola(store: SecretStore) -> FastAPI:
    """Lola's own key, own budget, own DB role. Nothing of Charlie's is reachable."""
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    policy = default_policy()
    clock = SystemClock()
    auth = ServiceAuth(store)
    db = pool(store.get("db.dsn"))
    ledger = PgLolaLedger(db)
    ks = lease_view(store, clock)
    llm = MeteredLLM(llm_backend(cfg, store, "anthropic.lola_api_key"), "lola", cfg, ledger, clock)

    def rules(f: Facts) -> Any:
        return evaluate(f, policy, cfg)

    lola = LolaAgent(
        llm,
        PgGuestArea(db),
        cfg,
        policy,
        load_prompt("lola"),
        rules,
        lambda: (ks.state(), ks.lola_may_act()),
        ledger,
        ledger,
        clock,
    )
    app = base_app("agents-lola")
    _lease_route(app, auth, ks)
    every(30, lambda: ledger.heartbeat(clock.now()), "heartbeat")

    @app.post("/turn")
    def turn(body: dict[str, Any], _c: str = Depends(auth.require("gateway"))) -> dict[str, str]:
        channel = body.get("channel")
        if channel not in ("phone_lola", "door", "chat_guest", "sms_lola"):
            raise HTTPException(status_code=400, detail="bad channel")
        if not ks.lola_may_act():
            return {"reply": UNAVAILABLE}
        turn_result = lola.handle(str(body["text"])[:2000], channel, body.get("from"))
        return {"reply": turn_result.reply}

    return app


def build_extractor(store: SecretStore) -> FastAPI:
    """No tools, no database, no internet: only the executor's metered LLM proxy."""
    from assistants.services.apps import base_app  # noqa: PLC0415

    cfg = load_config()
    auth = ServiceAuth(store)
    extractor = Extractor(
        HttpLLM(ServiceClient(URLS["executor"], store.get("self.token"))),
        cfg,
        default_policy(),
        load_prompt("extractor"),
    )
    app = base_app("extractor")

    @app.post("/extract")
    def extract(
        body: dict[str, Any], _c: str = Depends(auth.require("charlie", "executor", "worker"))
    ) -> dict[str, Any]:
        ext = extractor.extract(
            str(body.get("source", "unknown"))[:40],
            str(body.get("text", "")),
            agent="charlie",
            channel=str(body.get("channel", "internal"))[:20],
            now=body.get("now"),
        )
        return ext.model_dump()

    return app


def build_workroom(_store: SecretStore) -> FastAPI:
    """Sandbox for code, CAD and simulation: no network at all, no keys. Used via
    ``docker compose exec workroom ...`` with the /work volume."""
    from assistants.services.apps import base_app  # noqa: PLC0415

    return base_app("workroom")
