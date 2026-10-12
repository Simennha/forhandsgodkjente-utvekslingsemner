"""Rule engine: every check, plus property tests for the hard invariants."""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from assistants.common.config import ROOT, AppConfig, load_config
from assistants.events import Mode, Trust
from assistants.killswitch.core import KSState
from assistants.policy import Level, default_policy
from assistants.rules.engine import OPEN_WORKSHOP_TOOLS, Facts, evaluate

POLICY = default_policy()
CFG: AppConfig = load_config(ROOT / "config.example.yaml")


def facts(**kw: object) -> Facts:
    base = Facts(
        agent="charlie",
        tool="time.read",
        trust=Trust.OWNER_PIN,
        mode=Mode.NORMAL,
        killswitch=KSState.ACTIVE,
        lola_enabled=True,
        calls_last_hour=0,
        spent_today_nok=0.0,
        spent_month_nok=0.0,
    )
    return dataclasses.replace(base, **kw)  # type: ignore[arg-type]


def test_engine_is_small_and_has_no_llm() -> None:
    src = (ROOT / "src/assistants/rules/engine.py").read_text()
    assert len(src.splitlines()) < 600
    for word in ("anthropic", "LLMClient", "llm", "httpx", "requests", "psycopg"):
        assert word not in src


@pytest.mark.parametrize("tool", sorted(POLICY.forbidden))
@pytest.mark.parametrize("trust", list(Trust))
def test_level4_always_denied(tool: str, trust: Trust) -> None:
    d = evaluate(facts(tool=tool, trust=trust), POLICY, CFG)
    assert d.outcome == "deny"
    assert d.reason == "level4_never"


def test_unknown_tool_denied() -> None:
    assert evaluate(facts(tool="shell.exec"), POLICY, CFG).reason == "unknown_tool"


@pytest.mark.parametrize("tool", sorted(POLICY.tools_for("charlie")))
def test_lola_never_reaches_charlie_tools(tool: str) -> None:
    d = evaluate(facts(agent="lola", tool=tool, trust=Trust.GUEST), POLICY, CFG)
    assert d.outcome == "deny"
    assert d.reason == "tool_not_for_agent"


@pytest.mark.parametrize("tool", sorted(POLICY.tools_for("lola")))
def test_charlie_does_not_use_lola_tools(tool: str) -> None:
    assert evaluate(facts(tool=tool), POLICY, CFG).reason == "tool_not_for_agent"


def test_known_number_gets_levels_1_and_2_only() -> None:
    assert evaluate(facts(tool="calendar.read", trust=Trust.OWNER_KNOWN), POLICY, CFG).allowed
    assert evaluate(facts(tool="notes.remember", trust=Trust.OWNER_KNOWN), POLICY, CFG).allowed
    d = evaluate(facts(tool="email.send", trust=Trust.OWNER_KNOWN), POLICY, CFG)
    assert d.reason == "pin_required"


def test_pin_gives_level3_with_confirmation() -> None:
    d = evaluate(facts(tool="email.send"), POLICY, CFG)
    assert d.outcome == "confirm"
    assert d.confirm_method == "passkey"


@pytest.mark.parametrize("trust", [Trust.UNKNOWN, Trust.GUEST, Trust.UNTRUSTED])
def test_untrusted_sources_get_nothing(trust: Trust) -> None:
    for tool in POLICY.tools_for("charlie"):
        assert evaluate(facts(tool=tool, trust=trust), POLICY, CFG).outcome == "deny"


def test_duress_only_duress_safe_tools() -> None:
    for name, tool in POLICY.tools_for("charlie").items():
        d = evaluate(facts(tool=name, trust=Trust.DURESS), POLICY, CFG)
        if tool.duress_safe:
            assert d.allowed, name
        else:
            assert d.outcome == "deny"
            assert d.reason == "not_available"  # generic: reveals nothing


def test_system_trust_limited() -> None:
    assert evaluate(facts(tool="report.generate", trust=Trust.SYSTEM), POLICY, CFG).allowed
    assert evaluate(facts(tool="email.send", trust=Trust.SYSTEM), POLICY, CFG).outcome == "deny"


@pytest.mark.parametrize("state", [KSState.PAUSED, KSState.STOPPED])
def test_killswitch_blocks_charlie(state: KSState) -> None:
    assert evaluate(facts(killswitch=state), POLICY, CFG).reason == "killswitch"


def test_lola_runs_when_charlie_paused_but_can_be_turned_off_alone() -> None:
    lola = facts(agent="lola", tool="guest.house_rules_read", trust=Trust.GUEST)
    assert evaluate(dataclasses.replace(lola, killswitch=KSState.PAUSED), POLICY, CFG).allowed
    assert not evaluate(dataclasses.replace(lola, killswitch=KSState.STOPPED), POLICY, CFG).allowed
    assert not evaluate(dataclasses.replace(lola, lola_enabled=False), POLICY, CFG).allowed


