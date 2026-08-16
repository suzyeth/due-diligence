"""Where the answers live between runs.

An agent that has to be re-briefed on every invocation is a script with extra
steps. The whole premise here is that it is asked once and then gets on with it,
so the answers have to outlive the process.

Stored as the same Pydantic model the rest of the code uses, so a profile that
round-trips is a profile the pipeline can already consume — there is no second
representation to keep in sync.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .models import UserSituation

PROFILE_FILE = Path(__file__).resolve().parent.parent / "data" / "profile.json"


def profile_exists() -> bool:
    return PROFILE_FILE.exists()


def load_profile() -> UserSituation | None:
    """The stored answers, or None if there are none worth trusting.

    A corrupt or outdated file returns None rather than raising: the caller's
    recovery is to ask again, which is a better outcome than a stack trace on a
    scheduled run nobody is watching.
    """
    if not PROFILE_FILE.exists():
        return None
    try:
        return UserSituation.model_validate_json(
            PROFILE_FILE.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def save_profile(situation: UserSituation) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_FILE.write_text(
        situation.model_dump_json(indent=2), encoding="utf-8"
    )


def clear_profile() -> None:
    if PROFILE_FILE.exists():
        PROFILE_FILE.unlink()
