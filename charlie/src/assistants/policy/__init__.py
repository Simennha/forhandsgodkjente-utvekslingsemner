"""Loader for policy/levels.yaml - the single source of truth for access levels."""

from __future__ import annotations

from enum import IntEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from assistants.common.config import ROOT
from assistants.common.errors import PolicyError

POLICY_PATH = ROOT / "policy" / "levels.yaml"

AgentName = Literal["charlie", "lola"]


class Level(IntEnum):
    READ = 1
    ACT = 2
    CONFIRM = 3
    NEVER = 4


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: Level
    agents: tuple[AgentName, ...]
    description: str
    confirm: Literal["passkey", "approval_box"] | None = None
    large_confirm: Literal["approval_box"] | None = None
    duress_safe: bool = False


class ForbiddenAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: Literal[4]
    description: str
    patterns: tuple[str, ...]


class DuressPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    allowed_tools_flag: Literal["duress_safe"]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    agents: dict[AgentName, dict[str, str]]
    tools: dict[str, ToolPolicy]
    forbidden: dict[str, ForbiddenAction]
    duress: DuressPolicy

    def tool(self, name: str) -> ToolPolicy:
        try:
            return self.tools[name]
        except KeyError as exc:
            raise PolicyError(f"tool not in policy: {name}") from exc

    def tools_for(self, agent: AgentName) -> dict[str, ToolPolicy]:
        return {n: t for n, t in self.tools.items() if agent in t.agents}

    def duress_tools(self) -> frozenset[str]:
        return frozenset(n for n, t in self.tools.items() if t.duress_safe)

    def validate_invariants(self) -> None:
        for name, tool in self.tools.items():
            if tool.level == Level.NEVER:
                raise PolicyError(f"{name}: level 4 must live under 'forbidden', never 'tools'")
            if tool.level == Level.CONFIRM and tool.confirm is None:
                raise PolicyError(f"{name}: level 3 tool needs a confirm method")
            if tool.level < Level.CONFIRM and (tool.confirm or tool.large_confirm):
                raise PolicyError(f"{name}: only level 3 tools may have confirm methods")
            if tool.duress_safe and tool.level != Level.READ:
                raise PolicyError(f"{name}: duress-safe tools must be level 1")
            if "charlie" in tool.agents and "lola" in tool.agents:
                raise PolicyError(f"{name}: a tool may never be shared by charlie and lola")
            if "lola" in tool.agents and not name.startswith("guest."):
                raise PolicyError(f"{name}: lola tools must live in the guest.* namespace")
        overlap = set(self.tools) & set(self.forbidden)
        if overlap:
            raise PolicyError(f"forbidden actions registered as tools: {sorted(overlap)}")


def load_policy(path: Path | None = None) -> Policy:
    with (path or POLICY_PATH).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    policy = Policy.model_validate(data)
    policy.validate_invariants()
    return policy


@lru_cache(maxsize=1)
def default_policy() -> Policy:
    return load_policy()