def test_budget_caps() -> None:
    assert evaluate(facts(spent_today_nok=100.0), POLICY, CFG).reason == "budget_exhausted"
    assert evaluate(facts(spent_month_nok=1500.0), POLICY, CFG).reason == "budget_exhausted"
    lola = facts(agent="lola", tool="guest.take_message", trust=Trust.GUEST)
    assert (
        evaluate(dataclasses.replace(lola, spent_today_nok=20.0), POLICY, CFG).reason
        == "budget_exhausted"
    )
    assert (
        evaluate(dataclasses.replace(lola, spent_month_nok=300.0), POLICY, CFG).reason
        == "budget_exhausted"
    )


def test_rate_limit() -> None:
    assert (
        evaluate(facts(tool="email.send", calls_last_hour=10), POLICY, CFG).reason == "rate_limited"
    )
    assert evaluate(facts(tool="email.send", calls_last_hour=9), POLICY, CFG).outcome == "confirm"


def test_order_caps_and_box() -> None:
    small = evaluate(facts(tool="inventory.order", amount_nok=4999.0), POLICY, CFG)
    assert small.confirm_method == "passkey"
    big = evaluate(facts(tool="inventory.order", amount_nok=5000.01), POLICY, CFG)
    assert big.confirm_method == "approval_box"
    over = evaluate(
        facts(tool="inventory.order", amount_nok=10.0, orders_month_nok=49_995.0), POLICY, CFG
    )
    assert over.reason == "monthly_order_cap"
    assert (
        evaluate(facts(tool="inventory.order", amount_nok=None), POLICY, CFG).reason
        == "order_amount_missing"
    )


def test_open_workshop_background_only() -> None:
    for name in POLICY.tools_for("charlie"):
        d = evaluate(facts(tool=name, mode=Mode.OPEN_WORKSHOP, amount_nok=10.0), POLICY, CFG)
        if name not in OPEN_WORKSHOP_TOOLS:
            assert d.reason == "open_workshop_background_only", name


def test_lola_max_level_2() -> None:
    for name, tool in POLICY.tools_for("lola").items():
        assert tool.level <= Level.ACT, name


# ------------------------------------------------------------ properties

tools = st.sampled_from(sorted([*POLICY.tools, *POLICY.forbidden, "evil.tool"]))
fact_strategy = st.builds(
    Facts,
    agent=st.sampled_from(["charlie", "lola"]),
    tool=tools,
    trust=st.sampled_from(list(Trust)),
    mode=st.sampled_from(list(Mode)),
    killswitch=st.sampled_from(list(KSState)),
    lola_enabled=st.booleans(),
    calls_last_hour=st.integers(0, 200),
    spent_today_nok=st.floats(0, 500, allow_nan=False),
    spent_month_nok=st.floats(0, 5000, allow_nan=False),
    amount_nok=st.one_of(st.none(), st.floats(-10, 200_000, allow_nan=False)),
    orders_month_nok=st.floats(0, 100_000, allow_nan=False),
)


@settings(max_examples=3000)
@given(fact_strategy)
def test_invariants_hold_for_any_facts(f: Facts) -> None:
    d = evaluate(f, POLICY, CFG)
    budget = CFG.budgets[f.agent]
    if d.outcome == "deny":
        return
    # Never level 4, never an unknown tool, never another agent's tool.
    assert f.tool in POLICY.tools
    assert f.agent in POLICY.tools[f.tool].agents
    # Never above any cap.
    assert f.spent_today_nok < budget.daily_nok
    assert f.spent_month_nok < budget.monthly_nok
    if f.tool == "inventory.order":
        assert f.amount_nok is not None
        assert f.amount_nok > 0
        assert f.orders_month_nok + f.amount_nok <= CFG.orders.monthly_max_nok
        if f.amount_nok > CFG.orders.level3_single_max_nok:
            assert d.confirm_method == "approval_box"
    # Level 3 is never allowed without confirmation, and never without PIN trust.
    if POLICY.tools[f.tool].level == Level.CONFIRM:
        assert d.outcome == "confirm"
        assert f.trust == Trust.OWNER_PIN
    # Charlie only acts while the kill switch is ACTIVE.
    if f.agent == "charlie":
        assert f.killswitch == KSState.ACTIVE
        assert f.trust in (Trust.OWNER_PIN, Trust.OWNER_KNOWN, Trust.DURESS, Trust.SYSTEM)
    else:
        assert f.killswitch != KSState.STOPPED
        assert f.lola_enabled
