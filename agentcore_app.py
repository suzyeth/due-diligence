"""The unattended arm: the agent as something that wakes up on its own.

Everything else in this project runs because a person asked it to. This is the
part the product description actually promises — it runs in the background and
surfaces only when there is a real decision to make.

Two pieces are needed for that and neither is in the Strands SDK, which has no
scheduling primitive:

*Something to wake it.* An EventBridge schedule invokes this runtime daily with
the person's situation as the payload.

*A rule for when to speak.* The CLI ends a notification when a human
acknowledges it; nobody is here to do that. So `src.notify` compares a
fingerprint of the reasons against the last one sent, held in S3, and mail goes
out only when that set changes. Without it, one overdue deadline would produce
an identical email every morning until the deadline passed.

Entry point for Amazon Bedrock AgentCore Runtime. The agent itself is the same
`src.pipeline.run` the CLI and the web page call; this module owns only waking,
state and delivery.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from src.models import UserSituation
from src.notify import fingerprint, should_notify
from src.pipeline import run

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("due-diligence")

app = BedrockAgentCoreApp()

STATE_BUCKET = os.environ.get("AFH_STATE_BUCKET", "")
TOPIC_ARN = os.environ.get("AFH_TOPIC_ARN", "")
STATE_KEY = "last-notified.json"


def _load_last_sent(person: str) -> str | None:
    """The fingerprint of the last message sent to this person, if any.

    A missing or unreadable object means "nothing sent yet", which errs toward
    delivering a message. Erring the other way would silently drop the first
    notification after any storage hiccup, and the whole point is that the first
    one arrives.
    """
    if not STATE_BUCKET:
        return None
    try:
        body = boto3.client("s3").get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)["Body"].read()
        return json.loads(body).get(person)
    except (ClientError, ValueError, KeyError):
        return None


def _save_last_sent(person: str, value: str) -> None:
    if not STATE_BUCKET:
        return
    s3 = boto3.client("s3")
    try:
        body = s3.get_object(Bucket=STATE_BUCKET, Key=STATE_KEY)["Body"].read()
        state = json.loads(body)
    except (ClientError, ValueError):
        state = {}
    state[person] = value
    s3.put_object(
        Bucket=STATE_BUCKET,
        Key=STATE_KEY,
        Body=json.dumps(state, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def _compose(report, reasons: list[str]) -> tuple[str, str]:
    """Subject and body. Written to be readable on a phone lock screen.

    The subject carries the single most urgent thing rather than a count,
    because "Due Diligence: 4 items" tells you nothing you can act on.
    """
    subject = f"Due Diligence: {reasons[0][:80]}"
    lines = ["Something in your tax obligations needs you.", ""]
    lines += [f"  - {r}" for r in reasons]

    if report.obligations:
        lines += ["", "Dates:"]
        for o in report.unacknowledged or report.obligations:
            when = (
                f"{abs(o.days_away)} days ago"
                if o.days_away < 0
                else ("today" if o.days_away == 0 else f"in {o.days_away} days")
            )
            lines += [f"  {o.due_on:%Y-%m-%d} ({when})  {o.name}",
                      f"      if missed: {o.consequence_if_missed}"]

    if report.findings:
        lines += ["", "Sources checked:"]
        seen = {f.evidence.source_url for f in report.findings}
        lines += [f"  {url}" for url in sorted(seen)]

    lines += [
        "",
        "Not tax advice. This reports which obligations appear to apply and when",
        "they fall due, citing gov.uk for each. It does not calculate tax owed.",
        "",
        "You will not get this message again unless what needs you changes.",
    ]
    return subject, "\n".join(lines)


@app.entrypoint
def invoke(payload: dict) -> dict:
    """One scheduled check.

    The payload carries the person's situation, so the schedule is the only
    thing that has to know who is being watched. Returns a summary either way —
    a quiet run is a successful run, and the caller should be able to see that
    it happened.
    """
    person = payload.get("person", "default")
    raw = payload.get("situation")
    if not raw:
        return {"error": "payload must contain a 'situation' object"}

    situation = UserSituation.model_validate(raw)
    # No acknowledgement ledger out here: nobody can acknowledge anything, and
    # src.notify is what stops this repeating instead.
    report = run(situation, acknowledged=frozenset())
    reasons = report.interrupt_reasons()

    last_sent = _load_last_sent(person)
    notifying = should_notify(reasons, last_sent)

    # Three distinct silences, logged distinctly. "Nothing to say", "said it
    # already" and "no way to say it" look identical from outside and mean very
    # different things when someone is working out why no mail arrived.
    if not reasons:
        log.info("nothing needs a human")
    elif not notifying:
        log.info("unchanged since the last notification, staying quiet (%d reasons)", len(reasons))
    elif not TOPIC_ARN:
        log.warning(
            "WOULD NOTIFY but AFH_TOPIC_ARN is unset — %d reasons going nowhere", len(reasons)
        )
        notifying = False
    else:
        subject, body = _compose(report, reasons)
        boto3.client("sns").publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body)
        log.info("notified %s: %s", person, reasons[0])

    # Recorded even on a quiet run, so a problem that clears and returns is news.
    _save_last_sent(person, fingerprint(reasons))

    return {
        "person": person,
        "needs_human": report.should_interrupt_human,
        "notified": notifying,
        "reasons": reasons,
        "obligations": [
            {"name": o.name, "due_on": o.due_on.isoformat(), "status": o.status.value}
            for o in report.obligations
        ],
        "blind_reason": report.blind_reason,
    }


if __name__ == "__main__":
    app.run()
