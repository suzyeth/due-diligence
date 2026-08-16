"""Which findings the human has already seen.

Without this, an obligation that was missed once resurfaces on every single run
forever — which is precisely the noise this agent exists to avoid. Dropping it
silently would be worse: an unfiled statutory return should never quietly
disappear. So it keeps surfacing until, and only until, a human says they know.

Acknowledging is not the same as doing. The record says "this person has been
told", nothing more.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import DatedObligation, Finding

ACK_FILE = Path(__file__).resolve().parent.parent / "data" / "acknowledged.json"


def obligation_key(obligation: DatedObligation) -> str:
    """Identity of one obligation occurrence — same duty, same due date."""
    return f"{obligation.name}|{obligation.due_on.isoformat()}"


def verdict_key(finding: Finding) -> str:
    """Identity of one judgement — same duty, same answer.

    The verdict is part of the key on purpose. "VAT does not apply to you" and
    "VAT applies to you" are different news about the same duty, so an
    acknowledgement of the first must not silence the second. That is the case
    the product exists for: the day something starts applying.
    """
    return f"verdict|{finding.verdict.obligation}|{finding.verdict.verdict.value}"


def _load() -> dict[str, str]:
    if not ACK_FILE.exists():
        return {}
    try:
        data = json.loads(ACK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_acknowledged() -> frozenset[str]:
    return frozenset(_load())


def _record(key: str, when: datetime | None) -> None:
    records = _load()
    records[key] = (when or datetime.now(timezone.utc)).isoformat()
    ACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACK_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def acknowledge(obligation: DatedObligation, when: datetime | None = None) -> None:
    """Record that the human has been told about this deadline."""
    _record(obligation_key(obligation), when)


def acknowledge_verdict(finding: Finding, when: datetime | None = None) -> None:
    """Record that the human knows this duty applies — or does not.

    Separate from acknowledging a deadline: knowing that MTD applies to you says
    nothing about whether you know a particular quarter is overdue.
    """
    _record(verdict_key(finding), when)


def clear() -> None:
    """Used by tests and by the demo to get back to a known state."""
    if ACK_FILE.exists():
        ACK_FILE.unlink()
