"""The one conversation this agent has with a person.

Two rules shape every question here.

First, **"I don't know" has to be a first-class answer.** The verdict model has a
third state for exactly this, and an onboarding flow that forces a number would
quietly destroy it — a guessed figure produces a confident wrong answer, which is
the worst thing this product could do.

Second, **no threshold figure appears in any prompt.** The questions ask what the
person's position is, never "are you over £90,000", because the moment a number
is typed here it is a hardcoded rule that will not follow gov.uk when it moves.
"""

from __future__ import annotations

from datetime import date

from .models import UserSituation

SKIP_WORDS = frozenset({"", "?", "skip", "unknown", "dunno", "no idea", "not sure", "unsure"})


def _ask(prompt: str) -> str:
    return input(f"  {prompt}\n  > ").strip()


def ask_yes_no(prompt: str) -> bool:
    """A question with only two acceptable answers, asked until it gets one."""
    while True:
        answer = _ask(f"{prompt} [y/n]").lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("  Please answer y or n.")


def ask_yes_no_unsure(prompt: str) -> bool | None:
    """Three states, because 'I'm not sure' is a real position, not evasion."""
    while True:
        answer = _ask(f"{prompt} [y/n/? if unsure]").lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        if answer in SKIP_WORDS:
            return None
        print("  Please answer y, n, or ? if you are not sure.")


def ask_money(prompt: str) -> int | None:
    """A pounds figure, or None if the person genuinely does not know.

    Punctuation people actually type — commas, a pound sign, decimals — is
    accepted rather than rejected, because bouncing someone back to retype
    '£62,000' as '62000' is how they end up guessing instead.
    """
    while True:
        answer = _ask(f"{prompt} [amount in £, or ? if you don't know]")
        if answer.lower() in SKIP_WORDS:
            return None
        cleaned = answer.replace(",", "").replace("£", "").replace(" ", "")
        try:
            return int(float(cleaned))
        except ValueError:
            print("  Please give a number, or ? if you don't know.")


def ask_month(prompt: str) -> date | None:
    """A month, stored as its first day. None means unknown."""
    while True:
        answer = _ask(f"{prompt} [YYYY-MM, or ? if you don't know]")
        if answer.lower() in SKIP_WORDS:
            return None
        try:
            year, month = answer.split("-")
            return date(int(year), int(month), 1)
        except (ValueError, IndexError):
            print("  Please give a month like 2026-06, or ? if you don't know.")


INTRO = """
Due Diligence needs a few facts about your situation. It asks once and then
runs quietly, only speaking up when something actually needs you.

It never asks for tax amounts, bank access or HMRC login details — only what is
needed to work out which obligations land on you.

"I don't know" is a real answer. Say so rather than guessing: a guess produces
a confident wrong deadline, which is worse than no answer at all.
"""


def run_onboarding() -> UserSituation:
    """Ask the minimum needed to judge applicability, and nothing else."""
    print(INTRO)

    is_sole_trader = ask_yes_no("Are you self-employed as a sole trader?")
    has_property_income = ask_yes_no("Do you receive income from UK property?")

    print("\n  — Making Tax Digital is tested on your PREVIOUS tax year's return —")
    prior_year_turnover = ask_money(
        "Turnover BEFORE expenses on your last filed return (not profit)"
    )
    prior_year_label = None
    if prior_year_turnover is not None:
        answer = _ask("Which tax year was that? [e.g. 2024 to 2025]")
        prior_year_label = answer or None
    already_signed_up_to_mtd = ask_yes_no("Have you already signed up for Making Tax Digital?")

    print("\n  — VAT runs on a rolling 12 months, so it needs a separate figure —")
    already_vat_registered = ask_yes_no("Are you already registered for VAT?")
    rolling_turnover = None
    crossed_in = None
    expects_soon = None
    if not already_vat_registered:
        rolling_turnover = ask_money("Taxable turnover across the LAST 12 MONTHS")
        if ask_yes_no("Do you know your turnover has passed the VAT registration threshold?"):
            crossed_in = ask_month("Which month did it pass?")
        expects_soon = ask_yes_no_unsure(
            "Do you expect to pass the VAT threshold in the next few weeks?"
        )

    return UserSituation(
        is_sole_trader=is_sole_trader,
        has_property_income=has_property_income,
        prior_year_turnover_gbp=prior_year_turnover,
        prior_year_label=prior_year_label,
        already_signed_up_to_mtd=already_signed_up_to_mtd,
        rolling_12m_turnover_gbp=rolling_turnover,
        vat_threshold_crossed_in=crossed_in,
        expects_to_exceed_vat_threshold_soon=expects_soon,
        already_vat_registered=already_vat_registered,
    )
