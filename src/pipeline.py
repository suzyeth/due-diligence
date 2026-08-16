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
    extract_vat_rules,
    judge_applicability,
    judge_vat_applicability,
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
from .vat import build_vat_obligations


@dataclass(frozen=True)
class RunReport:
    """The outcome of one run, across every obligation the agent checks.

    `findings` is plural because the agent now judges more than one obligation,
    and they fail independently: VAT can come back undecidable while MTD is
    settled. Collapsing them into a single verdict would force a choice about
    which failure to report and hide the other.
    """

    findings: tuple[Finding, ...]
    rule_changes: tuple[RuleChange, ...]
    health: SourceHealth
    blind_reason: str | None
    obligations: tuple[DatedObligation, ...] = ()
    schedule_errors: tuple[str, ...] = ()
    acknowledged: frozenset[str] = frozenset()

    @property
    def unacknowledged(self) -> tuple[DatedObligation, ...]:
        return needs_attention(self.obligations, self.acknowledged)

    def _with_verdict(self, verdict: Verdict) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.verdict.verdict is verdict)

    @property
    def should_interrupt_human(self) -> bool:
        """The four — and only four — reasons to break someone's concentration."""
        return bool(
            self.blind_reason  # 4. the agent cannot see
            or self.schedule_errors  # 4. (same reason, different source)
            or self.rule_changes  # 3. the rules moved
            or self._with_verdict(Verdict.APPLIES)  # 1. a duty landed on you
            or self.unacknowledged  # 2. a deadline entered the danger window
            or self._with_verdict(Verdict.INSUFFICIENT_INFO)  # needs a human fact
        )

    def interrupt_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.blind_reason:
            reasons.append(f"agent cannot verify its sources: {self.blind_reason}")
        for error in self.schedule_errors:
            reasons.append(f"agent cannot verify the filing schedule: {error}")
        for change in self.rule_changes:
            reasons.append(f"rule changed — {change.field}: {change.previous} → {change.current}")
        for finding in self._with_verdict(Verdict.APPLIES):
            reasons.append(f"obligation applies to you: {finding.verdict.obligation}")
        for obligation in self.unacknowledged:
            when = (
                f"was due {abs(obligation.days_away)} days ago"
                if obligation.days_away < 0
                else f"due in {obligation.days_away} days"
            )
            reasons.append(f"{obligation.name} {when} ({obligation.due_on:%Y-%m-%d})")
        for finding in self._with_verdict(Verdict.INSUFFICIENT_INFO):
            reasons.append(
                f"cannot decide {finding.verdict.obligation} without a fact only you know: "
                + ", ".join(finding.verdict.missing_facts)
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
            findings=(),
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
    schedule_errors: list[str] = []
    if verdict.verdict is Verdict.APPLIES:
        obligations, schedule_error = _build_schedule(now, model)
        if schedule_error:
            schedule_errors.append(schedule_error)

    vat_finding, vat_obligations, vat_error = _run_vat(situation, now, model)
    if vat_error:
        schedule_errors.append(vat_error)

    return RunReport(
        findings=tuple(f for f in (finding, vat_finding) if f is not None),
        rule_changes=changes,
        health=health,
        blind_reason=None,
        obligations=tuple(sorted(obligations + vat_obligations, key=lambda o: o.due_on)),
        schedule_errors=tuple(schedule_errors),
        acknowledged=load_acknowledged(),
    )


def _run_vat(
    situation: UserSituation,
    now: datetime,
    model,
) -> tuple[Finding | None, tuple[DatedObligation, ...], str | None]:
    """The VAT arm: same contract as the MTD arm, on its own source and clock.

    Kept separate rather than folded into `run()` because the two obligations
    fail independently — a dead VAT page must not suppress an MTD conclusion
    that was verified perfectly well.
    """
    source = next((s for s in SOURCES if s.key == "vat_registration"), None)
    if source is None:
        return None, (), "no VAT source is registered"

    result = fetch(source)
    record_health(result)
    if not result.ok:
        # Blind on VAT only. Say so, and produce no VAT finding at all.
        return None, (), result.error or "VAT source fetch failed"

    rules = extract_vat_rules(result.text, model=model)
    save_snapshot(
        source.key,
        {
            "fetched_at": result.fetched_at,
            "digest": result.digest,
            "registration_threshold_gbp": rules.registration_threshold_gbp,
            "register_within_days_of_month_end": rules.register_within_days_of_month_end,
        },
    )

    verdict = judge_vat_applicability(rules, situation, today=f"{now:%Y-%m-%d}", model=model)
    finding = Finding(
        verdict=verdict,
        evidence=Evidence(
            source_url=source.url,
            verified_at=result.fetched_at,
            source_is_stale=False,
            snapshot_digest=result.digest,
        ),
    )

    if verdict.verdict is not Verdict.APPLIES:
        return finding, (), None

    # It applies, but the deadline hangs off the month the threshold was crossed.
    # Without that month there is a real obligation and no honest date for it —
    # which is worth saying out loud, not quietly rounding to today.
    if situation.vat_threshold_crossed_in is None:
        return (
            finding,
            (),
            "VAT registration applies, but the month the threshold was crossed is "
            "unknown, so the registration deadline cannot be dated",
        )

    obligations, reason = build_vat_obligations(
        rules,
        crossed_threshold_in=situation.vat_threshold_crossed_in,
        today=now.date(),
    )
    return finding, obligations, reason


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

