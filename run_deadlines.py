"""Applicability → concrete dates. The full chain for one person.

    .venv/Scripts/python.exe run_deadlines.py

The beat worth watching: the first-ever MTD quarterly deadline fell on 7 August
2026. Anyone in the first wave has already missed it — but 2026 to 2027 is a
grace year, so it costs nothing, and the same miss next year starts costing
points. Reporting "you're late" without that second half teaches the wrong habit.
"""

from __future__ import annotations

from src.console import use_utf8_stdout
from src.models import UserSituation
from src.pipeline import run

DISCLAIMER = (
    "Not tax advice. Reports which obligations appear to apply and when they fall "
    "due, with a source for every claim. Does not calculate tax."
)

PERSON = UserSituation(
    is_sole_trader=True,
    has_property_income=False,
    prior_year_turnover_gbp=62_000,
    prior_year_label="2024 to 2025",
    # Answered, and under the VAT threshold: this script is about the MTD
    # calendar, and an unanswered VAT question would drag an unrelated
    # "I need to ask you something" into a demo that is not about that.
    rolling_12m_turnover_gbp=71_000,
    expects_to_exceed_vat_threshold_soon=False,
)


def main() -> None:
    use_utf8_stdout()
    print(DISCLAIMER)
    print("\n" + "=" * 74)
    print(f"PERSON — {PERSON.describe()}")
    print("=" * 74)

    report = run(PERSON)

    for finding in report.findings:
        print(finding.render())

    for error in report.schedule_errors:
        print(f"\n  ⚠️ could not date an obligation: {error}")

    if report.obligations:
        print(f"\n  FILING CALENDAR ({len(report.obligations)} dates)\n")
        for obligation in report.obligations:
            print(obligation.render())
            print()
    else:
        print("\n  no dated obligations (duty does not apply)")

    print(f"  interrupt the human? {report.should_interrupt_human}")
    for reason in report.interrupt_reasons():
        print(f"    → {reason}")
    if not report.should_interrupt_human:
        print("    → stays silent")


if __name__ == "__main__":
    main()
