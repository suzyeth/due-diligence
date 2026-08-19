"""Re-extracting a page that has not changed is waste, and it is also risk.

Waste, because extraction is the expensive half of a run and gov.uk does not
change hourly. Risk, because a model asked the same question twice can answer it
differently, and a rule set that drifts while its source sits still would be
indistinguishable from a real rule change.

The digest settles it: identical bytes cannot yield different rules, so the
stored answer is reused and no model is called. A changed digest is the only
thing that earns a fresh extraction — and it is exactly the thing that should.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.extraction_cache import extract_or_reuse
from src.models import MtdPhase, MtdRuleSet
from src.sources import FetchResult, Source

SOURCE = Source(key="unit_test_cache", url="https://example.invalid", expect_text="x")

RULES = MtdRuleSet(
    phases=[
        MtdPhase(
            qualifying_income_over_gbp=50_000,
            mandatory_from="2026-04-06",
            tax_year_tested="2024 to 2025",
        )
    ],
    qualifying_income_definition="turnover before expenses",
    who_it_applies_to="sole traders",
)


def result(digest: str) -> FetchResult:
    return FetchResult(
        source=SOURCE,
        ok=True,
        text="page text",
        fetched_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        digest=digest,
    )


class Counter:
    """Stands in for the model so the tests can assert it was not called."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _text: str) -> MtdRuleSet:
        self.calls += 1
        return RULES


@pytest.fixture(autouse=True)
def isolated_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("src.sources.SNAPSHOT_DIR", tmp_path)
    yield


class TestFirstRun:
    def test_extracts_when_there_is_no_snapshot(self) -> None:
        extract = Counter()
        rules, reused = extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, extract)
        assert extract.calls == 1
        assert reused is False
        assert rules == RULES


class TestUnchangedPage:
    def test_reuses_without_calling_the_model(self) -> None:
        first = Counter()
        extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, first)

        second = Counter()
        rules, reused = extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, second)

        assert second.calls == 0, "identical bytes must not trigger a second extraction"
        assert reused is True
        assert rules == RULES

    def test_the_reused_object_is_fully_reconstructed(self) -> None:
        extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, Counter())
        rules, _ = extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, Counter())
        # Not just the phases: the prose fields have to survive the round trip,
        # because the applicability judge is handed them verbatim.
        assert rules.qualifying_income_definition == "turnover before expenses"
        assert rules.who_it_applies_to == "sole traders"


class TestChangedPage:
    def test_a_new_digest_forces_a_fresh_extraction(self) -> None:
        extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, Counter())

        again = Counter()
        _, reused = extract_or_reuse(SOURCE.key, result("bbb"), MtdRuleSet, again)

        assert again.calls == 1, "a changed page is exactly when re-reading matters"
        assert reused is False


class TestCorruptSnapshot:
    def test_falls_back_to_extracting(self, tmp_path) -> None:
        (tmp_path / f"{SOURCE.key}.json").write_text("{not json", encoding="utf-8")
        extract = Counter()
        _, reused = extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, extract)
        assert extract.calls == 1
        assert reused is False

    def test_a_snapshot_in_the_old_format_is_not_trusted(self, tmp_path) -> None:
        """Missing 'rules' means it predates the cache — re-read rather than guess."""
        (tmp_path / f"{SOURCE.key}.json").write_text(
            '{"digest": "aaa", "phases": []}', encoding="utf-8"
        )
        extract = Counter()
        _, reused = extract_or_reuse(SOURCE.key, result("aaa"), MtdRuleSet, extract)
        assert extract.calls == 1
        assert reused is False
