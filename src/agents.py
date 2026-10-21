"""The two reasoning steps: extract rules from a page, then judge applicability.

Both steps use `structured_output_model` so the model returns a validated object
rather than prose we would have to parse. Neither step is allowed to invent a
figure — extraction reads only what is on the page, and judgement reads only the
extracted rules plus the user's own statement.
"""

from __future__ import annotations

import os

from strands import Agent
from strands.models import Model
from strands.models.bedrock import BedrockModel

from .excerpt import select
from .models import (
    ApplicabilityVerdict,
    MtdRuleSet,
    QuarterlyRules,
    UserSituation,
    VatRules,
)

# An inference profile ID, not a bare model ID: Claude 4.x on Bedrock is only
# reachable cross-region, and the bare `anthropic.` ID fails at invoke time.
DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "us-east-1"

# Anchors, not a fixed head window. The mandation table sits near the top of its
# page, but the penalty rules sit near the bottom of theirs — a head-only window
# silently drops them and the model fills the gap with a plausible zero.
MANDATION_ANCHORS = ("qualifying income", "6 April 2026")
SCHEDULE_ANCHORS = ("update deadline", "miss a deadline", "penalty point", "tax year")
VAT_ANCHORS = ("taxable turnover", "30 days", "effective date of registration")

EXTRACTOR_PROMPT = """\
You extract tax rules from official UK government pages.

Rules you must follow:
- Report ONLY figures and dates that appear in the text you are given.
- Never infer, round, or complete a figure from prior knowledge.
- If the page states a threshold as "more than £X", record X.
- Dates must be ISO format YYYY-MM-DD. UK tax phases start on 6 April.
"""

JUDGE_PROMPT = """\
You decide whether a UK tax obligation applies to one specific person.

Rules you must follow:
- Decide using ONLY the supplied rules and the person's stated situation.
- Qualifying income is turnover BEFORE expenses, taken from the PRIOR tax year's
  return. It is not profit and it is not the current year.
- If any fact needed for the decision is unknown, return verdict
  "insufficient_info" and list exactly what is missing. Do NOT guess, and do NOT
  assume a missing figure is small.
- Only return "applies" or "does_not_apply" when the stated facts settle it.
"""


def build_model(model_id: str | None = None, region: str | None = None) -> Model:
    """The one place that knows which provider we talk to.

    Everything downstream is typed against `Model`, so swapping providers stays a
    change to this function alone. Temperature is pinned to zero: both callers do
    extraction and adjudication, where sampling variance is a defect, not variety.
    """
    return BedrockModel(
        model_id=model_id or os.environ.get("AFH_MODEL_ID", DEFAULT_MODEL_ID),
        region_name=region or os.environ.get("AWS_REGION", DEFAULT_REGION),
        temperature=0.0,
    )


def _reasoner(system_prompt: str, schema: type, model: Model | None) -> Agent:
    """Every agent in this file has the same shape: silent, structured, pinned.

    `callback_handler=None` matters more than it looks. Left at its default, the
    model's own narration streams to stdout and lands in the middle of the
    report — and a report that says "I need to determine whether..." before its
    conclusion undercuts the one thing this output has to be, which is scannable.
    Only `structured_output` is ever read, so the prose is pure noise.
    """
    return Agent(
        model=model or build_model(),
        system_prompt=system_prompt,
        structured_output_model=schema,
        callback_handler=None,
    )


def extract_rules(page_text: str, model: Model | None = None) -> MtdRuleSet:
    """Read mandation rules off a primary-source page."""
    agent = _reasoner(EXTRACTOR_PROMPT, MtdRuleSet, model)
    excerpt = select(page_text, MANDATION_ANCHORS)
    result = agent(
        "Extract every Making Tax Digital for Income Tax mandation phase from this page.\n\n"
        f"--- PAGE TEXT ---\n{excerpt.text}"
    )
    return result.structured_output


SCHEDULE_PROMPT = """\
You extract filing deadlines from official UK government pages.

Rules you must follow:
- Report ONLY periods, dates, figures and penalties printed in the text.
- Use the STANDARD quarterly periods (the ones starting 6 April), not the
  calendar-quarter alternative.
- Give days and months as numbers: '6 April to 5 July' has period_end_day 5 and
  period_end_month 7.
- Set grace_tax_year only if the page names a tax year in which late quarterly
  updates carry no penalty. Otherwise leave it null.
- For the penalty threshold use the figure for people REQUIRED to use Making Tax
  Digital, not the figure for volunteers.
- If a figure is not stated in the text, leave it null. Never write 0 to mean
  'not stated' — a zero will be read as a real rule.
"""


def extract_quarterly_rules(page_text: str, model: Model | None = None) -> QuarterlyRules:
    """Read the quarterly update schedule and penalties off a primary-source page."""
    agent = _reasoner(SCHEDULE_PROMPT, QuarterlyRules, model)
    excerpt = select(page_text, SCHEDULE_ANCHORS)
    result = agent(
        "Extract the standard quarterly update periods, their deadlines, whether "
        "updates are cumulative, and the penalty rules from this page.\n\n"
        f"--- PAGE TEXT ---\n{excerpt.text}"
    )
    return result.structured_output


