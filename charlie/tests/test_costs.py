"""Real token cost accounting and hard caps that pause the agent."""

from __future__ import annotations

import pytest

from assistants.adapters.llm import LLMRequest, Usage
from assistants.common.errors import Denied
from assistants.ops.costs import MeteredLLM, cost_nok
from assistants.sim import SimSystem


def test_cost_formula_uses_config_prices() -> None:
    sim = SimSystem()
    price = sim.cfg.llm.prices_usd_per_mtok["claude-sonnet-5-5"]
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=500_000)
    expected = (2.0 + 1.0 + 0.1) * sim.cfg.llm.usd_to_nok
    assert cost_nok(usage, price, sim.cfg.llm.usd_to_nok) == pytest.approx(expected)


def test_ledger_records_per_agent_and_channel() -> None:
    sim = SimSystem()
    owner = sim.cfg.identity.owner_numbers[0]
    sim.call("charlie", owner, ["Hva er klokka?"])
    sim.gateway.guest_chat("Når er det åpent?", "g")
    assert sim.costs.spent("charlie") > 0
    assert sim.lola_costs.spent("lola") > 0
    assert {e.channel for e in sim.costs.entries} == {"phone"}
    assert {e.channel for e in sim.lola_costs.entries} == {"chat_guest"}
    assert sim.costs.spent("lola") == 0  # separate ledgers, separate caps


def test_daily_cap_pauses_charlie() -> None:
    sim = SimSystem()
    paused: list[str] = []
    llm = MeteredLLM(
        sim.raw_llm,
        "charlie",
        sim.cfg,
        sim.costs,
        sim.clock,
        on_pause=lambda p: paused.append(p.reason),
    )
    huge = LLMRequest("charlie", "hard", "claude-sonnet-5-5", "x" * 20_000_000, "y", 100_000)
    with pytest.raises(Denied, match="budget_daily"):
        llm.complete(huge)
    assert paused == ["daily"]
    assert sim.costs.entries == []


def test_cap_reached_means_no_more_calls_and_polite_reply() -> None:
    sim = SimSystem()
    from assistants.common.clock import oslo_date, oslo_month  # noqa: PLC0415
    from assistants.storage.stores import CostEntry  # noqa: PLC0415

    sim.costs.add(
        CostEntry(
            sim.clock.now(),
            "charlie",
            "phone",
            "claude-haiku-5-5",
            0,
            0,
            0,
            0,
            100.0,
            oslo_date(sim.clock),
            oslo_month(sim.clock),
        )
    )
    n = len(sim.raw_llm.calls)  # type: ignore[attr-defined]
    said = sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?"])
    assert "får jeg ikke" in said[-1]
    assert len(sim.raw_llm.calls) == n  # type: ignore[attr-defined]


def test_lola_cap_is_separate_and_lower() -> None:
    sim = SimSystem()
    from assistants.common.clock import oslo_date, oslo_month  # noqa: PLC0415
    from assistants.storage.stores import CostEntry  # noqa: PLC0415

    sim.lola_costs.add(
        CostEntry(
            sim.clock.now(),
            "lola",
            "door",
            "claude-haiku-5-5",
            0,
            0,
            0,
            0,
            20.0,
            oslo_date(sim.clock),
            oslo_month(sim.clock),
        )
    )
    assert "ikke tilgjengelig" in sim.gateway.guest_chat("Hei", "g")
    said = sim.call("charlie", sim.cfg.identity.owner_numbers[0], ["Hva er klokka?"])
    assert "Klokka er" in said[-1]


def test_llm_request_for_other_agent_refused() -> None:
    sim = SimSystem()
    with pytest.raises(Denied):
        sim.lola_llm.complete(LLMRequest("charlie", "router", "claude-haiku-5-5", "s", "u", 10))
