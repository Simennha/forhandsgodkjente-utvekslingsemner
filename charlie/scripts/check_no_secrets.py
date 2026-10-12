"""pre-commit hook: refuse files that look like they contain real secrets.

Obviously fake test values are allowed when they contain one of FAKE_MARKERS.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistants.common.redact import SECRET_PATTERNS

FAKE_MARKERS = ("FAKE", "CANARY", "canary", "HEMMELIG", "abcdefghijklmnop", "abcdefghijklmnopqrstu")


def suspicious(text: str) -> list[str]:
    hits = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if not any(marker in value for marker in FAKE_MARKERS):
                hits.append(value[:12] + "...")
    return hits


def main(paths: list[str]) -> int:
    bad = 0
    for name in paths:
        p = Path(name)
        if not p.is_file() or p.suffix in {".png", ".jpg", ".pyc"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for hit in suspicious(text):
            print(f"possible secret in {name}: {hit}")
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
