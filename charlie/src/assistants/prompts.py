"""System prompts live in prompts/*.md (Norwegian). Loaded verbatim."""

from __future__ import annotations

from assistants.common.config import ROOT


def load_prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
