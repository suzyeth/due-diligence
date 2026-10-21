"""Extract a page's rules, unless the page is byte-for-byte what it was.

Two reasons this exists, and the second is the more important one.

It is cheap: extraction is the expensive half of a run, and a daemon that wakes
regularly would otherwise re-read an unchanged gov.uk page every single time.

It is also *stable*. A model asked the same question twice may phrase the answer
differently, and rules that drift while their source sits still are
indistinguishable from a real rule change — which is the one signal this product
must never cry wolf on. Pinning the answer to the page digest means a reported
change is always a real change.

Note what is NOT skipped: the fetch still happens, the digest is still compared,
health is still recorded. Only the model call is avoided. The agent never stops
looking at the source.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .sources import FetchResult, load_snapshot, save_snapshot

T = TypeVar("T", bound=BaseModel)


def extract_or_reuse(
    key: str,
    result: FetchResult,
    schema: type[T],
    extract: Callable[[str], T],
) -> tuple[T, bool]:
    """Return `(rules, was_reused)` for this page.

    `extract` is called only when the stored answer cannot be trusted: no
    snapshot, a different digest, a snapshot written before this cache existed,
    or one that no longer validates against the schema. Every one of those falls
    back to reading the page again, which is the safe direction to fail in.
    """
    snapshot = load_snapshot(key)
    stored = (snapshot or {}).get("rules")

    if snapshot and stored and snapshot.get("digest") == result.digest:
        try:
            return schema.model_validate(stored), True
        except ValidationError:
            pass  # schema moved on since this was written; read it again

    rules = extract(result.text)
    save_snapshot(
        key,
        {
            "fetched_at": result.fetched_at,
            "digest": result.digest,
            "rules": rules.model_dump(),
        },
    )
    return rules, False
