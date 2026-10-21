"""Due Diligence, as a thing you install and then forget about.

    python -m src.cli check          run; say nothing unless something needs you
    python -m src.cli check --report print the full picture regardless
    python -m src.cli ack            mark outstanding items as seen
    python -m src.cli profile        show the stored answers
    python -m src.cli profile --reset

The silence is the product, so it is enforced here rather than left to a flag:
`check` writes nothing at all — not a heartbeat, not an "all clear" — when the
answer is that nothing needs a human. Anything else trains people to skim past
it, and a notification people skim past is worse than none.

Exit codes are how an unattended runner tells the difference:

    0   nothing needs you
    10  something needs you
    1   the agent could not run
"""

from __future__ import annotations

import argparse
import sys

from .acknowledgements import acknowledge, acknowledge_verdict, load_acknowledged
from .console import use_utf8_stdout
from .models import Finding, UserSituation
from .onboarding import run_onboarding
from .pipeline import RunReport, run
from .profile import clear_profile, load_profile, save_profile

EXIT_QUIET = 0
EXIT_NEEDS_YOU = 10
EXIT_ERROR = 1

DISCLAIMER = (
    "Not tax advice. Reports which obligations appear to apply and when they "
    "fall due, with a source for every claim. It does not calculate tax owed."
)


def _situation_or_onboard() -> UserSituation:
    """Stored answers if we have them, otherwise ask — once."""
    stored = load_profile()
    if stored is not None:
        return stored
    situation = run_onboarding()
    save_profile(situation)
    print("\n  Saved. From here on this runs quietly.\n")
    return situation


def _print_report(report: RunReport, *, full: bool) -> None:
    print(DISCLAIMER)
    print()

    if report.blind_reason:
        print("⚠️  Cannot verify sources — no conclusions this run.")
        print(f"    {report.blind_reason}")
        return

    # When it speaks unprompted, it shows only what is new — repeating settled
    # verdicts is how a notification becomes wallpaper. `--report` is the
    # deliberate "show me everything" gesture, so there it shows everything.
    for finding in report.findings if full else report.unacknowledged_findings:
        print(finding.render())
        print()

    shown = report.obligations if full else report.unacknowledged
    if shown:
        print(f"  {'ALL DATED OBLIGATIONS' if full else 'NEEDS YOUR ATTENTION'}\n")
        for obligation in shown:
            print(obligation.render())
            print()

    for change in report.rule_changes:
        print(f"  ⚡ rule changed — {change.field}: {change.previous} → {change.current}")
    for error in report.schedule_errors:
        print(f"  ⚠️  could not date an obligation: {error}")


def cmd_check(args: argparse.Namespace) -> int:
    situation = _situation_or_onboard()
    report = run(situation)

    if not report.should_interrupt_human and not args.report:
        return EXIT_QUIET  # deliberately silent — see module docstring

    _print_report(report, full=args.report)

    if report.should_interrupt_human:
        print("  Why you are seeing this:")
        for reason in report.interrupt_reasons():
            print(f"    → {reason}")
        return EXIT_NEEDS_YOU

    print("  Nothing needs you right now.")
    return EXIT_QUIET


def cmd_ack(args: argparse.Namespace) -> int:
    """Acknowledging is the only thing that stops an item resurfacing.

    Not the same as doing it. The record says the person has been told, which is
    why the wording below avoids implying the filing itself is done.
    """
    situation = load_profile()
    if situation is None:
        print("No profile yet — run `check` first.")
        return EXIT_ERROR

    report = run(situation)

    # Two kinds of news, acknowledged the same way but stored under different
    # keys: "this duty applies to you" and "this particular date is upon you".
    items: list[tuple[str, object]] = [
        (f"{f.verdict.obligation} — {f.verdict.verdict.value}", f)
        for f in report.unacknowledged_findings
    ] + [
        (f"{o.due_on:%Y-%m-%d}  {o.name}", o) for o in report.unacknowledged
    ]

    if not items:
        print("Nothing outstanding to acknowledge.")
        return EXIT_QUIET

    print("Outstanding:\n")
    for index, (label, _) in enumerate(items, start=1):
        print(f"  {index}. {label}")

    print(
        "\nAcknowledging means 'I have seen this', not 'I have filed it'.\n"
        "It stops the reminder; it does not stop the deadline."
    )
    answer = input("\nNumber to acknowledge, 'all', or Enter to cancel: ").strip().lower()
    if not answer:
        return EXIT_QUIET

    if answer == "all":
        chosen = [entry for _, entry in items]
    elif answer.isdigit() and 1 <= int(answer) <= len(items):
        chosen = [items[int(answer) - 1][1]]
    else:
        print("Not a valid choice — nothing acknowledged.")
        return EXIT_ERROR

    for entry in chosen:
        if isinstance(entry, Finding):
            acknowledge_verdict(entry)
        else:
            acknowledge(entry)
    print(f"Acknowledged {len(chosen)}.")
    return EXIT_QUIET


def cmd_profile(args: argparse.Namespace) -> int:
    if args.reset:
        clear_profile()
        print("Profile cleared. The next `check` will ask again.")
        return EXIT_QUIET

    situation = load_profile()
    if situation is None:
        print("No profile yet — run `check` to set one up.")
        return EXIT_QUIET

    print("Stored answers:\n")
    print(f"  Income Tax / MTD : {situation.describe()}")
    print(f"  VAT              : {situation.describe_for_vat()}")
    print(f"\n  Acknowledged items: {len(load_acknowledged())}")
    return EXIT_QUIET


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="due-diligence",
        description="Works out which UK tax obligations apply to you, and stays quiet otherwise.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="run a check; silent unless something needs you")
    check.add_argument(
        "--report",
        action="store_true",
        help="print everything, including what does not need you",
    )
    check.set_defaults(func=cmd_check)

    ack = sub.add_parser("ack", help="mark outstanding items as seen")
    ack.set_defaults(func=cmd_ack)

    profile = sub.add_parser("profile", help="show or reset the stored answers")
    profile.add_argument("--reset", action="store_true", help="forget the stored answers")
    profile.set_defaults(func=cmd_profile)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    use_utf8_stdout()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
