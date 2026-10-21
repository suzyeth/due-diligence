"""Deciding whether an unattended run has anything worth sending.

On the command line a person ends a notification by acknowledging it. A
scheduled run has no person in the loop, so the same rule cannot apply — and
without a rule it would mail the same overdue deadline every morning, which is
exactly the nagging this product replaces, relocated to your inbox.

The rule here is: notify on *change*. Fingerprint the set of reasons the agent
wants a human; send only when that set differs from the last one sent. A quiet
run still records its state, so a problem that clears and later returns is news
again — but clearing itself is never mailed. An agent that writes to say it has
nothing to say has missed the point.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def fingerprint(reasons: Iterable[str]) -> str:
    """A stable identity for one set of reasons.

    Sorted before hashing because reason order falls out of dict and set
    iteration upstream, and an ordering change is not news.
    """
    joined = "\n".join(sorted(reasons))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def should_notify(reasons: list[str], last_sent: str | None) -> bool:
    """Whether this run has something a person has not already been told.

    No reasons means no message, whatever the previous state was — including the
    case where a problem has just cleared.
    """
    if not reasons:
        return False
    return fingerprint(reasons) != last_sent
