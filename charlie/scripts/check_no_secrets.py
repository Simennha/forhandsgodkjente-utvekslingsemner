"""pre-commit hook: refuse files that look like they contain real secrets."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistants.common.redact import redact_text

ALLOWED = {"tests/test_redact.py"}


def main(paths: list[str]) -> int:
    bad = []
    for name in paths:
        p = Path(name)
        if name in ALLOWED or not p.is_file() or p.suffix in {".png", ".jpg"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if redact_text(text) != text:
            bad.append(name)
    for name in bad:
        print(f"possible secret in {name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
