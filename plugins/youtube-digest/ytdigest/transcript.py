"""The normalize stage of the pipeline.

Captions can arrive in three different shapes (YouTube json3, YouTube WebVTT, or
Whisper's own output). Everything downstream should only ever see one shape, so
this module converts all three into a list of `Segment` objects.

This is the same move you make in detection engineering when you map CrowdStrike,
Defender, and SentinelOne events into one alert schema before writing any rules:
normalize once at the edge, and every rule downstream gets simpler.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path


# A "dataclass" is Python shorthand for "a struct that also gets __init__,
# __repr__, and __eq__ written for me." The lines below declare three typed
# fields; the decorator generates the boilerplate. `frozen=True` makes instances
# immutable, so nothing downstream can quietly mutate a segment out from under us.
@dataclass(frozen=True)
class Segment:
    """One timed chunk of speech."""

    start: float  # seconds from the start of the video
    end: float
    text: str


# ---------------------------------------------------------------------------
# Parsers: three input formats in, Segments out
# ---------------------------------------------------------------------------


def parse_json3(raw: str) -> list[Segment]:
    """Parse YouTube's `json3` caption format.

    Shape looks like:
        {"events": [{"tStartMs": 4120, "dDurationMs": 3100,
                     "segs": [{"utf8": "hello "}, {"utf8": "world"}]}]}

    Events with no `segs` key are formatting/positioning noise and get dropped.
    """
    data = json.loads(raw)
    segments: list[Segment] = []

    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue

        # Join the pieces of this event. `.get("utf8", "")` guards against
        # fragments that carry timing but no text.
        text = "".join(seg.get("utf8", "") for seg in segs)
        text = _clean_text(text)
        if not text:
            continue

        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)
        segments.append(
            Segment(
                start=start_ms / 1000.0,
                end=(start_ms + duration_ms) / 1000.0,
                text=text,
            )
        )

    return segments


# WebVTT cue timings look like: 00:01:23.456 --> 00:01:26.789 align:start position:0%
_VTT_TIMING = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
# Inline karaoke-style timing tags YouTube embeds in auto-captions: <00:00:01.234>
_VTT_INLINE_TAG = re.compile(r"<[^>]+>")


def parse_vtt(raw: str) -> list[Segment]:
    """Parse WebVTT captions — the fallback when json3 isn't offered."""
    segments: list[Segment] = []
    lines = raw.splitlines()
    i = 0

    while i < len(lines):
        match = _VTT_TIMING.search(lines[i])
        if not match:
            i += 1
            continue

        # Groups 1-4 are the start h/m/s/ms, groups 5-8 the end.
        g = [int(x) for x in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0

        # Text runs from the next line until a blank line or the next cue.
        i += 1
        buf: list[str] = []
        while i < len(lines) and lines[i].strip() and not _VTT_TIMING.search(lines[i]):
            buf.append(lines[i])
            i += 1

        text = _clean_text(_VTT_INLINE_TAG.sub("", " ".join(buf)))
        if text:
            segments.append(Segment(start=start, end=end, text=text))

    return segments


def _clean_text(text: str) -> str:
    """Undo HTML entities, drop bracketed sound cues, collapse whitespace."""
    text = html.unescape(text)
    text = re.sub(r"\[(music|applause|laughter|inaudible)[^\]]*\]", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Cleanup: YouTube auto-captions repeat themselves
# ---------------------------------------------------------------------------


def dedupe_segments(segments: list[Segment]) -> list[Segment]:
    """Remove the rolling-window duplication in YouTube auto-captions.

    Auto-captions scroll like a teleprompter, so consecutive cues overlap:

        "so the first thing we"
        "so the first thing we noticed was"
        "noticed was that the traffic"

    Naively concatenating gives you every phrase two or three times. For each
    segment we check whether its text is already the tail of what we've kept,
    and if so keep only the genuinely new suffix.
    """
    out: list[Segment] = []
    tail = ""  # the last chunk of accumulated text, used for overlap checks

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue

        # Exact repeat of the previous cue — skip entirely.
        if out and text == out[-1].text:
            continue

        # If the previous cue is a prefix of this one, this cue is the previous
        # cue plus new words. Replace rather than append.
        if out and text.startswith(out[-1].text) and len(text) > len(out[-1].text):
            prev = out[-1]
            out[-1] = Segment(start=prev.start, end=seg.end, text=text)
            tail = text[-200:]
            continue

        # Otherwise strip any leading words that already appear at the end of
        # what we've kept, so overlapping windows don't double up.
        new_text = _strip_overlap(tail, text)
        if not new_text:
            continue

        out.append(Segment(start=seg.start, end=seg.end, text=new_text))
        tail = (tail + " " + new_text)[-200:]

    return out


def _strip_overlap(tail: str, text: str) -> str:
    """If `text` starts with words that end `tail`, return only the new remainder."""
    if not tail:
        return text

    tail_words = tail.split()
    text_words = text.split()

    # Try the longest plausible overlap first and walk down. Capped at 12 words
    # so we never spend real time on this, and so a legitimately repeated short
    # phrase ("right, right") doesn't get eaten.
    max_overlap = min(len(tail_words), len(text_words), 12)
    for n in range(max_overlap, 0, -1):
        if tail_words[-n:] == text_words[:n]:
            return " ".join(text_words[n:])

    return text


# ---------------------------------------------------------------------------
# Chunking: keep prompts inside a sane size
# ---------------------------------------------------------------------------


def chunk_segments(segments: list[Segment], max_words: int = 12000) -> list[list[Segment]]:
    """Split segments into word-bounded windows, never splitting a segment.

    A one-hour talk is roughly 10k words and fits in a single prompt, so most
    videos come back as one chunk. Three-hour podcasts get split, analyzed
    separately, then merged by a reduce pass in `analyze.py`.
    """
    if not segments:
        return []

    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    count = 0

    for seg in segments:
        words = len(seg.text.split())
        # Start a new chunk if adding this segment would blow the budget —
        # but only if the current chunk already has something in it, otherwise
        # a single enormous segment would produce an empty chunk.
        if current and count + words > max_words:
            chunks.append(current)
            current = []
            count = 0
        current.append(seg)
        count += words

    if current:
        chunks.append(current)

    return chunks


def word_count(segments: list[Segment]) -> int:
    return sum(len(seg.text.split()) for seg in segments)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_timestamp(seconds: float) -> str:
    """Seconds -> `H:MM:SS` (or `M:SS` for videos under an hour)."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def as_plain_text(segments: list[Segment]) -> str:
    """Flatten to prose, no timestamps — used for `--transcript-only`."""
    return " ".join(seg.text for seg in segments)


def as_timestamped_text(segments: list[Segment], interval: float = 30.0) -> str:
    """Flatten to prose with a `[H:MM:SS]` marker roughly every `interval` seconds.

    This is what gets fed to the model. The markers are what let it cite real
    timestamps for each highlight instead of guessing — without them, every
    deep link in the final report would be fiction.
    """
    if not segments:
        return ""

    parts: list[str] = []
    next_marker = segments[0].start

    for seg in segments:
        if seg.start >= next_marker:
            parts.append(f"\n[{format_timestamp(seg.start)}]")
            # Advance in whole intervals so a long gap doesn't emit a burst of
            # markers all at once.
            next_marker = seg.start + interval
        parts.append(seg.text)

    return " ".join(parts).strip()


# ---------------------------------------------------------------------------
# Cache: don't refetch what we already have
# ---------------------------------------------------------------------------


def cache_path(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / f"{video_id}.json"


def analysis_cache_path(cache_dir: Path, video_id: str) -> Path:
    return cache_dir / f"{video_id}.analysis.json"


def analysis_fingerprint(model: str, profile_text: str | None, max_words: int) -> str:
    """Identify the inputs that would change the analysis.

    Caching the analysis matters for more than speed. Re-analyzing re-rolls the
    model's occasional refusal on dense technical content, so a cached result
    also makes `--episode` reliable on exactly the videos most likely to trip it.
    Changing the model, the profile, or the chunk size invalidates the cache.
    """
    import hashlib

    material = f"{model}|{max_words}|{profile_text or ''}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def save_analysis(cache_dir: Path, video_id: str, analysis: dict, fingerprint: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "analysis": analysis}
    analysis_cache_path(cache_dir, video_id).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def load_analysis(cache_dir: Path, video_id: str, fingerprint: str) -> dict | None:
    """Return the cached analysis only if it was produced the same way."""
    path = analysis_cache_path(cache_dir, video_id)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict) or payload.get("fingerprint") != fingerprint:
        return None

    analysis = payload.get("analysis")
    return analysis if isinstance(analysis, dict) else None


def save_cache(cache_dir: Path, video_id: str, meta: dict, segments: list[Segment]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "segments": [asdict(s) for s in segments]}
    cache_path(cache_dir, video_id).write_text(json.dumps(payload), encoding="utf-8")


def load_cache(cache_dir: Path, video_id: str) -> tuple[dict, list[Segment]] | None:
    """Return `(meta, segments)` if cached, else `None`.

    `None` here is a sentinel — Python's idiomatic "no value" marker. The caller
    checks `if cached is None:` rather than relying on an exception, because a
    cache miss is an expected outcome, not an error.
    """
    path = cache_path(cache_dir, video_id)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        segments = [Segment(**s) for s in payload["segments"]]
        return payload.get("meta", {}), segments
    except (json.JSONDecodeError, KeyError, TypeError):
        # A corrupt cache file should never be fatal — treat it as a miss.
        return None
