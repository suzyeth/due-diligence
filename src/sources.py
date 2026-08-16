"""Primary-source fetching, snapshotting and blindness detection.

Design rule that everything else depends on: a failed fetch is an *event*, never
a silent no-op. If the agent cannot see the source, it must say so rather than
fall back on what it remembers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .models import SourceHealth

USER_AGENT = "agents-for-humans-spike/0.1 (hackathon project; contact via repo)"
FETCH_TIMEOUT_SECONDS = 20.0
STALE_AFTER_DAYS = 7

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
HEALTH_FILE = DATA_DIR / "source_health.json"


@dataclass(frozen=True)
class Source:
    """A primary source we are willing to derive conclusions from."""

    key: str
    url: str
    expect_text: str
    """A phrase that must appear in the fetched page. If it disappears, the page
    was restructured and any extraction from it is suspect."""


SOURCES: tuple[Source, ...] = (
    Source(
        key="mtd_mandation",
        url="https://www.gov.uk/guidance/use-making-tax-digital-for-income-tax/before-you-use-this-guide",
        expect_text="qualifying income",
    ),
    Source(
        key="mtd_quarterly",
        url="https://www.gov.uk/guidance/use-making-tax-digital-for-income-tax/send-quarterly-updates",
        expect_text="quarterly update",
    ),
)


@dataclass(frozen=True)
class FetchResult:
    source: Source
    ok: bool
    text: str
    fetched_at: datetime
    digest: str
    error: str | None = None
    structure_changed: bool = False


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    main = soup.find("main") or soup
    return "\n".join(line.strip() for line in main.get_text("\n").splitlines() if line.strip())


def fetch(source: Source) -> FetchResult:
    """Fetch one source. Never raises — failure is data, not an exception."""
    now = datetime.now(timezone.utc)
    try:
        response = httpx.get(
            source.url,
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any failure means "we are blind"
        return FetchResult(
            source=source,
            ok=False,
            text="",
            fetched_at=now,
            digest="",
            error=f"{type(exc).__name__}: {exc}",
        )

    text = _html_to_text(response.text)
    structure_changed = source.expect_text.lower() not in text.lower()
    return FetchResult(
        source=source,
        ok=not structure_changed,
        text=text,
        fetched_at=now,
        digest=_digest(text),
        error=(
            f"expected phrase {source.expect_text!r} not found — page may have been restructured"
            if structure_changed
            else None
        ),
        structure_changed=structure_changed,
    )


# ── snapshots ─────────────────────────────────────────────────────────────────


def snapshot_path(key: str) -> Path:
    return SNAPSHOT_DIR / f"{key}.json"


def load_snapshot(key: str) -> dict | None:
    path = snapshot_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_snapshot(key: str, payload: dict) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path(key).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


# ── health ────────────────────────────────────────────────────────────────────


def _load_health_raw() -> dict:
    if not HEALTH_FILE.exists():
        return {}
    try:
        return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_health(key: str) -> SourceHealth:
    raw = _load_health_raw().get(key, {})
    last = raw.get("last_success")
    return SourceHealth(
        source_url=raw.get("source_url", ""),
        last_success=datetime.fromisoformat(last) if last else None,
        consecutive_failures=int(raw.get("consecutive_failures", 0)),
        last_error=raw.get("last_error"),
    )


def record_health(result: FetchResult) -> SourceHealth:
    """Update the health ledger from one fetch and return the new state."""
    raw = _load_health_raw()
    previous = raw.get(result.source.key, {})

    if result.ok:
        entry = {
            "source_url": result.source.url,
            "last_success": result.fetched_at.isoformat(),
            "consecutive_failures": 0,
            "last_error": None,
        }
    else:
        entry = {
            "source_url": result.source.url,
            "last_success": previous.get("last_success"),
            "consecutive_failures": int(previous.get("consecutive_failures", 0)) + 1,
            "last_error": result.error,
        }

    raw[result.source.key] = entry
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return read_health(result.source.key)


def is_stale(health: SourceHealth, now: datetime) -> bool:
    if health.last_success is None:
        return True
    return (now - health.last_success).days >= STALE_AFTER_DAYS
