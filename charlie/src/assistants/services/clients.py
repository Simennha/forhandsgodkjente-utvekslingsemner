"""HTTP implementations of the in-process interfaces, so services can be wired like SimSystem."""

from __future__ import annotations

import dataclasses
from typing import Any

from assistants.adapters.llm import LLMRequest, LLMResponse, Usage
from assistants.agents.extractor import Extraction
from assistants.events import Mode, Trust
from assistants.executor.core import CallContext, SubmitResult
from assistants.killswitch.core import KSState
from assistants.rules.engine import Decision, Facts
from assistants.services.common import ServiceClient


def facts_to_json(f: Facts) -> dict[str, Any]:
    data = dataclasses.asdict(f)
    data["trust"] = f.trust.value
    data["mode"] = f.mode.value
    data["killswitch"] = f.killswitch.value
    return data


def facts_from_json(d: dict[str, Any]) -> Facts:
    return Facts(
        agent=d["agent"],
        tool=str(d["tool"]),
        trust=Trust(d["trust"]),
        mode=Mode(d["mode"]),
        killswitch=KSState(d["killswitch"]),
        lola_enabled=bool(d["lola_enabled"]),
        calls_last_hour=int(d["calls_last_hour"]),
        spent_today_nok=float(d["spent_today_nok"]),
        spent_month_nok=float(d["spent_month_nok"]),
        amount_nok=None if d.get("amount_nok") is None else float(d["amount_nok"]),
        orders_month_nok=float(d.get("orders_month_nok", 0.0)),
    )


class RulesClient:
    def __init__(self, client: ServiceClient) -> None:
        self._c = client

    def __call__(self, facts: Facts) -> Decision:
        try:
            d = self._c.post("/evaluate", facts_to_json(facts))
        except Exception:  # noqa: BLE001 - rules unreachable => deny (fail closed)
            return Decision("deny", "rules_unavailable", 0)
        return Decision(d["outcome"], d["reason"], int(d["level"]), d.get("confirm_method"))


class HttpLLM:
    """LLM via the executor's metered proxy. The caller holds no provider key."""

    def __init__(self, client: ServiceClient) -> None:
        self._c = client

    def complete(self, request: LLMRequest) -> LLMResponse:
        d = self._c.post("/llm", dataclasses.asdict(request))
        u = d["usage"]
        return LLMResponse(
            d["text"],
            Usage(
                u["input_tokens"],
                u["output_tokens"],
                u.get("cache_read_input_tokens", 0),
                u.get("cache_creation_input_tokens", 0),
            ),
            d["model"],
            d["stop_reason"],
        )


class HttpExtractor:
    def __init__(self, client: ServiceClient) -> None:
        self._c = client

    def extract(
        self,
        source: str,
        text: str,
        agent: str = "charlie",
        channel: str = "internal",
        now: str | None = None,
    ) -> Extraction:
        try:
            d = self._c.post(
                "/extract",
                {"source": source, "text": text, "agent": agent, "channel": channel, "now": now},
            )
            return Extraction.model_validate(d)
        except Exception:  # noqa: BLE001 - degrade to a safe, flagged extraction
            return Extraction(
                source=source, summary="(uttrekk utilgjengelig)", flags=["extractor_error"]
            )


def ctx_to_json(ctx: CallContext) -> dict[str, Any]:
    return {
        "trust": ctx.trust.value,
        "mode": ctx.mode.value,
        "channel": ctx.channel,
        "session_id": ctx.session_id,
    }


def ctx_from_json(d: dict[str, Any]) -> CallContext:
    return CallContext(
        Trust(d["trust"]), Mode(d["mode"]), str(d["channel"])[:20], d.get("session_id")
    )


class HttpSubmit:
    def __init__(self, client: ServiceClient) -> None:
        self._c = client

    def __call__(self, proposal: dict[str, Any], ctx: CallContext) -> SubmitResult:
        try:
            d = self._c.post("/proposals", {"proposal": proposal, "ctx": ctx_to_json(ctx)})
        except Exception:  # noqa: BLE001
            return SubmitResult("denied", str(proposal.get("tool", "?")), 0, "executor_unavailable")
        return SubmitResult(**d)
