"""Drive the applicability chain over three people and show what the agent does.

    .venv/Scripts/python.exe run_applicability.py

Case 2 is the one that matters: the person does not know their prior-year
turnover, and the correct behaviour is to refuse rather than guess.
"""

from __future__ import annotations

from src.console import use_utf8_stdout
from src.models import UserSituation
from src.pipeline import run

DISCLAIMER = (
    "Not tax advice. Reports which obligations appear to apply and when they fall "
    "due, with a source for every claim. Does not calculate tax."
)

CASES: tuple[tuple[str, UserSituation], ...] = (
    (
        "over the first-wave threshold",
        UserSituation(
            is_sole_trader=True,
            has_property_income=False,
            prior_year_turnover_gbp=62_000,
            prior_year_label="2024 to 2025",
        ),
    ),
    (
        "does not know their turnover",
        UserSituation(
            is_sole_trader=True,
            has_property_income=True,
            prior_year_turnover_gbp=None,
        ),
    ),
    (
        "well under every threshold",
        UserSituation(
            is_sole_trader=True,
            has_property_income=False,
            prior_year_turnover_gbp=14_000,
            prior_year_label="2024 to 2025",
        ),
    ),
)


def main() -> None:
    use_utf8_stdout()
    print(DISCLAIMER)
    for label, situation in CASES:
        print("\n" + "=" * 74)
        print(f"CASE — {label}")
        print(f"  {situation.describe()}")
        print("=" * 74)

        report = run(situation)

        if report.finding:
            print(report.finding.render())
        if report.rule_changes:
            print("\n  rule changes since last run:")
            for change in report.rule_changes:
                print(f"    • {change.field}: {change.previous} → {change.current}")

        print(f"\n  interrupt the human? {report.should_interrupt_human}")
        for reason in report.interrupt_reasons():
            print(f"    → {reason}")
        if not report.should_interrupt_human:
            print("    → stays silent")


if __name__ == "__main__":
    main()
