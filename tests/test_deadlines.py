"""Pure-logic tests. No model, no network — these must stay fast and deterministic.

Two things are covered because both fail silently:
  * calendar arithmetic, where the fourth quarter's deadline lands in the
    following calendar year;
  * the zero-guard, where an unextracted penalty must not be rendered as a real
    rule saying the fine is £0.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.acknowledgements import obligation_key
from src.deadlines import (
    UNVERIFIED_PENALTY,
    build_calendar,
    needs_attention,
    tax_year_label,
    tax_year_starting,
)
from src.models import DeadlineStatus, QuarterlyDeadline, QuarterlyRules

STANDARD_QUARTERS = [
    QuarterlyDeadline(
        period_covered="6 April to 5 July",
        period_end_day=5,
        period_end_month=7,
        deadline_day=7,
        deadline_month=8,
    ),
    QuarterlyDeadline(
        period_covered="6 April to 5 October",
        period_end_day=5,
        period_end_month=10,
        deadline_day=7,
        deadline_month=11,
    ),
    QuarterlyDeadline(
        period_covered="6 April to 5 January",
        period_end_day=5,
        period_end_month=1,
        deadline_day=7,
        deadline_month=2,
    ),
    QuarterlyDeadline(
        period_covered="6 April to 5 April",
        period_end_day=5,
        period_end_month=4,
        deadline_day=7,
        deadline_month=5,
    ),
]


def rules(**overrides) -> QuarterlyRules:
    base = dict(
        deadlines=STANDARD_QUARTERS,
        is_cumulative=True,
        points_before_fine=4,
        fine_gbp=200,
        grace_tax_year="2026 to 2027",
    )
    base.update(overrides)
    return QuarterlyRules(**base)


# ── tax year boundaries ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 4, 5), 2025),  # last day of the old tax year
        (date(2026, 4, 6), 2026),  # first day of the new one
        (date(2026, 12, 31), 2026),
        (date(2027, 1, 1), 2026),  # January still belongs to the 2026 year
        (date(2027, 4, 6), 2027),
    ],
)
def test_tax_year_boundaries(day: date, expected: int) -> None:
    assert tax_year_starting(day) == expected


def test_tax_year_label() -> None:
    assert tax_year_label(2026) == "2026 to 2027"


# ── calendar arithmetic ───────────────────────────────────────────────────────


def test_all_four_deadlines_land_on_the_right_dates() -> None:
    calendar = build_calendar(rules(), today=date(2026, 8, 11))
    assert [o.due_on for o in calendar] == [
        date(2026, 8, 7),
        date(2026, 11, 7),
        date(2027, 2, 7),
        date(2027, 5, 7),  # the one that crosses into the next calendar year
    ]


def test_fourth_quarter_deadline_follows_its_period_end() -> None:
    """7 May must sit after the 5 April period end, not eleven months before it."""
    calendar = build_calendar(rules(), today=date(2026, 8, 11))
    last = calendar[-1]
    assert last.due_on == date(2027, 5, 7)
    assert last.due_on > date(2027, 4, 5)


def test_status_classification() -> None:
    calendar = build_calendar(rules(), today=date(2026, 8, 11))
    assert calendar[0].status is DeadlineStatus.OVERDUE
    assert calendar[0].days_away == -4
    assert calendar[1].status is DeadlineStatus.FUTURE


def test_due_soon_window() -> None:
    calendar = build_calendar(rules(), today=date(2026, 11, 1))
    due_next = next(o for o in calendar if o.due_on == date(2026, 11, 7))
    assert due_next.status is DeadlineStatus.DUE_SOON
    assert due_next in needs_attention(calendar)


def test_a_missed_deadline_keeps_surfacing_until_acknowledged() -> None:
    """An unfiled statutory return must not quietly disappear on a timer."""
    calendar = build_calendar(rules(), today=date(2026, 9, 15))
    still_overdue = needs_attention(calendar)
    assert [o.due_on for o in still_overdue] == [date(2026, 8, 7)]


def test_acknowledging_ends_the_nagging() -> None:
    """...but once the human has been told, the agent goes quiet again.

    Surfacing it forever would make this the nag it was built to replace.
    """
    calendar = build_calendar(rules(), today=date(2026, 9, 15))
    missed = calendar[0]
    acknowledged = frozenset({obligation_key(missed)})
    assert needs_attention(calendar, acknowledged) == ()


def test_acknowledging_one_occurrence_does_not_silence_the_next() -> None:
    """Acknowledgement is per due date, not per obligation type."""
    calendar = build_calendar(rules(), today=date(2026, 11, 1))
    acknowledged = frozenset({obligation_key(calendar[0])})  # the August one
    remaining = needs_attention(calendar, acknowledged)
    assert [o.due_on for o in remaining] == [date(2026, 11, 7)]


def test_nothing_needs_attention_in_a_genuinely_quiet_week() -> None:
    """The product's whole promise is silence when there is nothing to say."""
    calendar = build_calendar(rules(), today=date(2026, 9, 15))
    acknowledged = frozenset(obligation_key(o) for o in calendar)
    assert needs_attention(calendar, acknowledged) == ()


def test_malformed_extraction_is_dropped_not_crashed() -> None:
    broken = QuarterlyDeadline(
        period_covered="nonsense",
        period_end_day=31,
        period_end_month=2,  # 31 February
        deadline_day=40,
        deadline_month=13,
    )
    calendar = build_calendar(rules(deadlines=[broken, *STANDARD_QUARTERS]), today=date(2026, 8, 11))
    assert len(calendar) == 4


# ── the zero-guard ────────────────────────────────────────────────────────────


def test_grace_year_is_stated_with_what_comes_after() -> None:
    calendar = build_calendar(rules(), today=date(2026, 8, 11))
    consequence = calendar[0].consequence_if_missed
    assert "grace year" in consequence
    assert "4 points" in consequence and "£200" in consequence


def test_unextracted_penalty_is_never_rendered_as_a_real_rule() -> None:
    """A missing figure must read as 'unverified', never as 'a £0 fine'."""
    calendar = build_calendar(
        rules(points_before_fine=None, fine_gbp=None), today=date(2026, 8, 11)
    )
    consequence = calendar[0].consequence_if_missed
    assert UNVERIFIED_PENALTY in consequence
    assert "£0" not in consequence


def test_zero_is_treated_as_not_extracted() -> None:
    """The exact failure observed in the first run: the model wrote 0, not null."""
    assert rules(points_before_fine=0, fine_gbp=0).penalty_is_verified is False
    calendar = build_calendar(rules(points_before_fine=0, fine_gbp=0), today=date(2026, 8, 11))
    assert "£0" not in calendar[0].consequence_if_missed


def test_no_grace_year_states_the_penalty_plainly() -> None:
    calendar = build_calendar(rules(grace_tax_year=None), today=date(2026, 8, 11))
    consequence = calendar[0].consequence_if_missed
    assert "grace year" not in consequence
    assert "4 points" in consequence
