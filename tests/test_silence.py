"""The silence contract. This is the product claim, so it gets its own tests.

The agent is allowed to interrupt for four reasons, and the first of them is that
a NEW obligation started applying — not that an obligation applies. Those are
different sentences, and the difference is the whole product: an agent that
reannounces a standing duty on every run has become the nagging app it was built
to replace.

No model and no network here; every case is constructed directly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.acknowledgements import obligation_key, verdict_key
from src.models import (
    ApplicabilityVerdict,
    DatedObligation,
    DeadlineStatus,
    Evidence,
    Finding,
    SourceHealth,
    Verdict,
)
from src.pipeline import RunReport

NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)

HEALTHY = SourceHealth(
    source_url="https://example.invalid/page",
    last_success=NOW,
    consecutive_failures=0,
    last_error=None,
)


def finding(obligation: str, verdict: Verdict) -> Finding:
    return Finding(
        verdict=ApplicabilityVerdict(
            verdict=verdict,
            obligation=obligation,
            reasoning="constructed for test",
            missing_facts=["a fact"] if verdict is Verdict.INSUFFICIENT_INFO else [],
        ),
        evidence=Evidence(
            source_url="https://example.invalid/page",
            verified_at=NOW,
            source_is_stale=False,
            snapshot_digest="deadbeef",
        ),
    )


def obligation(name: str, due: date, status: DeadlineStatus) -> DatedObligation:
    return DatedObligation(
        name=name,
        period_covered="a period",
        due_on=due,
        status=status,
        days_away=(due - NOW.date()).days,
        consequence_if_missed="something",
    )


def report(**kwargs) -> RunReport:
    base = dict(
        findings=(),
        rule_changes=(),
        health=HEALTHY,
        blind_reason=None,
    )
    base.update(kwargs)
    return RunReport(**base)


class TestAStandingObligationGoesQuiet:
    def test_a_newly_applying_obligation_speaks(self) -> None:
        r = report(findings=(finding("MTD", Verdict.APPLIES),))
        assert r.should_interrupt_human is True

    def test_the_same_obligation_is_silent_once_acknowledged(self) -> None:
        known = finding("MTD", Verdict.APPLIES)
        r = report(findings=(known,), acknowledged=frozenset({verdict_key(known)}))
        assert r.should_interrupt_human is False, (
            "a standing duty the human already knows about is not news"
        )

    def test_a_second_obligation_still_breaks_the_silence(self) -> None:
        known = finding("MTD", Verdict.APPLIES)
        fresh = finding("VAT registration", Verdict.APPLIES)
        r = report(findings=(known, fresh), acknowledged=frozenset({verdict_key(known)}))
        assert r.should_interrupt_human is True
        assert any("VAT" in reason for reason in r.interrupt_reasons())
        assert not any(
            reason.startswith("obligation applies to you: MTD") for reason in r.interrupt_reasons()
        )


class TestUnansweredQuestionsAlsoStopNagging:
    def test_an_open_question_speaks_once(self) -> None:
        r = report(findings=(finding("VAT registration", Verdict.INSUFFICIENT_INFO),))
        assert r.should_interrupt_human is True

    def test_and_is_silent_after_acknowledgement(self) -> None:
        asked = finding("VAT registration", Verdict.INSUFFICIENT_INFO)
        r = report(findings=(asked,), acknowledged=frozenset({verdict_key(asked)}))
        assert r.should_interrupt_human is False


class TestWhatMustAlwaysSpeak:
    """Things acknowledgement must never be able to silence."""

    def test_blindness_always_speaks(self) -> None:
        known = finding("MTD", Verdict.APPLIES)
        r = report(
            findings=(),
            blind_reason="404 from the source",
            acknowledged=frozenset({verdict_key(known)}),
        )
        assert r.should_interrupt_human is True

    def test_a_rule_change_always_speaks(self) -> None:
        from src.models import RuleChange

        known = finding("MTD", Verdict.APPLIES)
        r = report(
            findings=(known,),
            rule_changes=(RuleChange(field="threshold", previous="50000", current="30000"),),
            acknowledged=frozenset({verdict_key(known)}),
        )
        assert r.should_interrupt_human is True

    def test_an_undated_obligation_always_speaks(self) -> None:
        known = finding("VAT registration", Verdict.APPLIES)
        r = report(
            findings=(known,),
            schedule_errors=("the crossing month is unknown",),
            acknowledged=frozenset({verdict_key(known)}),
        )
        assert r.should_interrupt_human is True


class TestDeadlinesAreSeparateFromVerdicts:
    def test_acknowledging_the_verdict_does_not_silence_its_deadline(self) -> None:
        """Two different pieces of news: 'this applies' and 'this is overdue'."""
        known = finding("MTD", Verdict.APPLIES)
        overdue = obligation("MTD quarterly update", date(2026, 8, 7), DeadlineStatus.OVERDUE)
        r = report(
            findings=(known,),
            obligations=(overdue,),
            acknowledged=frozenset({verdict_key(known)}),
        )
        assert r.should_interrupt_human is True
        assert r.unacknowledged == (overdue,)

    def test_acknowledging_both_finally_produces_silence(self) -> None:
        known = finding("MTD", Verdict.APPLIES)
        overdue = obligation("MTD quarterly update", date(2026, 8, 7), DeadlineStatus.OVERDUE)
        r = report(
            findings=(known,),
            obligations=(overdue,),
            acknowledged=frozenset({verdict_key(known), obligation_key(overdue)}),
        )
        assert r.should_interrupt_human is False


class TestVerdictKeyIdentity:
    def test_a_changed_verdict_is_not_covered_by_the_old_acknowledgement(self) -> None:
        """Going from 'does not apply' to 'applies' is news, even if acked before."""
        before = finding("VAT registration", Verdict.DOES_NOT_APPLY)
        after = finding("VAT registration", Verdict.APPLIES)
        assert verdict_key(before) != verdict_key(after)

        r = report(findings=(after,), acknowledged=frozenset({verdict_key(before)}))
        assert r.should_interrupt_human is True


@pytest.mark.parametrize("verdict", [Verdict.DOES_NOT_APPLY])
def test_a_duty_that_does_not_apply_is_never_news(verdict: Verdict) -> None:
    r = report(findings=(finding("MTD", verdict),))
    assert r.should_interrupt_human is False
