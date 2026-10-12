"""The adversarial corpus as pytest cases (same runner as `make eval`)."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from assistants.eval.runner import HARD, load_corpus, run_case

CASES = load_corpus()
REQUIRED_CATEGORIES = {
    "niva4",
    "identitet",
    "injeksjon_epost",
    "hast",
    "trusler",
    "lola_lekkasje",
    "logg_av",
}


def test_corpus_shape() -> None:
    assert len(CASES) >= 50
    assert len({c["id"] for c in CASES}) == len(CASES)
    counts = Counter(c["kategori"] for c in CASES)
    assert set(counts) == REQUIRED_CATEGORIES
    assert min(counts.values()) >= 6
    for c in CASES:
        assert c["forventet"], c["id"]
        assert any(e in HARD for e in c["forventet"]), c["id"]


@pytest.mark.parametrize("mode", ["obedient", "compromised"])
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case: dict[str, Any], mode: str) -> None:
    result = run_case(case, mode, None)
    assert result.hard_failures == [], result.replies
    if mode == "obedient":
        assert result.soft_failures == [], result.replies
