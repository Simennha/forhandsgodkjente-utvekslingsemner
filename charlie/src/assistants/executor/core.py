"""Executor: the only component that can act.

``submit`` takes a proposal in the fixed JSON format, validates it against the schema,
asks the rule engine, and then either runs it (levels 1-2), stores it pending approval
(level 3) or denies it. ``approve`` takes an attestation from the approvals service and
runs the STORED proposal - parameters can never be swapped after confirmation.
Everything is written to the audit log.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from assistants.approvals.service import ApprovalRequest, AttestationVerifier
from assistants.audit import AuditLog
from assistants.common.canonical import digest
from assistants.common.clock import Clock, oslo_date, oslo_month
from assistants.common.config import AppConfig
from assistants.common.errors import Denied, ValidationFailed
from assistants.events import Mode, Trust
from assistants.executor import schemas as s
from assistants.executor.confirmation import render_confirmation
from assistants.executor.handlers import ToolRunner
from assistants.executor.tokens import ActionTokens
from assistants.inventory.repo import InventoryRepo
from assistants.killswitch.core import KSState
from assistants.policy import Policy
from assistants.rules.engine import Decision, Facts
from assistants.storage.stores import CostLedger, ProposalStore, StoredProposal, ToolCallLog

Status = Literal["executed", "pending", "denied", "failed"]
RuleChecker = Callable[[Facts], Decision]
KillSwitchView = Callable[[], tuple[KSState, bool]]


@dataclass(frozen=True)
class CallContext:
    trust: Trust
    mode: Mode
    channel: str
    session_id: str | None = None


@dataclass(frozen=True)
class SubmitResult:
    status: Status
    tool: str
    level: int
    reason: str
    proposal_id: str | None = None
    result: dict[str, Any] | None = None
    confirm_method: str | None = None
    confirmation_text: str | None = None


class Executor:
    def __init__(
        self,
        policy: Policy,
        cfg: AppConfig,
        clock: Clock,
        rules: RuleChecker,
        killswitch: KillSwitchView,
        mode_view: Callable[[], Mode],
        proposals: ProposalStore,
        costs: CostLedger,
        calls: ToolCallLog,
        audit: AuditLog,
        tokens: ActionTokens,
        runner: ToolRunner,
        inventory: InventoryRepo,
        request_approval: Callable[[ApprovalRequest], None],
        attestations: AttestationVerifier,
        on_denied: Callable[[str, str], None] | None = None,
        active_since: Callable[[], float] | None = None,
    ) -> None:
        self._active_since = active_since or (lambda: 0.0)
        self._approve_lock = threading.Lock()
        self._policy = policy
        self._cfg = cfg
        self._clock = clock
        self._rules = rules
        self._ks = killswitch
        self._mode = mode_view
        self._proposals = proposals
        self._costs = costs
        self._calls = calls
        self._audit = audit
        self._tokens = tokens
        self._runner = runner
        self._inventory = inventory
        self._request_approval = request_approval
        self._attestations = attestations
        self._on_denied = on_denied or (lambda _tool, _reason: None)

    # ------------------------------------------------------------ facts
    def _facts(
        self,
        agent: Literal["charlie", "lola"],
        tool: str,
        ctx: CallContext,
        amount: float | None,
        recheck: bool = False,
    ) -> Facts:
        state, lola_enabled = self._ks()
        now = self._clock.now()
        return Facts(
            agent=agent,
            tool=tool,
            trust=ctx.trust,
            mode=ctx.mode,
            killswitch=state,
            lola_enabled=lola_enabled,
            # On re-check after approval, the proposal's own submission is not counted twice.
            calls_last_hour=max(
                0, self._calls.count_since(agent, tool, now - timedelta(hours=1)) - int(recheck)
            ),
            spent_today_nok=self._costs.spent(agent, day=oslo_date(self._clock)),
            spent_month_nok=self._costs.spent(agent, month=oslo_month(self._clock)),
            amount_nok=amount,
            orders_month_nok=self._proposals.orders_total(oslo_month(self._clock)),
        )

    def _amount(self, params: s.Params) -> float | None:
        if isinstance(params, s.InventoryOrder):
            return self._inventory.price_order(
                params.lokasjon, params.leverandor_id, [(x.sku, x.antall) for x in params.lines]
            )
        return None

    def _deny(self, tool: str, level: int, reason: str, ctx: CallContext) -> SubmitResult:
        self._audit.append(
            "executor",
            "action.denied",
            {"tool": tool, "level": level, "reason": reason, "channel": ctx.channel},
        )
        self._on_denied(tool, reason)
        return SubmitResult("denied", tool, level, reason)

    # ------------------------------------------------------------ submit
    def submit(self, raw: Any, ctx: CallContext) -> SubmitResult:
        try:
            proposal, params = s.parse_proposal(raw)
        except ValidationFailed as exc:
            tool = str(raw.get("tool", "?"))[:60] if isinstance(raw, dict) else "?"
            level = 4 if tool in self._policy.forbidden else 0
            return self._deny(tool, level, f"invalid:{exc}", ctx)
        if proposal.agent != "charlie":
            return self._deny(proposal.tool, 0, "executor_serves_charlie_only", ctx)
        try:
            amount = self._amount(params)
        except Denied as exc:
            return self._deny(proposal.tool, 3, exc.reason, ctx)
        decision = self._rules(self._facts("charlie", proposal.tool, ctx, amount))
        if decision.outcome == "deny":
            return self._deny(proposal.tool, decision.level, decision.reason, ctx)

        pj = s.params_json(params)
        ph = digest(pj)
        pid = uuid.uuid4()
        now = self._clock.now()
        self._calls.record("charlie", proposal.tool, now)

        if decision.outcome == "allow":
            stored = StoredProposal(
                pid,
                now,
                "charlie",
                proposal.tool,
                pj,
                ph,
                decision.level,
                "approved",
                None,
                None,
                amount,
                None,
                secrets.token_hex(16),
                approved_at=now,
            )
            self._proposals.save(stored)
            return self._execute(stored, params, ctx.channel)

        # Level 3: store and ask for confirmation. Text is rendered from stored params.
        method = decision.confirm_method or "passkey"
        ttl = (
            self._cfg.approvals.box_ttl_s
            if method == "approval_box"
            else self._cfg.approvals.passkey_ttl_s
        )
        challenge = secrets.token_urlsafe(24)
        text = render_confirmation(proposal.tool, params, amount)
        stored = StoredProposal(
            pid,
            now,
            "charlie",
            proposal.tool,
            pj,
            ph,
            decision.level,
            "pending",
            method,
            challenge,
            amount,
            now + timedelta(seconds=ttl),
            secrets.token_hex(16),
        )
        self._proposals.save(stored)
        self._request_approval(
            ApprovalRequest(str(pid), ph, challenge, method, text, now + timedelta(seconds=ttl))
        )
        self._audit.append(
            "executor",
            "action.pending",
            {
                "tool": proposal.tool,
                "proposal_id": str(pid),
                "method": method,
                "amount_nok": amount,
                "channel": ctx.channel,
            },
        )
        return SubmitResult(
            "pending",
            proposal.tool,
            decision.level,
            "needs_confirmation",
            str(pid),
            None,
            method,
            text,
        )

    # ------------------------------------------------------------ approve
    def approve(self, attestation_token: str) -> SubmitResult:
        att = self._attestations.verify(attestation_token)
        try:
            pid = uuid.UUID(att.proposal_id)
        except ValueError as exc:
            raise Denied("bad_proposal_id") from exc
        stored = self._proposals.get(pid)
        if stored is None:
            raise Denied("unknown_proposal")
        ctx = CallContext(Trust.OWNER_PIN, self._mode(), "approval")
        if stored.status != "pending":
            return self._deny(stored.tool, stored.level, f"not_pending:{stored.status}", ctx)
        if (
            att.params_hash != stored.params_hash
            or att.challenge != stored.challenge
            or att.method != stored.confirm_method
        ):
            return self._deny(stored.tool, stored.level, "attestation_mismatch", ctx)
        if stored.expires_at is None or self._clock.now() >= stored.expires_at:
            self._proposals.transition(pid, "pending", "expired")
            return self._deny(stored.tool, stored.level, "expired", ctx)
        state, _ = self._ks()
        # A pause - even just a lost lease - after the proposal was made freezes it for good
        # (red team #12): it must be proposed again after the owner has had a look.
        if state != KSState.ACTIVE or self._active_since() > stored.created_at.timestamp():
            self._proposals.transition(pid, "pending", "frozen")
            return self._deny(stored.tool, stored.level, "killswitch", ctx)
        params = s.TOOL_PARAMS[stored.tool].model_validate(stored.params)
        # Re-check and claim under one lock, so two approvals at the same time cannot both
        # pass the caps (red team #8).
        with self._approve_lock:
            decision = self._rules(
                self._facts("charlie", stored.tool, ctx, stored.amount_nok, recheck=True)
            )
            if decision.outcome != "confirm" or decision.confirm_method != stored.confirm_method:
                self._proposals.transition(pid, "pending", "denied")
                return self._deny(stored.tool, stored.level, f"recheck:{decision.reason}", ctx)
            # Compare-and-set: only one approval can ever move pending -> approved.
            if not self._proposals.transition(pid, "pending", "approved", at=self._clock.now()):
                return self._deny(stored.tool, stored.level, "replay", ctx)
        return self._execute(stored, params, "approval")

    # ------------------------------------------------------------ execute
    def _execute(self, stored: StoredProposal, params: s.Params, channel: str) -> SubmitResult:
        token = self._tokens.mint(
            stored.tool, stored.params_hash, str(stored.id), stored.idempotency_key
        )
        try:
            result = self._runner.run(token, stored.tool, params)
        except Denied as exc:
            self._proposals.transition(stored.id, "approved", "failed", {"error": exc.reason})
            self._audit.append(
                "executor",
                "action.failed",
                {"tool": stored.tool, "proposal_id": str(stored.id), "reason": exc.reason},
            )
            return SubmitResult("failed", stored.tool, stored.level, exc.reason, str(stored.id))
        except Exception as exc:  # noqa: BLE001 - adapters may fail in many ways
            self._proposals.transition(
                stored.id, "approved", "failed", {"error": type(exc).__name__}
            )
            self._audit.append(
                "executor",
                "action.failed",
                {"tool": stored.tool, "proposal_id": str(stored.id), "reason": type(exc).__name__},
            )
            return SubmitResult(
                "failed", stored.tool, stored.level, type(exc).__name__, str(stored.id)
            )
        self._proposals.transition(stored.id, "approved", "executed", {"ok": True})
        self._audit.append(
            "executor",
            "action.executed",
            {
                "tool": stored.tool,
                "proposal_id": str(stored.id),
                "level": stored.level,
                "channel": channel,
                "amount_nok": stored.amount_nok,
            },
        )
        return SubmitResult("executed", stored.tool, stored.level, "ok", str(stored.id), result)

    # ------------------------------------------------------------ kill switch
    def freeze_all(self) -> int:
        n = self._proposals.freeze_pending()
        self._audit.append("executor", "proposals.frozen", {"count": n})
        return n
