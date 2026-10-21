"""Choose which parts of a long page the model actually gets to read.

A fixed head-of-document window quietly drops whatever sits lower down. That is
how a schema field ends up filled with a plausible zero instead of the real
figure — the model was never shown the sentence that contained it.

Selecting windows around anchor phrases keeps the relevant paragraphs regardless
of where they sit, and keeps the prompt small enough for a local model.
"""

from __future__ import annotations

from dataclasses import dataclass

WINDOW_BEFORE = 400
WINDOW_AFTER = 1800


@dataclass(frozen=True)
class Excerpt:
    text: str
    anchors_found: tuple[str, ...]
    anchors_missing: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        """False means the page no longer contains something we rely on."""
        return not self.anchors_missing


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def select(text: str, anchors: tuple[str, ...], head_chars: int = 2500) -> Excerpt:
    """Head of the page plus a window around each anchor phrase.

    The head is always included because tables and definitions tend to sit near
    the top; the anchors pull in whatever else the schema needs.
    """
    spans: list[tuple[int, int]] = [(0, min(head_chars, len(text)))]
    found: list[str] = []
    missing: list[str] = []

    lowered = text.lower()
    for anchor in anchors:
        index = lowered.find(anchor.lower())
        if index < 0:
            missing.append(anchor)
            continue
        found.append(anchor)
        spans.append((max(0, index - WINDOW_BEFORE), min(len(text), index + WINDOW_AFTER)))

    chunks = [text[start:end] for start, end in _merge(spans)]
    return Excerpt(
        text="\n[...]\n".join(chunks),
        anchors_found=tuple(found),
        anchors_missing=tuple(missing),
    )
