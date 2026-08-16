"""Domain models.

Every model here is immutable (`frozen=True`): a run produces new objects rather
than mutating prior state, so a snapshot taken at any point stays trustworthy.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# ── what we read off a primary source ─────────────────────────────────────────


class MtdPhase(Frozen):
    """One mandation wave of Making Tax Digital for Income Tax."""

    qualifying_income_over_gbp: int = Field(
        description="Qualifying income threshold in pounds, e.g. 50000"
    )
    mandatory_from: str = Field(
        description="Date this phase becomes mandatory, ISO format YYYY-MM-DD"
    )
    tax_year_tested: str = Field(
        description="Which tax year's return the threshold is tested against, e.g. '2024 to 2025'"
    )


class MtdRuleSet(Frozen):
    """Rules extracted from a primary source page. Never hardcode these."""

    phases: list[MtdPhase] = Field(description="All mandation phases described on the page")
    qualifying_income_definition: str = Field(
        description="How the page defines qualifying income, in one sentence"
    )
    who_it_applies_to: str = Field(
        description="Which taxpayers the rules apply to, in one sentence"
    )


# ── what the user tells us (the minimum needed to judge applicability) ────────


class UserSituation(Frozen):
    """Deliberately minimal. No amounts of tax, no bank access, no HMRC login."""

    is_sole_trader: bool
    has_property_income: bool
    prior_year_turnover_gbp: int | None = Field(
        default=None,
        description="Turnover BEFORE expenses from the prior tax year return. "
        "None means the user does not know — that is a valid state.",
    )
    prior_year_label: str | None = Field(
        default=None, description="Which tax year the turnover figure is from, e.g. '2024 to 2025'"
    )
    already_signed_up_to_mtd: bool = False

    def describe(self) -> str:
        """Render as plain prose for the reasoning agent."""
        parts = [
            "sole trader" if self.is_sole_trader else "not a sole trader",
            "has property income" if self.has_property_income else "no property income",
        ]
        if self.prior_year_turnover_gbp is None:
            parts.append("prior-year turnover: UNKNOWN — the user does not know this figure")
        else:
            label = self.prior_year_label or "prior tax year"
            parts.append(f"turnover before expenses for {label}: £{self.prior_year_turnover_gbp:,}")
        parts.append(
            "already signed up to MTD" if self.already_signed_up_to_mtd else "not signed up to MTD"
        )
        return "; ".join(parts)


# ── what we output ────────────────────────────────────────────────────────────


class Verdict(str, Enum):
    """Three states. The third one is the honest answer, not a failure."""

    APPLIES = "applies"
    DOES_NOT_APPLY = "does_not_apply"
    INSUFFICIENT_INFO = "insufficient_info"


class ApplicabilityVerdict(Frozen):
    """A judgement about one obligation, with the reasoning that produced it."""

    verdict: Verdict = Field(
        description="applies / does_not_apply / insufficient_info. "
        "Use insufficient_info when a required fact is unknown — never guess."
    )
    obligation: str = Field(description="Short name of the obligation being judged")
    reasoning: str = Field(
        description="Why this verdict follows from the rules and the user's situation, 1-3 sentences"
    )
    missing_facts: list[str] = Field(
        default_factory=list,
        description="If insufficient_info, exactly which facts are needed. Empty otherwise.",
    )
    mandatory_from: str | None = Field(
        default=None, description="If it applies, the date it starts, ISO format. Otherwise null."
    )


class Evidence(Frozen):
    """Provenance for a verdict. Without this the verdict is not trustworthy."""

    source_url: str
    verified_at: datetime
    source_is_stale: bool
    snapshot_digest: str


class Finding(Frozen):
    """A verdict plus its provenance — the unit this agent actually emits."""

    verdict: ApplicabilityVerdict
    evidence: Evidence

    def render(self) -> str:
        v = self.verdict
        icon = {
            Verdict.APPLIES: "🔴",
            Verdict.DOES_NOT_APPLY: "⚪",
            Verdict.INSUFFICIENT_INFO: "🟡",
        }[v.verdict]
        lines = [f"{icon} {v.obligation} — {v.verdict.value.upper()}"]
        if v.mandatory_from:
            lines.append(f"   starts: {v.mandatory_from}")
        lines.append(f"   why: {v.reasoning}")
        if v.missing_facts:
            lines.append(f"   need to know: {', '.join(v.missing_facts)}")
        lines.append(
            f"   source: {self.evidence.source_url}"
            f" (checked {self.evidence.verified_at:%Y-%m-%d})"
        )
        if self.evidence.source_is_stale:
            lines.append("   ⚠️ source check is stale — do not rely on this without a re-check")
        return "\n".join(lines)


# ── the quarterly filing schedule, also read off a primary source ─────────────


class QuarterlyDeadline(Frozen):
    """One quarterly update: the period it covers and when it is due.

    Days and months are separate integers on purpose — small models fill those
    far more reliably than they parse a date string.
    """

    period_covered: str = Field(description="The period label exactly as printed, e.g. '6 April to 5 July'")
    period_end_day: int = Field(description="Day of month the period ends, 1-31")
    period_end_month: int = Field(description="Month the period ends, 1-12")
    deadline_day: int = Field(description="Day of month the update is due, 1-31")
    deadline_month: int = Field(description="Month the update is due, 1-12")


class QuarterlyRules(Frozen):
    """Extracted from the quarterly-updates page. Nothing here is hardcoded."""

    deadlines: list[QuarterlyDeadline] = Field(
        description="The four standard quarterly update periods and deadlines"
    )
    is_cumulative: bool = Field(
        description="True if each update covers from the start of the tax year, not just the quarter"
    )
    points_before_fine: int | None = Field(
        default=None,
        description="How many penalty points trigger a fine for people who are REQUIRED to use "
        "MTD (not volunteers). Null if the text does not state it — never guess, never use 0.",
    )
    fine_gbp: int | None = Field(
        default=None,
        description="The fine in pounds once the points threshold is reached. "
        "Null if the text does not state it — never guess, never use 0.",
    )
    grace_tax_year: str | None = Field(
        default=None,
        description="A tax year in which late quarterly updates are NOT penalised, e.g. "
        "'2026 to 2027'. Null if the text describes no such grace period.",
    )

    @property
    def penalty_is_verified(self) -> bool:
        """Zero is not a plausible penalty rule — treat it as 'not extracted'."""
        return bool(self.points_before_fine) and bool(self.fine_gbp)


class DeadlineStatus(str, Enum):
    OVERDUE = "overdue"
    DUE_SOON = "due_soon"
    UPCOMING = "upcoming"
    FUTURE = "future"


class DatedObligation(Frozen):
    """A concrete thing to do on a concrete day, with what happens if you don't."""

    name: str
    period_covered: str
    due_on: date
    status: DeadlineStatus
    days_away: int
    consequence_if_missed: str

    def render(self, ) -> str:
        icon = {
            DeadlineStatus.OVERDUE: "⛔",
            DeadlineStatus.DUE_SOON: "🔴",
            DeadlineStatus.UPCOMING: "🟠",
            DeadlineStatus.FUTURE: "⚪",
        }[self.status]
        when = (
            f"{abs(self.days_away)} days ago"
            if self.days_away < 0
            else ("today" if self.days_away == 0 else f"in {self.days_away} days")
        )
        return (
            f"{icon} {self.due_on:%Y-%m-%d} ({when})  {self.name}\n"
            f"     covers: {self.period_covered}\n"
            f"     if missed: {self.consequence_if_missed}"
        )


class RuleChange(Frozen):
    """A difference between the last snapshot and what the source says now."""

    field: str
    previous: str
    current: str


class SourceHealth(Frozen):
    """Whether the agent can still see. Silence must never be mistaken for 'nothing changed'."""

    source_url: str
    last_success: datetime | None
    consecutive_failures: int
    last_error: str | None

    @property
    def is_blind(self) -> bool:
        return self.consecutive_failures > 0 or self.last_success is None

    def days_since_success(self, now: date) -> int | None:
        if self.last_success is None:
            return None
        return (now - self.last_success.date()).days
