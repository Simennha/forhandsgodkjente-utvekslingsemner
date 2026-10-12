"""LLM adapter: Anthropic Messages API and a deterministic mock.

Every call goes through :class:`MeteredLLM` (assistants.ops.costs) which enforces hard
budgets and records the real token cost. Structured outputs are requested with a JSON
schema and *always* re-validated by our own code afterwards - the model is never trusted
to have followed the schema.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Purpose = Literal["router", "hard", "lola", "extractor"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class LLMRequest:
    agent: str
    purpose: Purpose
    model: str
    system: str
    user_content: str
    max_tokens: int
    json_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: Usage
    model: str
    stop_reason: str


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class AnthropicLLM:
    """Anthropic Messages API (official SDK). NOT verified against the live API here.

    Run ``make eval-live`` with a real key to exercise it.
    """

    def __init__(self, api_key: str, timeout_s: float = 30.0) -> None:
        import anthropic  # noqa: PLC0415 - optional dependency (extra "live")

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=2)

    def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user_content}],
            "output_config": {"effort": "low"},
        }
        if request.json_schema is not None:
            kwargs["output_config"] = {
                "effort": "low",
                "format": {"type": "json_schema", "schema": request.json_schema},
            }
        response = self._client.messages.create(**kwargs)
        if response.stop_reason == "refusal":
            text = ""
        else:
            text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        return LLMResponse(
            text=text,
            usage=Usage(
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                cache_creation_input_tokens=int(
                    getattr(usage, "cache_creation_input_tokens", 0) or 0
                ),
            ),
            model=str(response.model),
            stop_reason=str(response.stop_reason),
        )


Brain = Callable[[LLMRequest], str]


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class MockLLM:
    """Deterministic stand-in. ``brains`` maps purpose -> function producing the text.

    The brains live next to the agents that use them (assistants.agents.mock_brains).
    ``calls`` records every request so tests can inspect exactly what a model saw.
    """

    brains: dict[str, Brain]
    calls: list[LLMRequest] = field(default_factory=list)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        brain = self.brains.get(request.purpose)
        text = brain(request) if brain else json.dumps({"reply": "OK"})
        return LLMResponse(
            text=text,
            usage=Usage(
                input_tokens=approx_tokens(request.system + request.user_content),
                output_tokens=approx_tokens(text),
            ),
            model=request.model,
            stop_reason="end_turn",
        )
