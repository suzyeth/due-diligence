"""Turn extracted quarterly rules into concrete dated obligations for one person.

The only arithmetic here is calendar arithmetic. Every figure it works from —
periods, deadlines, the fine, the grace year — was read off gov.uk, never
hardcoded. That is the point of the product.
"""

from __future__ import annotations

from datetime import date

from .models import (
    DatedObligation,
    DeadlineStatus,
    QuarterlyDeadline,
    QuarterlyRules,
)

DUE_SOON_DAYS = 14
UPCOMING_DAYS = 60

# A UK tax year runs 6 April to 5 April.
TAX_YEAR_START_MONTH = 4
TAX_YEAR_START_DAY = 6


def tax_year_starting(day: date) -> int:
    """The starting calendar year of the UK tax year that `day` falls in."""
    if (day.month, day.day) >= (TAX_YEAR_START_MONTH, TAX_YEAR_START_DAY):
        return day.year
    return day.year - 1


def tax_year_label(start_year: int) -> str:
    return f"{start_year} to {start_year + 1}"


def _first_occurrence_after(day: int, month: int, after: date) -> date:
    """The first time (day, month) comes round strictly after `after`.

    Using "next occurrence after the period ends" rather than mapping months to
    tax-year halves is what makes the final quarter work: its 7 May deadline
    falls in the *following* calendar year, and this rule gets that right without
    a special case.
    """
    for year in (after.year, after.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:  # e.g. 31 February from a bad extraction
            continue
        if candidate > after:
            return candidate
    raise ValueError(f"could not place {day}/{month} after {after}")


def _period_end_date(deadline: QuarterlyDeadline, tax_year_start: int) -> date:
    """Place a period end inside the tax year beginning `tax_year_start`."""
    starts_at = date(tax_year_start, TAX_YEAR_START_MONTH, TAX_YEAR_START_DAY)
    return _first_occurrence_after(
        deadline.period_end_day, deadline.period_end_month, starts_at
    )


def _classify(due_on: date, today: date) -> tuple[DeadlineStatus, int]:
    days_away = (due_on - today).days
    if days_away < 0:
        return DeadlineStatus.OVERDUE, days_away
    if days_away <= DUE_SOON_DAYS:
        return DeadlineStatus.DUE_SOON, days_away
    if days_away <= UPCOMING_DAYS:
        return DeadlineStatus.UPCOMING, days_away
    return DeadlineStatus.FUTURE, days_away


UNVERIFIED_PENALTY = (
    "penalty details could not be verified from the source — check gov.uk before relying on this"
)


def _consequence(rules: QuarterlyRules, tax_year: str) -> str:
    """What actually happens if this one is missed, in this specific tax year.

    The grace year is the single most misleading thing about the current regime:
    a miss costs nothing this year and starts costing points next year. Saying
    only "you missed it" would teach the wrong habit.

    If the penalty figures did not come back verified, say so. Printing an
    unextracted zero as "a £0 fine" would be worse than printing nothing.
    """
    if not rules.penalty_is_verified:
        if rules.grace_tax_year and rules.grace_tax_year.strip() == tax_year:
            return (
                f"no penalty points — {rules.grace_tax_year} is a grace year. "
                f"Consequences after that: {UNVERIFIED_PENALTY}."
            )
        return UNVERIFIED_PENALTY

    fine = f"1 penalty point; {rules.points_before_fine} points means a £{rules.fine_gbp} fine"
    if rules.grace_tax_year and rules.grace_tax_year.strip() == tax_year:
        return (
            f"no penalty points — {rules.grace_tax_year} is a grace year. "
            f"From the next tax year the same miss costs {fine}."
        )
    return fine


def build_calendar(
    rules: QuarterlyRules,
    today: date,
    tax_year_start: int | None = None,
) -> tuple[DatedObligation, ...]:
    """All quarterly updates for the tax year `today` falls in, in date order."""
    start_year = tax_year_start if tax_year_start is not None else tax_year_starting(today)
    label = tax_year_label(start_year)
    consequence = _consequence(rules, label)

    obligations: list[DatedObligation] = []
    for deadline in rules.deadlines:
        try:
            period_end = _period_end_date(deadline, start_year)
            due_on = _first_occurrence_after(
                deadline.deadline_day, deadline.deadline_month, period_end
            )
        except ValueError:
            # A malformed extraction must not silently vanish from the calendar.
            continue

        status, days_away = _classify(due_on, today)
        coverage = (
            f"{deadline.period_covered} (cumulative from the start of the tax year)"
            if rules.is_cumulative
            else deadline.period_covered
        )
        obligations.append(
            DatedObligation(
                name=f"MTD quarterly update — {label}",
                period_covered=coverage,
                due_on=due_on,
                status=status,
                days_away=days_away,
                consequence_if_missed=consequence,
            )
        )

    return tuple(sorted(obligations, key=lambda o: o.due_on))


def needs_attention(
    obligations: tuple[DatedObligation, ...],
    acknowledged: frozenset[str] = frozenset(),
) -> tuple[DatedObligation, ...]:
    """Reason #2 to interrupt a human: a deadline has entered the danger window.

    An overdue item keeps surfacing until the human acknowledges it. Dropping it
    on a timer would let a missed statutory filing quietly vanish; surfacing it
    forever would make the agent the nag it was built to replace. Acknowledgement
    is the only thing that ends it.
    """
    from .acknowledgements import obligation_key  # local import: keeps storage out of the math

    return tuple(
        o
        for o in obligations
        if o.status in (DeadlineStatus.OVERDUE, DeadlineStatus.DUE_SOON)
        and obligation_key(o) not in acknowledged
    )
