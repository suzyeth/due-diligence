"""The run: fetch → extract → diff against last snapshot → judge → decide whether to speak.

The decision at the end is the point of the product. Four reasons to interrupt a
human, and nothing else gets through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .agents import (
    build_model,
    extract_quarterly_rules,
    extract_rules,
    judge_applicability,
)
from .acknowledgements import load_acknowledged
from .deadlines import build_calendar, needs_attention
from .models import (
    DatedObligation,
    Evidence,
    Finding,
    MtdRuleSet,
    RuleChange,
    SourceHealth,
    UserSituation,
    Verdict,
)
from .sources import (
    SOURCES,
    Source,
    fetch,
    is_stale,
    load_snapshot,
    record_health,
    save_snapshot,
)


@dataclass(frozen=True)
class RunReport:
    finding: Finding | None
    rule_changes: tuple[RuleChange, ...]
    health: SourceHealth
    blind_reason: str | None
    obligations: tuple[DatedObligation, ...] = ()
    schedule_error: str | None = None
    acknowledged: frozenset[str] = frozenset()

    @property
    def unacknowledged(self) -> tuple[DatedObligation, ...]:
        return needs_attention(self.obligations, self.acknowledged)

    @property
    def should_interrupt_human(self) -> bool:
        """The four — and only four — reasons to break someone's concentration."""
        return bool(
            self.blind_reason  # 4. the agent cannot see
            or self.schedule_error  # 4. (same reason, different source)
            or self.rule_changes  # 3. the rules moved
            or (self.finding and self.finding.verdict.verdict is Verdict.APPLIES)  # 1. new duty
            or self.unacknowledged  # 2. a deadline entered the danger window
            or (
                self.finding and self.finding.verdict.verdict is Verdict.INSUFFICIENT_INFO
            )  # needs a human fact
        )

    def interrupt_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.blind_reason:
            reasons.append(f"agent cannot verify its sources: {self.blind_reason}")
        if self.schedule_error:
            reasons.append(f"agent cannot verify the filing schedule: {self.schedule_error}")
        for change in self.rule_changes:
            reasons.append(f"rule changed — {change.field}: {change.previous} → {change.current}")
        if self.finding and self.finding.verdict.verdict is Verdict.APPLIES:
            reasons.append(f"obligation applies to you: {self.finding.verdict.obligation}")
        for obligation in self.unacknowledged:
            when = (
                f"was due {abs(obligation.days_away)} days ago"
                if obligation.days_away < 0
                else f"due in {obligation.days_away} days"
            )
            reasons.append(f"{obligation.name} {when} ({obligation.due_on:%Y-%m-%d})")
        if self.finding and self.finding.verdict.verdict is Verdict.INSUFFICIENT_INFO:
            reasons.append(
                "cannot decide without a fact only you know: "
                + ", ".join(self.finding.verdict.missing_facts)
            )
        return reasons


def _diff_rules(previous: dict | None, current: MtdRuleSet) -> tuple[RuleChange, ...]:
    """A rule change is an event in its own right, whoever it affects."""
    if not previous:
        return ()

    changes: list[RuleChange] = []
    old_phases = {
        str(p.get("qualifying_income_over_gbp")): p for p in previous.get("phases", [])
    }
    new_phases = {str(p.qualifying_income_over_gbp): p for p in current.phases}

    for threshold, new in new_phases.items():
        old = old_phases.get(threshold)
        if old is None:
            changes.append(
                RuleChange(
                    field=f"new phase over £{threshold}",
                    previous="(absent)",
                    current=new.mandatory_from,
                )
            )
        elif old.get("mandatory_from") != new.mandatory_from:
            changes.append(
                RuleChange(
                    field=f"start date for the over-£{threshold} phase",
                    previous=str(old.get("mandatory_from")),
                    current=new.mandatory_from,
                )
            )

    for threshold, old in old_phases.items():
        if threshold not in new_phases:
            changes.append(
                RuleChange(
                    field=f"phase over £{threshold}",
                    previous=str(old.get("mandatory_from")),
                    current="(no longer on the page)",
                )
            )

    return tuple(changes)


def run(
    situation: UserSituation,
    source: Source | None = None,
    now: datetime | None = None,
) -> RunReport:
    source = source or SOURCES[0]
    now = now or datetime.now(timezone.utc)

    result = fetch(source)
    health = record_health(result)

    if not result.ok:
        return RunReport(
            finding=None,
            rule_changes=(),
            health=health,
            blind_reason=result.error or "fetch failed",
        )

    model = build_model()
    rules = extract_rules(result.text, model=model)

    previous = load_snapshot(source.key)
    changes = _diff_rules(previous, rules)
    save_snapshot(
        source.key,
        {
            "fetched_at": result.fetched_at,
            "digest": result.digest,
            "phases": [p.model_dump() for p in rules.phases],
        },
    )

    verdict = judge_applicability(rules, situation, today=f"{now:%Y-%m-%d}", model=model)
    finding = Finding(
        verdict=verdict,
        evidence=Evidence(
            source_url=source.url,
            verified_at=result.fetched_at,
            source_is_stale=is_stale(health, now),
            snapshot_digest=result.digest,
        ),
    )

    # Only work out dates once we know the duty is real. Handing someone a
    # calendar for something that does not apply to them is noise.
    obligations: tuple[DatedObligation, ...] = ()
    schedule_error: str | None = None
    if verdict.verdict is Verdict.APPLIES:
        obligations, schedule_error = _build_schedule(now, model)

    return RunReport(
        finding=finding,
        rule_changes=changes,
        health=health,
        blind_reason=None,
        obligations=obligations,
        schedule_error=schedule_error,
        acknowledged=load_acknowledged(),
    )


def _build_schedule(now: datetime, model) -> tuple[tuple[DatedObligation, ...], str | None]:
    """Read the filing schedule off its own primary source, then date it.

    Same contract as the mandation source: if it cannot be verified, return no
    obligations and say why, rather than falling back on remembered dates.
    """
    schedule_source = next((s for s in SOURCES if s.key == "mtd_quarterly"), None)
    if schedule_source is None:
        return (), "no quarterly-schedule source is registered"

    schedule_result = fetch(schedule_source)
    record_health(schedule_result)
    if not schedule_result.ok:
        return (), schedule_result.error or "fetch failed"

    quarterly = extract_quarterly_rules(schedule_result.text, model=model)
    save_snapshot(
        schedule_source.key,
        {
            "fetched_at": schedule_result.fetched_at,
            "digest": schedule_result.digest,
            "deadlines": [d.model_dump() for d in quarterly.deadlines],
            "grace_tax_year": quarterly.grace_tax_year,
        },
    )
    return build_calendar(quarterly, today=now.date()), None

