"""Verify the two behaviours the pitch rests on, both of which are failure paths.

    .venv/Scripts/python.exe run_failure_modes.py

A. The rules moved since last run  → the agent speaks up even though nothing the
   user did changed.
B. The agent cannot see its source → it says so, instead of silently reusing what
   it remembers. Silence must never be mistakable for "nothing changed".
"""

from __future__ import annotations

from src.console import use_utf8_stdout
from src.models import UserSituation
from src.pipeline import run
from src.sources import SOURCES, Source, load_snapshot, save_snapshot

PERSON = UserSituation(
    is_sole_trader=True,
    has_property_income=False,
    prior_year_turnover_gbp=62_000,
    prior_year_label="2024 to 2025",
    # Fully answered on the VAT side so each scenario below shows exactly one
    # failure. An unanswered question is itself a reason to speak, and mixing it
    # in would blur which behaviour is being demonstrated.
    rolling_12m_turnover_gbp=71_000,
    expects_to_exceed_vat_threshold_soon=False,
)


def scenario_rule_change() -> None:
    print("=" * 74)
    print("A — the rules moved while the user did nothing")
    print("=" * 74)

    source = SOURCES[0]
    snapshot = load_snapshot(source.key)
    if not snapshot:
        print("  no snapshot yet — run run_applicability.py first")
        return

    tampered = dict(snapshot)
    tampered["phases"] = [dict(p) for p in snapshot["phases"]]
    if not tampered["phases"]:
        print("  snapshot has no phases to tamper with")
        return

    original = tampered["phases"][0]["mandatory_from"]
    tampered["phases"][0]["mandatory_from"] = "2029-04-06"
    save_snapshot(source.key, tampered)
    print(f"  pretending last week's snapshot said the first phase started 2029-04-06")
    print(f"  (gov.uk actually says {original})\n")

    report = run(PERSON)
    for change in report.rule_changes:
        print(f"  detected: {change.field}: {change.previous} → {change.current}")
    print(f"\n  interrupt the human? {report.should_interrupt_human}")
    for reason in report.interrupt_reasons():
        print(f"    → {reason}")
    if not report.rule_changes:
        print("  ❌ no change detected — diff is broken")


def scenario_blind() -> None:
    print("\n" + "=" * 74)
    print("B — the agent cannot see its source")
    print("=" * 74)

    dead = Source(
        key="mtd_mandation_dead",
        url="https://www.gov.uk/guidance/this-page-does-not-exist-agents-for-humans-spike",
        expect_text="qualifying income",
    )

    report = run(PERSON, source=dead)
    print(f"  findings produced? {len(report.findings)}  (must be 0)")
    print(f"  blind_reason: {report.blind_reason}")
    print(f"  consecutive_failures: {report.health.consecutive_failures}")
    print(f"  is_blind: {report.health.is_blind}")
    print(f"\n  interrupt the human? {report.should_interrupt_human}")
    for reason in report.interrupt_reasons():
        print(f"    → {reason}")

    if report.findings:
        print("  ❌ produced a verdict despite being unable to verify — unsafe")


def scenario_structure_changed() -> None:
    print("\n" + "=" * 74)
    print("C — the page still loads but was restructured")
    print("=" * 74)

    restructured = Source(
        key="mtd_mandation_restructured",
        url=SOURCES[0].url,
        expect_text="a phrase that will never appear on this page",
    )

    report = run(PERSON, source=restructured)
    print(f"  findings produced? {len(report.findings)}  (must be 0)")
    print(f"  blind_reason: {report.blind_reason}")
    print(f"  interrupt the human? {report.should_interrupt_human}")


if __name__ == "__main__":
    use_utf8_stdout()
    scenario_rule_change()
    scenario_blind()
    scenario_structure_changed()
