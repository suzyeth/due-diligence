"""A web front for the same agent the CLI drives.

Deliberately thin. Every judgement, every date and every rule still comes from
`src/`; this module owns nothing but HTML. If a behaviour can only be observed
through the browser, it is in the wrong place.

Two differences from the CLI, both forced by the medium:

*Stateless.* There is no stored profile and no acknowledgement ledger, because
those are per-person and this is a shared demo. Each request carries its own
answers. The silence contract still shows up — as the calm "nothing needs you"
result — but the acknowledge-and-go-quiet loop is a CLI thing, where it belongs.

*Bounded.* A public URL that calls a model is a public URL that spends money, so
requests are rate limited per container and the expensive half of the work is
cached against the page digest by `src.extraction_cache`.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.models import UserSituation
from src.pipeline import run

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(
    title="Due Diligence",
    description="Which UK tax obligations actually apply to you, with a source for every claim.",
    docs_url=None,
    redoc_url=None,
)

# ── spend guard ───────────────────────────────────────────────────────────────
# Crude on purpose: an in-memory window per container, no shared store. It will
# not stop a determined abuser, and it is not meant to — it stops a crawler or a
# stuck retry loop from quietly running up a model bill on a demo nobody is
# watching. Lambda reserved concurrency is the real backstop.
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW_SECONDS = 300
_recent: deque[float] = deque()


def _rate_limited() -> bool:
    now = time.monotonic()
    while _recent and now - _recent[0] > RATE_LIMIT_WINDOW_SECONDS:
        _recent.popleft()
    if len(_recent) >= RATE_LIMIT_REQUESTS:
        return True
    _recent.append(now)
    return False


def _optional_int(raw: str) -> int | None:
    """Blank means "I don't know", and that has to survive the round trip.

    Coercing a blank field to 0 here would turn "I don't know my turnover" into
    "my turnover is zero" — a confident, wrong, and entirely silent answer.
    """
    cleaned = raw.replace(",", "").replace("£", "").strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _optional_month(raw: str) -> date | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        year, month = raw.split("-")[:2]
        return date(int(year), int(month), 1)
    except (ValueError, IndexError):
        return None


def _tristate(raw: str) -> bool | None:
    return {"yes": True, "no": False}.get(raw, None)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "index.html", {"today": date.today()})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/check", response_class=HTMLResponse)
def check(
    request: Request,
    is_sole_trader: str = Form(default=""),
    has_property_income: str = Form(default=""),
    prior_year_turnover: str = Form(default=""),
    prior_year_label: str = Form(default=""),
    already_signed_up_to_mtd: str = Form(default=""),
    already_vat_registered: str = Form(default=""),
    rolling_turnover: str = Form(default=""),
    vat_crossed_in: str = Form(default=""),
    expects_to_exceed: str = Form(default=""),
) -> HTMLResponse:
    if _rate_limited():
        return TEMPLATES.TemplateResponse(
            request,
            "busy.html",
            {},
            status_code=429,
        )

    situation = UserSituation(
        is_sole_trader=is_sole_trader == "yes",
        has_property_income=has_property_income == "yes",
        prior_year_turnover_gbp=_optional_int(prior_year_turnover),
        prior_year_label=prior_year_label.strip() or None,
        already_signed_up_to_mtd=already_signed_up_to_mtd == "yes",
        already_vat_registered=already_vat_registered == "yes",
        rolling_12m_turnover_gbp=_optional_int(rolling_turnover),
        vat_threshold_crossed_in=_optional_month(vat_crossed_in),
        expects_to_exceed_vat_threshold_soon=_tristate(expects_to_exceed),
    )

    # Empty acknowledgements on purpose: see the docstring on `run`. This
    # deployment is shared, so nobody inherits anybody else's 'I've seen it'.
    report = run(situation, acknowledged=frozenset())
    return TEMPLATES.TemplateResponse(
        request,
        "report.html",
        {
            "report": report,
            "situation": situation,
            "checked_at": datetime.now(timezone.utc),
        },
    )
