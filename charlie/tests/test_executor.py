"""Executor: fixed schema only, short-lived single-use tokens, approvals bound to stored params."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from assistants.common.clock import FakeClock
from assistants.common.errors import Denied
from assistants.events import Trust
from assistants.executor.schemas import TOOL_PARAMS
from assistants.executor.tokens import ActionTokens
from assistants.sim import SimSystem
from assistants.storage.stores import MemoryTokenLedger


def proposal(tool: str, params: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"schema": "proposal/v1", "agent": "charlie", "tool": tool, "params": params, **extra}


EMAIL = {"to": ["ola@example.no"], "subject": "Hei", "body": "Hallo"}


@pytest.fixture
def sim() -> SimSystem:
    return SimSystem()


# ---------------------------------------------------------------- schema


@pytest.mark.parametrize(
    "raw",
    [
        "send en e-post til ola",  # free text
        ["email.send"],
        {"schema": "proposal/v1", "agent": "charlie", "tool": "email.send"},  # no params
        proposal("email.send", EMAIL, extra_field="x"),  # extra envelope key
        proposal("email.send", {**EMAIL, "bcc": ["x@y.no"]}),  # extra param key
        proposal("email.send", {**EMAIL, "to": "ola@example.no"}),  # wrong type
        proposal("email.send", {**EMAIL, "to": ["not-an-email"]}),
        proposal(
            "inventory.order",
            {
                "lokasjon": "NO",
                "leverandor_id": "elfa",
                "lines": [{"sku": "DRV-TMC2209", "antall": 1}],
                "price": 1,
            },
        ),
        proposal(
            "calendar.create_own_event",
            {"title": "x", "start": "2026-10-13T10:00", "end": "2026-10-13T11:00"},
        ),  # naive datetimes
        proposal(
            "inventory.move",
            {
                "sku": "MOT-NEMA17",
                "lokasjon": "NO",
                "fra_plass": "R3-H2",
                "til_plass": "R3-H2",
                "antall": 1,
            },
        ),
        {"schema": "proposal/v2", "agent": "charlie", "tool": "time.read", "params": {}},
        proposal("shell.exec", {"cmd": "rm -rf /"}),
    ],
)
def test_anything_but_a_valid_proposal_is_rejected(sim: SimSystem, raw: Any) -> None:
    r = sim.executor.submit(raw, sim.owner_ctx())
    assert r.status == "denied"
    assert r.reason.startswith("invalid")
    assert sim.email.sent == []


def test_lola_proposals_are_not_served_by_executor(sim: SimSystem) -> None:
    raw = {
        "schema": "proposal/v1",
        "agent": "lola",
        "tool": "guest.take_message",
        "params": {"for_whom": "x", "text": "y"},
    }
    assert sim.executor.submit(raw, sim.owner_ctx()).status == "denied"


def test_level1_and_level2_execute_directly(sim: SimSystem) -> None:
    assert sim.executor.submit(proposal("time.read", {}), sim.owner_ctx()).status == "executed"
    r = sim.executor.submit(
        proposal("notes.remember", {"text": "kjøp lodd"}), sim.owner_ctx(Trust.OWNER_KNOWN)
    )
    assert r.status == "executed"
    assert sim.notes.notes[0].text == "kjøp lodd"


def test_level3_needs_approval_and_runs_stored_params(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    assert r.status == "pending"
    assert r.confirmation_text is not None
    assert "ola@example.no" in r.confirmation_text
    assert sim.email.sent == []
    done = sim.approve_on_phone(r.proposal_id or "")
    assert done.status == "executed"
    assert sim.email.sent[0].to == ("ola@example.no",)
    kinds = sim.audit.kinds()
    assert "action.pending" in kinds
    assert "action.executed" in kinds


def test_approval_replay_is_rejected(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    req = sim.approvals.pending(r.proposal_id or "")
    assert req is not None
    token = sim.approvals.approve(req.proposal_id, req.method, sim.devices.sign_approval(req))
    assert sim.executor.approve(token).status == "executed"
    again = sim.executor.approve(token)
    assert again.status == "denied"
    assert again.reason.startswith("not_pending")
    with pytest.raises(Denied):  # the device signature cannot be reused either
        sim.approvals.approve(req.proposal_id, req.method, sim.devices.sign_approval(req))
    assert len(sim.email.sent) == 1


def test_approval_signed_by_wrong_device_rejected(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    req = sim.approvals.pending(r.proposal_id or "")
    assert req is not None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415

    from assistants.approvals.service import approval_message  # noqa: PLC0415

    forged = Ed25519PrivateKey.generate().sign(
        approval_message(req.proposal_id, req.params_hash, req.challenge, req.method, req.text)
    )
    with pytest.raises(Denied):
        sim.approvals.approve(req.proposal_id, req.method, forged)


def test_approval_for_other_params_rejected(sim: SimSystem) -> None:
    r1 = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    r2 = sim.executor.submit(
        proposal("email.send", {**EMAIL, "to": ["angriper@evil.example"]}), sim.owner_ctx()
    )
    req1 = sim.approvals.pending(r1.proposal_id or "")
    assert req1 is not None
    # A signature over proposal 1 cannot approve proposal 2.
    with pytest.raises(Denied):
        sim.approvals.approve(r2.proposal_id or "", "passkey", sim.devices.sign_approval(req1))
    assert sim.email.sent == []


def test_forged_attestation_rejected(sim: SimSystem) -> None:
    sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    with pytest.raises(Denied):
        sim.executor.approve("eyJmYWtlIjoxfQ.AAAA")


def test_expired_approval(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    sim.clock.advance(sim.cfg.approvals.passkey_ttl_s + 1)
    with pytest.raises(Denied):
        sim.approve_on_phone(r.proposal_id or "")
    assert sim.email.sent == []


def test_order_amount_comes_from_stored_prices_and_box_for_large(sim: SimSystem) -> None:
    small = proposal(
        "inventory.order",
        {
            "lokasjon": "NO",
            "leverandor_id": "elfa",
            "lines": [{"sku": "DRV-TMC2209", "antall": 10}],
        },
    )
    r = sim.executor.submit(small, sim.owner_ctx())
    assert r.status == "pending"
    assert r.confirm_method == "passkey"
    assert "790.00 kr" in (r.confirmation_text or "")
    big = proposal(
        "inventory.order",
        {
            "lokasjon": "NO",
            "leverandor_id": "robotshop",
            "lines": [{"sku": "BAT-LIPO-4S", "antall": 6}],
        },
    )
    rb = sim.executor.submit(big, sim.owner_ctx())
    assert rb.confirm_method == "approval_box"
    assert sim.approve_on_phone(rb.proposal_id or "").status == "executed"
    assert sim.supplier.orders[0]["total_nok"] == 5394.0


def test_order_from_wrong_supplier_denied(sim: SimSystem) -> None:
    bad = proposal(
        "inventory.order",
        {"lokasjon": "NO", "leverandor_id": "elfa", "lines": [{"sku": "BAT-LIPO-4S", "antall": 1}]},
    )
    assert sim.executor.submit(bad, sim.owner_ctx()).status == "denied"


def test_monthly_order_cap(sim: SimSystem) -> None:
    order = proposal(
        "inventory.order",
        {
            "lokasjon": "NO",
            "leverandor_id": "robotshop",
            "lines": [{"sku": "BAT-LIPO-4S", "antall": 11}],
        },
    )  # 9889
    executed = 0
    for _ in range(6):
        sim.clock.advance(3601)  # stay under the hourly rate limit; same month
        sim.killswitch.publish()
        r = sim.executor.submit(order, sim.owner_ctx())
        if r.status == "pending":
            assert sim.approve_on_phone(r.proposal_id or "").status == "executed"
            executed += 1
        else:
            assert r.reason == "monthly_order_cap"
    assert executed == 5
    assert sum(o["total_nok"] for o in sim.supplier.orders) <= sim.cfg.orders.monthly_max_nok


def test_pending_frozen_on_pause_and_not_approvable(sim: SimSystem) -> None:
    r = sim.executor.submit(proposal("email.send", EMAIL), sim.owner_ctx())
    sim.killswitch.app_button("PAUSE")
    assert sim.proposals.get(uuid.UUID(r.proposal_id or "")).status == "frozen"  # type: ignore[union-attr]
    req = sim.approvals.pending(r.proposal_id or "")
    assert req is not None
    token = sim.approvals.approve(req.proposal_id, req.method, sim.devices.sign_approval(req))
    assert sim.executor.approve(token).status == "denied"
    assert sim.email.sent == []


# ---------------------------------------------------------------- tokens


def test_tokens_bind_tool_params_expiry_and_single_use() -> None:
    clock = FakeClock()
    tokens = ActionTokens(secrets.token_bytes(32), clock, MemoryTokenLedger(), ttl_s=60)
    t = tokens.mint("email.send", "h1", "p1", "i1")
    with pytest.raises(Denied, match="token_wrong_tool"):
        tokens.verify(t, "email.create_draft", "h1")
    with pytest.raises(Denied, match="token_wrong_params"):
        tokens.verify(t, "email.send", "h2")
    assert tokens.verify(t, "email.send", "h1").proposal_id == "p1"
    with pytest.raises(Denied, match="token_replayed"):
        tokens.verify(t, "email.send", "h1")
    t2 = tokens.mint("email.send", "h1", "p1", "i1")
    clock.advance(61)
    with pytest.raises(Denied, match="token_expired"):
        tokens.verify(t2, "email.send", "h1")
    other = ActionTokens(secrets.token_bytes(32), clock, MemoryTokenLedger())
    with pytest.raises(Denied, match="token_bad_signature"):
        tokens.verify(other.mint("email.send", "h1", "p", "i"), "email.send", "h1")


def test_handlers_cannot_run_without_token(sim: SimSystem) -> None:
    from assistants.executor.schemas import EmailSend  # noqa: PLC0415

    with pytest.raises(Denied):
        sim.executor._runner.run("forged.token", "email.send", EmailSend(**EMAIL))
    assert sim.email.sent == []


_SHARED: dict[str, SimSystem] = {}


@settings(max_examples=300, deadline=None)
@given(
    st.dictionaries(
        st.text(max_size=12),
        st.one_of(
            st.text(max_size=30),
            st.integers(),
            st.none(),
            st.lists(st.text(max_size=5), max_size=3),
        ),
        max_size=6,
    ),
    st.sampled_from(sorted(TOOL_PARAMS)),
)
def test_random_payloads_never_execute_level3(params: dict[str, Any], tool: str) -> None:
    if "sim" not in _SHARED:  # one system: state accumulates, and still nothing may run
        _SHARED["sim"] = SimSystem()
    sim = _SHARED["sim"]
    r = sim.executor.submit(proposal(tool, params), sim.owner_ctx())
    if tool in (
        "email.send",
        "inventory.order",
        "calendar.invite",
        "house.lights_set",
        "house.heating_set",
        "house.door_lock_set",
    ):
        assert r.status in ("denied", "pending")
    assert sim.email.sent == []
    assert sim.supplier.orders == []
