"""Every tool has exactly one level, one schema and (Charlie side) one handler."""

from __future__ import annotations

from assistants.agents.lola import ACTION_TOOL
from assistants.executor.handlers import HANDLERS
from assistants.executor.schemas import TOOL_PARAMS
from assistants.policy import default_policy

POLICY = default_policy()


def test_every_registered_tool_has_a_level() -> None:
    missing = set(TOOL_PARAMS) - set(POLICY.tools)
    assert not missing, f"tools without level in policy/levels.yaml: {missing}"
    missing_h = set(HANDLERS) - set(POLICY.tools)
    assert not missing_h, f"handlers without level: {missing_h}"


def test_every_policy_tool_has_a_schema() -> None:
    assert set(POLICY.tools) == set(TOOL_PARAMS)


def test_charlie_tools_have_handlers_lola_tools_do_not() -> None:
    assert set(HANDLERS) == set(POLICY.tools_for("charlie"))
    assert not set(HANDLERS) & set(POLICY.tools_for("lola"))


def test_lola_actions_map_only_to_lola_tools() -> None:
    assert set(ACTION_TOOL.values()) <= set(POLICY.tools_for("lola"))
