"""Real token cost accounting and hard budgets per agent, day and month.

Before a call: if spent + worst case (full input + max_tokens output) would exceed the
daily or monthly cap, the call is refused and the agent is paused until the period rolls
over. After a call: the real usage (input, output, cache read/write tokens) is priced from
config and written to the ledger.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from assistants.adapters.llm import LLMClient, LLMRequest, LLMResponse, Usage, approx_tokens
from assistants.common.clock import Clock, oslo_date, oslo_month
from assistants.common.config import AppConfig, ModelPrice
from assistants.common.errors import Denied
from assistants.storage.stores import CostEntry, CostLedger


def cost_nok(usage: Usage, price: ModelPrice, usd_to_nok: float) -> float:
    usd = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.cache_write
    ) / 1_000_000
    return round(usd * usd_to_nok, 6)


@dataclass
class BudgetPaused:
    agent: str
    reason: str


class MeteredLLM:
    """Wraps an LLMClient for ONE agent ('charlie' or 'lola')."""

    def __init__(
        self,
        inner: LLMClient,
        agent: str,
        cfg: AppConfig,
        ledger: CostLedger,
        clock: Clock,
        on_pause: Callable[[BudgetPaused], None] | None = None,
        on_call: Callable[[CostEntry], None] | None = None,
    ) -> None:
        self._inner = inner
        self._agent = agent
        self._cfg = cfg
        self._ledger = ledger
        self._clock = clock
        self._on_pause = on_pause or (lambda _p: None)
        self._on_call = on_call or (lambda _e: None)

    def _price(self, model: str) -> ModelPrice:
        try:
            return self._cfg.llm.prices_usd_per_mtok[model]
        except KeyError as exc:
            raise Denied(f"no price configured for model {model}") from exc

    def worst_case_nok(self, request: LLMRequest) -> float:
        usage = Usage(approx_tokens(request.system + request.user_content) * 2, request.max_tokens)
        return cost_nok(usage, self._price(request.model), self._cfg.llm.usd_to_nok)

    def check_budget(self, request: LLMRequest) -> None:
        budget = self._cfg.budgets[self._agent]
        day = self._ledger.spent(self._agent, day=oslo_date(self._clock))
        month = self._ledger.spent(self._agent, month=oslo_month(self._clock))
        worst = self.worst_case_nok(request)
        if day + worst > budget.daily_nok:
            self._on_pause(BudgetPaused(self._agent, "daily"))
            raise Denied("budget_daily")
        if month + worst > budget.monthly_nok:
            self._on_pause(BudgetPaused(self._agent, "monthly"))
            raise Denied("budget_monthly")

    def complete(self, request: LLMRequest) -> LLMResponse:
        if request.agent != self._agent:
            raise Denied("llm request for another agent")
        self.check_budget(request)
        response = self._inner.complete(request)
        now = self._clock.now()
        entry = CostEntry(
            ts=now,
            agent=self._agent,
            channel=request.channel,
            model=response.model or request.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read=response.usage.cache_read_input_tokens,
            cache_write=response.usage.cache_creation_input_tokens,
            cost_nok=cost_nok(response.usage, self._price(request.model), self._cfg.llm.usd_to_nok),
            day=oslo_date(self._clock),
            month=oslo_month(self._clock),
        )
        self._ledger.add(entry)
        self._on_call(entry)
        return response