VAT_EXTRACT_PROMPT = """\
You extract VAT registration rules from official UK government pages.

Rules you must follow:
- Report ONLY figures printed in the text you are given.
- The registration threshold is the turnover figure above which a business MUST
  register. If the page says "goes over £X", record X.
- The rolling test looks back over a number of months; record that number.
- The forward-looking test is expressed in days; record that number.
- The registration window is counted from the END OF THE MONTH in which the
  threshold was crossed. Record how many days.
- "Your effective date of registration is the first day of the second month
  after you go over the threshold" means effective_from_months_after is 2.
- If a figure is not stated in the text, leave it null. Never write 0 to mean
  'not stated' — a zero will be read as a real rule and rendered as a deadline
  that has already passed.
"""


def extract_vat_rules(page_text: str, model: Model | None = None) -> VatRules:
    """Read the VAT registration test off a primary-source page."""
    agent = _reasoner(VAT_EXTRACT_PROMPT, VatRules, model)
    excerpt = select(page_text, VAT_ANCHORS)
    result = agent(
        "Extract the VAT registration threshold, the rolling look-back period, "
        "the forward-looking test, the registration deadline and the effective "
        "date rule from this page.\n\n"
        f"--- PAGE TEXT ---\n{excerpt.text}"
    )
    return result.structured_output


VAT_JUDGE_PROMPT = """\
You decide whether UK VAT registration is required for one specific person.

Rules you must follow:
- Decide using ONLY the supplied rules and the person's stated situation.
- The VAT test is a ROLLING one: taxable turnover over the last 12 months, which
  can tip over in any month. It is NOT the prior tax year, and it is NOT the
  figure used for Making Tax Digital. If you are only given a prior-tax-year
  figure, that does not settle the VAT question.
- If any fact needed for the decision is unknown, return verdict
  "insufficient_info" and list exactly what is missing. Do NOT guess, and do NOT
  assume a missing figure is small.
- Someone already registered for VAT has no outstanding registration obligation;
  return "does_not_apply" and say why.
- The forward-looking test turns on what the person EXPECTS. If they have been
  asked and said no, that test is settled — treat it as not triggered and do NOT
  report it as a missing fact. Only report it missing when it is NOT ANSWERED.
- Leave mandatory_from null. Every VAT date is derived arithmetically from the
  month the threshold was crossed, and a date estimated here would contradict
  the computed one. Judge whether it applies; the dates are not yours to set.
- Set obligation to 'VAT registration'.
"""


def judge_vat_applicability(
    rules: VatRules,
    situation: UserSituation,
    today: str,
    model: Model | None = None,
) -> ApplicabilityVerdict:
    """Decide whether the person is required to register for VAT."""
    agent = _reasoner(VAT_JUDGE_PROMPT, ApplicabilityVerdict, model)
    stated = [
        f"registration threshold: £{rules.registration_threshold_gbp:,}"
        if rules.registration_threshold_gbp
        else "registration threshold: NOT EXTRACTED from the source",
        f"rolling look-back: {rules.lookback_months} months"
        if rules.lookback_months
        else "rolling look-back period: NOT EXTRACTED",
        f"forward-looking test: expected to exceed within {rules.forward_look_days} days"
        if rules.forward_look_days
        else "forward-looking test: NOT EXTRACTED",
    ]
    result = agent(
        "Decide whether this person is required to register for VAT.\n\n"
        f"TODAY: {today}\n\n"
        "RULES (extracted from gov.uk):\n" + "\n".join(f"- {s}" for s in stated) + "\n\n"
        f"THIS PERSON: {situation.describe_for_vat()}\n"
    )
    return result.structured_output


def judge_applicability(
    rules: MtdRuleSet,
    situation: UserSituation,
    today: str,
    model: Model | None = None,
) -> ApplicabilityVerdict:
    """Decide whether MTD for Income Tax applies to this person."""
    agent = _reasoner(JUDGE_PROMPT, ApplicabilityVerdict, model)
    phases = "\n".join(
        f"- over £{p.qualifying_income_over_gbp:,} (tested on the {p.tax_year_tested} return)"
        f" becomes mandatory on {p.mandatory_from}"
        for p in rules.phases
    )
    result = agent(
        "Decide whether Making Tax Digital for Income Tax applies to this person.\n\n"
        f"TODAY: {today}\n\n"
        f"RULES (extracted from gov.uk):\n{phases}\n"
        f"Qualifying income is defined as: {rules.qualifying_income_definition}\n"
        f"It applies to: {rules.who_it_applies_to}\n\n"
        f"THIS PERSON: {situation.describe()}\n\n"
        "Set obligation to 'Making Tax Digital for Income Tax'."
    )
    return result.structured_output
