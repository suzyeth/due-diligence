"""VAT registration date arithmetic. No model, no network.

The VAT clock is deliberately unlike the MTD clock, and that is why this
obligation earns its place: MTD is tested against a completed tax year and its
deadlines are fixed calendar dates, whereas VAT runs on a rolling window and its
deadline is derived from the end of whichever month you happened to cross in.
Everything below is about getting that derivation right, including the two
places it crosses a year boundary.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.models import DeadlineStatus, VatRules
from src.vat import build_vat_obligations, end_of_month

# Figures as printed on gov.uk/vat-registration/when-to-register, passed in the
# same way the real pipeline passes them: extracted, never hardcoded in src/.
VERIFIED = VatRules(
    registration_threshold_gbp=90_000,
    lookback_months=12,
    forward_look_days=30,
    register_within_days_of_month_end=30,
    effective_from_months_after=2,
)


class TestEndOfMonth:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (date(2026, 6, 15), date(2026, 6, 30)),
            (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2026, 12, 25), date(2026, 12, 31)),
            (date(2027, 2, 3), date(2027, 2, 28)),
            (date(2028, 2, 3), date(2028, 2, 29)),  # leap year
        ],
    )
    def test_lands_on_the_last_day(self, day: date, expected: date) -> None:
        assert end_of_month(day) == expected


class TestRegistrationDeadline:
    def test_is_thirty_days_after_the_end_of_the_crossing_month(self) -> None:
        obligations, reason = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 6, 20)
        )
        assert reason is None
        # end of June is the 30th; thirty days later is 30 July.
        assert [o.due_on for o in obligations] == [date(2026, 7, 30)]

    def test_crossing_in_december_rolls_into_the_next_year(self) -> None:
        obligations, _ = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2026, 12, 10), today=date(2026, 12, 11)
        )
        assert obligations[0].due_on == date(2027, 1, 30)

    def test_short_february_is_measured_from_its_real_last_day(self) -> None:
        obligations, _ = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2027, 2, 3), today=date(2027, 2, 4)
        )
        # 28 Feb + 30 days, not 31 Feb and not a fixed 30-day month.
        assert obligations[0].due_on == date(2027, 3, 30)


class TestEffectiveDate:
    def test_is_the_first_of_the_second_month_after_crossing(self) -> None:
        obligations, _ = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 6, 20)
        )
        assert "2026-08-01" in obligations[0].consequence_if_missed

    def test_rolls_across_the_year_boundary(self) -> None:
        obligations, _ = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2026, 12, 10), today=date(2026, 12, 11)
        )
        assert "2027-02-01" in obligations[0].consequence_if_missed


class TestStatus:
    def test_a_passed_deadline_is_overdue(self) -> None:
        obligations, _ = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 9, 1)
        )
        assert obligations[0].status is DeadlineStatus.OVERDUE
        assert obligations[0].days_away < 0

    def test_a_near_deadline_is_due_soon(self) -> None:
        obligations, _ = build_vat_obligations(
            VERIFIED, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 7, 25)
        )
        assert obligations[0].status is DeadlineStatus.DUE_SOON


class TestUnverifiedRules:
    """Invariant 6: a figure that was not extracted must never be rendered as 0."""

    def test_missing_deadline_figure_yields_no_obligation_and_says_why(self) -> None:
        rules = VatRules(
            registration_threshold_gbp=90_000,
            lookback_months=12,
            forward_look_days=30,
            register_within_days_of_month_end=None,
            effective_from_months_after=2,
        )
        obligations, reason = build_vat_obligations(
            rules, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 6, 20)
        )
        assert obligations == ()
        assert reason is not None

    def test_zero_counts_as_not_extracted(self) -> None:
        """A zero-day window would render as 'register by the end of the month'."""
        rules = VatRules(
            registration_threshold_gbp=90_000,
            lookback_months=12,
            forward_look_days=30,
            register_within_days_of_month_end=0,
            effective_from_months_after=2,
        )
        obligations, reason = build_vat_obligations(
            rules, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 6, 20)
        )
        assert obligations == ()
        assert reason is not None

    def test_missing_threshold_yields_no_obligation(self) -> None:
        rules = VatRules(
            registration_threshold_gbp=None,
            lookback_months=12,
            forward_look_days=30,
            register_within_days_of_month_end=30,
            effective_from_months_after=2,
        )
        obligations, reason = build_vat_obligations(
            rules, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 6, 20)
        )
        assert obligations == ()
        assert reason is not None


class TestProvenance:
    def test_the_extracted_threshold_is_what_gets_reported(self) -> None:
        """Feed a different figure and the output must follow it, not gov.uk today.

        If this test ever fails because the output still says 90,000, someone has
        hardcoded the threshold and the product's central claim is broken.
        """
        moved = VatRules(
            registration_threshold_gbp=105_000,
            lookback_months=12,
            forward_look_days=30,
            register_within_days_of_month_end=30,
            effective_from_months_after=2,
        )
        obligations, _ = build_vat_obligations(
            moved, crossed_threshold_in=date(2026, 6, 15), today=date(2026, 6, 20)
        )
        rendered = obligations[0].name + obligations[0].period_covered
        assert "105,000" in rendered
        assert "90,000" not in rendered
