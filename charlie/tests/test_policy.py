"""Policy file invariants (Phase 0). Registry coverage is checked in test_tool_registry.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from assistants.common.errors import PolicyError
from assistants.policy import Level, load_policy


def test_policy_loads_and_every_tool_has_a_level() -> None:
    policy = load_policy()
    assert policy.tools, "policy must define tools"
    for name, tool in policy.tools.items():
        assert tool.level in (Level.READ, Level.ACT, Level.CONFIRM), name
    for name, action in policy.forbidden.items():
        assert action.level == 4, name
        assert action.patterns, f"{name} needs scan patterns"


def test_level4_never_in_tools() -> None:
    policy = load_policy()
    assert not set(policy.tools) & set(policy.forbidden)
    assert all(t.level != Level.NEVER for t in policy.tools.values())


def test_required_level4_actions_listed() -> None:
    policy = load_policy()
    assert {
        "backup.delete",
        "keys.change",
        "firewall.change",
        "machine.start",
        "money.transfer",
        "alarm.disable",
    } <= set(policy.forbidden)


def test_lola_and_charlie_never_share_tools() -> None:
    policy = load_policy()
    lola = set(policy.tools_for("lola"))
    charlie = set(policy.tools_for("charlie"))
    assert lola
    assert charlie
    assert not lola & charlie
    assert all(t.startswith("guest.") for t in lola)


@pytest.mark.parametrize(
    ("tool", "patch"),
    [
        ("evil", {"level": 4, "agents": ["charlie"], "description": "x"}),
        ("email.send", {"level": 3, "agents": ["charlie"], "description": "no confirm"}),
        ("x.read", {"level": 1, "agents": ["charlie", "lola"], "description": "shared"}),
        ("x.lola", {"level": 1, "agents": ["lola"], "description": "outside guest ns"}),
        ("x.duress", {"level": 2, "agents": ["charlie"], "description": "d", "duress_safe": True}),
        ("backup.delete", {"level": 1, "agents": ["charlie"], "description": "sneaky"}),
    ],
)
def test_invalid_policies_rejected(tmp_path: Path, tool: str, patch: dict[str, object]) -> None:
    from assistants.policy import POLICY_PATH  # noqa: PLC0415

    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    data["tools"][tool] = patch
    path = tmp_path / "levels.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises((PolicyError, ValueError)):
        load_policy(path)


def test_config_example_loads() -> None:
    from assistants.common.config import load_config  # noqa: PLC0415

    cfg = load_config()
    assert cfg.budgets["charlie"].daily_nok == 100
    assert cfg.budgets["lola"].monthly_nok == 300
    assert cfg.orders.level3_single_max_nok == 5000
    assert cfg.orders.monthly_max_nok == 50000
    assert cfg.llm.router_model == "claude-haiku-5-5"
    assert cfg.llm.hard_model == "claude-sonnet-5-5"
    assert cfg.locale.timezone == "Europe/Oslo"
