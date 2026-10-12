"""Lola: reception for everyone except the owner.

Lola is built from ONLY: her own LLM client (own key, own budget), the guest area
(guest info + append-only queue), the rule engine (pure code) and her kill-switch view.
She has no reference to any Charlie store, adapter, key or service, and her container has
no network route to them. The queue she writes to is data for Charlie's side, never orders.

Her model returns a fixed structure: a reply and one of three actions. Anything else
(e.g. a Charlie tool name) fails validation and becomes "no action". Her reply passes an
output guard before anyone hears it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from assistants.adapters.llm import LLMClient, LLMRequest
from assistants.common.canonical import canonical_json
from assistants.common.clock import Clock, oslo_date, oslo_month
from assistants.common.config import AppConfig
from assistants.common.redact import redact_text
from assistants.events import Mode, Trust
from assistants.killswitch.core import KSState
from assistants.lola_queue.store import Channel, GuestArea
from assistants.policy import Policy
from assistants.rules.engine import Decision, Facts
from assistants.storage.stores import CostLedger, ToolCallLog

FALLBACK = "Beklager, det kan jeg ikke hjelpe med. Jeg kan ta imot en beskjed."
UNAVAILABLE = "Lola er ikke tilgjengelig akkurat nå. Prøv igjen senere."

# Output guard: Lola never talks about internals, tools, keys, or makes promises for others.
_GUARD = re.compile(
    r"\b(guest|inventory|calendar|email|house|notes|project|workshop|alert|report|backup|keys|"
    r"firewall|machine|money|alarm)\.[a-z_]+\b"
    r"|sk-ant|api[- ]?n[øo]kkel|\bpin\b|passord til systemet|\btoken\b|\{|\}"
    r"|har godkjent|godkjent av charlie|p[åa] vegne av charlie|charlie (har|vil|skal) "
    r"|kalenderen til|e-posten til|lageret|innkjøp"
    r"|godkjen|kode(n)? til|\b\d{4,}\b",
    re.IGNORECASE,
)


class LolaDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: Annotated[str, StringConstraints(max_length=500)]
    action: Literal["none", "take_message", "queue_request"]
    for_whom: Annotated[str, StringConstraints(max_length=80)] | None = None
    from_name: Annotated[str, StringConstraints(max_length=80)] | None = None


LOLA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "action", "for_whom", "from_name"],
    "properties": {
        "reply": {"type": "string"},
        "action": {"type": "string", "enum": ["none", "take_message", "queue_request"]},
        "for_whom": {"type": ["string", "null"]},
        "from_name": {"type": ["string", "null"]},
    },
}

ACTION_TOOL = {"take_message": "guest.take_message", "queue_request": "guest.queue_request"}


def guard_reply(text: str) -> str:
    clean = redact_text(text).strip()
    if not clean or _GUARD.search(clean):
        return FALLBACK
    return clean


@dataclass(frozen=True)
class LolaTurn:
    reply: str
    action: str
    decision: Decision | None


class LolaAgent:
    def __init__(
        self,
        llm: LLMClient,
        guest: GuestArea,
        cfg: AppConfig,
        policy: Policy,
        system_prompt: str,
        rules: Callable[[Facts], Decision],
        killswitch: Callable[[], tuple[KSState, bool]],
        calls: ToolCallLog,
        costs: CostLedger,
        clock: Clock,
    ) -> None:
        self._llm = llm
        self._guest = guest
        self._cfg = cfg
        self._policy = policy
        self._system = system_prompt
        self._rules = rules
        self._ks = killswitch
        self._calls = calls
        self._costs = costs
        self._clock = clock

    def _facts(self, tool: str) -> Facts:
        state, enabled = self._ks()
        now = self._clock.now()
        return Facts(
            agent="lola",
            tool=tool,
            trust=Trust.GUEST,
            mode=Mode.GUEST,
            killswitch=state,
            lola_enabled=enabled,
            calls_last_hour=self._calls.count_since("lola", tool, now - timedelta(hours=1)),
            spent_today_nok=self._costs.spent("lola", day=oslo_date(self._clock)),
            spent_month_nok=self._costs.spent("lola", month=oslo_month(self._clock)),
        )

    def handle(self, text: str, channel: Channel, from_number: str | None) -> LolaTurn:
        read = self._rules(self._facts("guest.house_rules_read"))
        if not read.allowed:
            return LolaTurn(UNAVAILABLE, "none", read)
        info = [{"key": i.key, "title": i.title, "body": i.body} for i in self._guest.info()]
        request = LLMRequest(
            agent="lola",
            purpose="lola",
            model=self._cfg.llm.lola_model,
            system=self._system,
            user_content=canonical_json(
                {"guest_info": info, "channel": channel, "guest_text": text[:2000]}
            ),
            max_tokens=self._cfg.llm.max_tokens.get("lola", 512),
            json_schema=LOLA_SCHEMA,
            channel=channel,
        )
        try:
            raw = self._llm.complete(request).text
            decision = LolaDecision.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            return LolaTurn(FALLBACK, "none", None)
        except Exception:  # noqa: BLE001 - budget exhausted or provider down
            return LolaTurn(UNAVAILABLE, "none", None)

        action = decision.action
        rule: Decision | None = None
        if action != "none":
            tool = ACTION_TOOL[action]
            rule = self._rules(self._facts(tool))
            if rule.allowed:
                self._calls.record("lola", tool, self._clock.now())
                if action == "take_message":
                    self._guest.add_message(
                        decision.for_whom or "Verkstedet", decision.from_name, text
                    )
                    self._guest.add_request(channel, from_number, "message", text)
                else:
                    self._guest.add_request(channel, from_number, "request", text)
            else:
                action = "none"
        return LolaTurn(guard_reply(decision.reply), action, rule)
