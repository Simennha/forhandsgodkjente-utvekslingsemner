"""Rule engine: level, caps and rate for EVERY tool call. Pure code, no LLM, no I/O.

Read it top to bottom: ``evaluate`` applies the checks in a fixed order and returns at
the first one that fails. The caller (the executor) supplies the facts - kill-switch
state from the signed lease, spend from the cost ledger, call counts from the tool log,
and order amounts computed from stored prices (never from model text).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from assistants.common.config import AppConfig
from assistants.events import Mode, Trust
from assistants.killswitch.core import KSState
from assistants.policy import Level, Policy

Outcome = Literal["allow", "confirm", "deny"]
ConfirmMethod = Literal["passkey", "approval_box"]

# Tools Charlie may still use in open-workshop mode: background work only
# (inventory, orders, sensors). No calendar, e-mail, house or anything conversational.
OPEN_WORKSHOP_TOOLS = frozenset(
    {
        "inventory.read",
        "inventory.suggestions",
        "inventory.move",
        "inventory.order",
        "sensors.read",
        "workshop.sensor_log",
        "status.read",
        "time.read",
        "alert.notify_owner",
        "report.generate",
    }
)

# Scheduled jobs (Trust.SYSTEM) may only read and do level-2 bookkeeping.
SYSTEM_TOOLS = frozenset(
    {
        "inventory.read",
        "inventory.suggestions",
        "sensors.read",
        "status.read",
        "time.read",
        "calendar.read",
        "notes.read",
        "project.status_read",
        "lola_queue.read",
        "workshop.sensor_log",
        "report.generate",
        "alert.notify_owner",
    }
)


@dataclass(frozen=True)
class Facts:
    """Everything the engine needs to decide. Collected by the caller."""

    agent: Literal["charlie", "lola"]
    tool: str
    trust: Trust
    mode: Mode
    killswitch: KSState
    lola_enabled: bool
    calls_last_hour: int
    spent_today_nok: float
    spent_month_nok: float
    amount_nok: float | None = None  # orders only, computed from DB prices
    orders_month_nok: float = 0.0  # executed + pending-approved orders this month


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    level: int
    confirm_method: ConfirmMethod | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


def _deny(reason: str, level: int = 0) -> Decision:
    return Decision("deny", reason, level)


def evaluate(f: Facts, policy: Policy, cfg: AppConfig) -> Decision:
    # 1. Level 4 is never allowed and has no implementation anywhere.
    if f.tool in policy.forbidden:
        return _deny("level4_never", 4)

    # 2. Unknown tools do not exist.
    tool = policy.tools.get(f.tool)
    if tool is None:
        return _deny("unknown_tool")
    level = int(tool.level)

    # 3. A tool belongs to exactly one agent. Lola can never reach Charlie's tools.
    if f.agent not in tool.agents:
        return _deny("tool_not_for_agent", level)

    # 4. Kill switch (fail-closed lease state supplied by caller).
    if f.agent == "charlie" and f.killswitch != KSState.ACTIVE:
        return _deny("killswitch", level)
    if f.agent == "lola" and (f.killswitch == KSState.STOPPED or not f.lola_enabled):
        return _deny("killswitch", level)

    # 5. Hard budget per agent per day and month.
    budget = cfg.budgets[f.agent]
    if f.spent_today_nok >= budget.daily_nok or f.spent_month_nok >= budget.monthly_nok:
        return _deny("budget_exhausted", level)

    # 6. Rate limit per tool per hour.
    limits = cfg.rate_limits.tool_calls_per_hour
    if f.calls_last_hour >= limits.get(f.tool, limits.get("default", 60)):
        return _deny("rate_limited", level)

    # 7. Who is asking. Trust comes from the gateway, never from content.
    if f.agent == "lola":
        if f.trust not in (Trust.GUEST, Trust.UNTRUSTED):
            return _deny("bad_trust_for_lola", level)
        if level > Level.ACT:
            return _deny("lola_max_level_2", level)
        return Decision("allow", "ok", level)

    trust_decision = _check_charlie_trust(f, tool.duress_safe, level)
    if trust_decision is not None:
        return trust_decision

    # 8. Workshop mode.
    if f.mode == Mode.OPEN_WORKSHOP and f.tool not in OPEN_WORKSHOP_TOOLS:
        return _deny("open_workshop_background_only", level)
    if f.mode == Mode.GUEST and not tool.duress_safe:
        return _deny("guest_mode", level)

    # 9. Levels 1 and 2 are allowed here.
    if level <= Level.ACT:
        return Decision("allow", "ok", level)

    # 10. Level 3: always confirmed; orders checked against caps first.
    method: ConfirmMethod = tool.confirm or "passkey"
    if f.tool in cfg.approvals.approval_box_tools:
        method = "approval_box"
    if f.tool == "inventory.order":
        if f.amount_nok is None or f.amount_nok <= 0:
            return _deny("order_amount_missing", level)
        if f.orders_month_nok + f.amount_nok > cfg.orders.monthly_max_nok:
            return _deny("monthly_order_cap", level)
        if f.amount_nok > cfg.orders.level3_single_max_nok:
            method = tool.large_confirm or "approval_box"
    return Decision("confirm", "needs_confirmation", level, method)


def _check_charlie_trust(f: Facts, duress_safe: bool, level: int) -> Decision | None:
    match f.trust:
        case Trust.DURESS:
            # Same generic reason as any other unavailability: nothing reveals duress.
            return None if duress_safe else _deny("not_available", level)
        case Trust.OWNER_PIN:
            return None
        case Trust.OWNER_KNOWN:
            return None if level <= Level.ACT else _deny("pin_required", level)
        case Trust.SYSTEM:
            return None if f.tool in SYSTEM_TOOLS else _deny("not_for_system", level)
        case Trust.GUEST | Trust.UNKNOWN | Trust.UNTRUSTED:
            return _deny("untrusted_source", level)
