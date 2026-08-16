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

from .models import DatedObligation

ACK_FILE = Path(__file__).resolve().parent.parent / "data" / "acknowledged.json"


def obligation_key(obligation: DatedObligation) -> str:
    """Identity of one obligation occurrence — same duty, same due date."""
    return f"{obligation.name}|{obligation.due_on.isoformat()}"


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


def acknowledge(obligation: DatedObligation, when: datetime | None = None) -> None:
    """Record that the human has been told about this one."""
    records = _load()
    records[obligation_key(obligation)] = (when or datetime.now(timezone.utc)).isoformat()
    ACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACK_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def clear() -> None:
    """Used by tests and by the demo to get back to a known state."""
    if ACK_FILE.exists():
        ACK_FILE.unlink()
