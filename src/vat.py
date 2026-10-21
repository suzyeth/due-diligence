"""Turn extracted VAT rules into a dated registration obligation.

VAT keeps a different clock from MTD, which is the whole reason it is worth
carrying as a second obligation:

    MTD  — tested against a completed tax year, deadlines on fixed calendar days
    VAT  — tested against a rolling 12 months, deadline derived from the end of
           whichever month you happened to cross in

So the arithmetic here is month-relative rather than year-relative, and it has
to survive both year boundaries and short Februaries. As in `deadlines.py`, every
figure it works from was read off gov.uk; nothing is hardcoded.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from .deadlines import _classify
from .models import DatedObligation, VatRules

UNVERIFIED = (
    "VAT registration rules could not be verified from the source — "
    "check gov.uk before relying on this"
)


def end_of_month(day: date) -> date:
    """The last day of the month `day` falls in.

    `calendar.monthrange` rather than a 30-day approximation: the registration
    window is counted from the real month end, so February and the 31-day months
    must both come out right.
    """
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _first_day_months_after(day: date, months: int) -> date:
    """The first day of the month `months` after the one `day` falls in.

    Arithmetic on a month ordinal rather than on the date itself, so December
    plus two lands in February of the following year without a special case.
    """
    ordinal = day.year * 12 + (day.month - 1) + months
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def build_vat_obligations(
    rules: VatRules,
    crossed_threshold_in: date,
    today: date,
) -> tuple[tuple[DatedObligation, ...], str | None]:
    """The registration deadline arising from crossing in a given month.

    Returns `(obligations, reason_it_could_not_be_dated)` — the same contract the
    quarterly schedule uses. An unverifiable rule produces no obligation and a
    reason, never a date derived from a figure that was never extracted.
    """
    if not rules.is_datable:
        return (), UNVERIFIED

    # Narrowed by `is_datable`; restated so the arithmetic below reads plainly.
    threshold = rules.registration_threshold_gbp
    within_days = rules.register_within_days_of_month_end
    months_after = rules.effective_from_months_after
    assert threshold and within_days and months_after  # noqa: S101 - invariant, not validation

    due_on = end_of_month(crossed_threshold_in) + timedelta(days=within_days)
    effective_from = _first_day_months_after(crossed_threshold_in, months_after)
    status, days_away = _classify(due_on, today)

    window = (
        f"rolling {rules.lookback_months} months"
        if rules.lookback_months
        else "the rolling turnover test"
    )

    return (
        DatedObligation(
            name=f"VAT registration — turnover passed £{threshold:,}",
            period_covered=(
                f"{window} to the end of {crossed_threshold_in:%B %Y}"
            ),
            due_on=due_on,
            status=status,
            days_away=days_away,
            consequence_if_missed=(
                f"registration takes effect {effective_from:%Y-%m-%d} regardless of "
                f"when you register, so VAT is owed on sales from that date onward "
                f"whether or not you charged it"
            ),
        ),
    ), None
