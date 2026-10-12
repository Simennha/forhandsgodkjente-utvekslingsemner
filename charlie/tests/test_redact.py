from __future__ import annotations

from assistants.common.redact import REDACTED, redact, redact_text, register_secret


def test_patterns_are_redacted() -> None:
    text = "key sk-ant-api03-abcdefghijklmnop and Bearer abcdefghijklmnopqrstuv"
    out = redact_text(text)
    assert "sk-ant-" not in out
    assert "abcdefghijklmnopqrstuv" not in out


def test_registered_values_are_redacted() -> None:
    register_secret("supersecretvalue42")
    assert redact_text("x supersecretvalue42 y") == f"x {REDACTED} y"


def test_sensitive_keys_are_redacted() -> None:
    out = redact({"pin": "123456", "nested": {"api_key": "x", "ok": "fine"}, "list": ["a"]})
    assert out == {"pin": REDACTED, "nested": {"api_key": REDACTED, "ok": "fine"}, "list": ["a"]}
