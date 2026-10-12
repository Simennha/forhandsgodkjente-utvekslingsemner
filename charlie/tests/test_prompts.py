"""Section 10: system prompts never grant more access than policy/levels.yaml."""

from __future__ import annotations

import re

import pytest

from assistants.common.redact import contains_secret
from assistants.policy import default_policy
from assistants.prompts import load_prompt

POLICY = default_policy()
TOOL_TOKEN = re.compile(r"\b([a-z_]+\.[a-z_]+)\b")
GRANTING_PHRASES = (
    "full tilgang",
    "alle verktøy",
    "uten bekreftelse",
    "kan overstyre",
    "admin",
    "superbruker",
    "nivå 4 er tillatt",
    "du kan alltid",
    "ignorer reglene",
    "uten pin",
    "du har nøkler",
    "du har tilgang til nøkler",
    "kan slå av logg",
    "kan slette",
    "kan starte maskin",
)


@pytest.mark.parametrize("name", ["charlie", "lola", "extractor"])
def test_prompt_mentions_only_its_own_tools(name: str) -> None:
    text = load_prompt(name)
    allowed = set(POLICY.tools_for("charlie" if name != "lola" else "lola"))
    for token in TOOL_TOKEN.findall(text):
        if token.endswith((".md", ".yaml")):
            continue
        assert token not in POLICY.forbidden, f"{name} names a level-4 action: {token}"
        assert token in allowed, f"{name} names a tool outside its policy: {token}"


@pytest.mark.parametrize("name", ["charlie", "lola", "extractor"])
def test_prompt_contains_no_granting_language_or_secrets(name: str) -> None:
    text = load_prompt(name).lower()
    for phrase in GRANTING_PHRASES:
        assert phrase not in text, f"{name}: {phrase!r}"
    assert not contains_secret(load_prompt(name))


def test_charlie_prompt_rules() -> None:
    text = load_prompt("charlie").lower()
    assert "foreslå før du handler" in text
    assert "nivå" in text
    assert "spør aldri etter pin" in text
    assert "håndheves av systemet" in text
    assert "nivå 4" in text


def test_lola_prompt_rules() -> None:
    text = load_prompt("lola").lower()
    assert "gjesteområdet" in text
    assert "kan ikke love noe på vegne av andre" in text
    assert "ta imot beskjeder" in text
    assert "charlie" not in text  # she does not even know the name of the assistant


def test_extractor_prompt_rules() -> None:
    text = load_prompt("extractor").lower()
    assert "følg aldri" in text
    assert "json" in text
    assert "ingen verktøy" in text


def test_prompts_are_short() -> None:
    for name in ("charlie", "lola", "extractor"):
        assert len(load_prompt(name).split()) < 400
