"""Deterministic timestamp correction.

Language models are bad at counting. Asked to cite the timestamp of a moment in
a 48-minute transcript, the model reliably picks a marker that is *near* the
right place but often 60-90 seconds off — which makes every deep link land in
the wrong passage.

So we don't let the model decide timestamps at all. It returns a short verbatim
`anchor` phrase for each highlight, and this module finds where that phrase
actually occurs in the transcript and derives the timestamp from the segment
that contains it. The model does what it's good at (judging what matters);
string search does what it's good at (knowing exactly where something is).

Same instinct as never trusting a log's self-reported timestamp when you have
the collector's receipt time.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .transcript import Segment

# Shortest prefix we'll accept when the full anchor doesn't match. Below about
# four words, matches start being coincidental rather than real.
MIN_ANCHOR_WORDS = 4
_WINDOW_WORDS = 5


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Auto-captions have no punctuation and inconsistent casing, while the model
    tends to tidy quotes up when it repeats them. Normalizing both sides makes
    those cosmetic differences stop mattering.
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class SearchIndex:
    """Normalized transcript text plus a map from character offset back to time."""

    text: str = ""
    offsets: list[int] = field(default_factory=list)
    starts: list[float] = field(default_factory=list)

    def time_at(self, offset: int) -> float:
        """Which segment contains this character offset?

        `bisect_right` finds the insertion point in the sorted offsets list;
        stepping back one gives the last segment that begins at or before the
        offset. That's a binary search — O(log n) instead of scanning all 1300
        segments for every lookup.
        """
        if not self.offsets:
            return 0.0
        index = max(bisect_right(self.offsets, offset) - 1, 0)
        return self.starts[index]


def build_index(segments: list[Segment]) -> SearchIndex:
    """Flatten segments into one normalized string, remembering where each began."""
    index = SearchIndex()
    parts: list[str] = []
    position = 0

    for seg in segments:
        normalized = normalize(seg.text)
        if not normalized:
            continue
        if parts:
            position += 1  # account for the space we'll join with
        index.offsets.append(position)
        index.starts.append(seg.start)
        parts.append(normalized)
        position += len(normalized)

    index.text = " ".join(parts)
    return index


def locate(index: SearchIndex, phrase: str) -> float | None:
    """Find when `phrase` was said. Returns seconds, or None if not found.

    Three passes, most-precise first:
      1. the whole phrase
      2. progressively shorter prefixes, down to MIN_ANCHOR_WORDS
      3. any 5-word window inside the phrase

    Passes 2 and 3 exist because the model sometimes extends an anchor past
    what was actually said, or starts it a word or two early.
    """
    if not index.text or not phrase:
        return None

    words = normalize(phrase).split()
    if len(words) < MIN_ANCHOR_WORDS:
        return None

    for length in range(len(words), MIN_ANCHOR_WORDS - 1, -1):
        offset = index.text.find(" ".join(words[:length]))
        if offset != -1:
            return index.time_at(offset)

    for start in range(1, max(len(words) - _WINDOW_WORDS + 1, 1)):
        offset = index.text.find(" ".join(words[start : start + _WINDOW_WORDS]))
        if offset != -1:
            return index.time_at(offset)

    return None


def verify_analysis(analysis: dict, segments: list[Segment]) -> tuple[dict, dict]:
    """Replace model-guessed timestamps with located ones.

    Returns `(corrected_analysis, stats)`. Each highlight and quote gains a
    `verified` boolean so the renderer can mark anything we couldn't confirm,
    rather than presenting a guess as if it were a fact.
    """
    index = build_index(segments)
    stats = {"verified": 0, "unverified": 0}

    def resolve(item: dict, *keys: str) -> dict:
        """Try each candidate field as an anchor until one locates."""
        for key in keys:
            value = item.get(key)
            if not isinstance(value, str):
                continue
            found = locate(index, value)
            if found is not None:
                stats["verified"] += 1
                return {**item, "timestamp_s": found, "verified": True}

        stats["unverified"] += 1
        return {**item, "verified": False}

    corrected = dict(analysis)

    highlights = analysis.get("highlights") or []
    if isinstance(highlights, list):
        # Anchor first; fall back to the detail text, which is often close
        # enough to something actually said to find a match.
        corrected["highlights"] = [
            resolve(h, "anchor", "detail") if isinstance(h, dict) else h for h in highlights
        ]

    quotes = analysis.get("notable_quotes") or []
    if isinstance(quotes, list):
        corrected["notable_quotes"] = [
            resolve(q, "quote") if isinstance(q, dict) else q for q in quotes
        ]

    return corrected, stats
