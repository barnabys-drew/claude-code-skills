"""The ingest stage: URL in, video metadata and raw captions out.

We drive yt-dlp as a Python library rather than shelling out to the binary. That
keeps errors as real exceptions we can catch by type instead of exit codes and
scraped stderr, and it avoids a second process for every run.
"""

from __future__ import annotations

from urllib.parse import urlparse, parse_qs

import requests

from .transcript import Segment, parse_json3, parse_vtt, dedupe_segments


class FetchError(Exception):
    """Raised when a video can't be reached or has no usable captions."""


class _SilentLogger:
    """Swallow yt-dlp's own console output.

    `quiet` alone doesn't stop yt-dlp printing `ERROR: ...` to stderr, which
    would double up with the clean message we raise ourselves. We catch the
    exception and phrase the error once, so its logger goes nowhere.
    """

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


# YouTube video IDs are always 11 characters of [A-Za-z0-9_-].
_ID_LENGTH = 11
_VALID_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def parse_video_id(url: str) -> str:
    """Extract the 11-character video ID from any common YouTube URL shape.

    Handles:
        https://www.youtube.com/watch?v=ID&t=42s
        https://youtu.be/ID?si=tracking
        https://www.youtube.com/shorts/ID
        https://www.youtube.com/live/ID
        https://www.youtube.com/embed/ID
        ID                              (a bare ID, for convenience)

    Raises `ValueError` with a readable message on anything else — a clear
    failure here is worth more than a confusing one three stages downstream.
    """
    url = url.strip()

    # Allow a bare video ID so you can paste just the ID if that's what you have.
    if _looks_like_id(url):
        return url

    # urlparse needs a scheme to populate .netloc, so add one if it's missing.
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host not in _YOUTUBE_HOSTS:
        raise ValueError(f"Not a YouTube URL: {parsed.netloc or url!r}")

    # youtu.be/ID — the ID is the whole path.
    if host in ("youtu.be", "www.youtu.be"):
        candidate = parsed.path.lstrip("/").split("/")[0]
        if _looks_like_id(candidate):
            return candidate
        raise ValueError(f"Could not find a video ID in: {url!r}")

    # youtube.com/watch?v=ID — the ID is a query parameter.
    query = parse_qs(parsed.query)
    if "v" in query and _looks_like_id(query["v"][0]):
        return query["v"][0]

    # youtube.com/{shorts,live,embed,v}/ID — the ID is the path segment after
    # a known prefix.
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        if _looks_like_id(parts[1]):
            return parts[1]

    raise ValueError(f"Could not find a video ID in: {url!r}")


def _looks_like_id(value: str) -> bool:
    return len(value) == _ID_LENGTH and set(value) <= _VALID_ID_CHARS


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def timestamp_url(video_id: str, seconds: float) -> str:
    """A deep link that opens the video already seeked to `seconds`."""
    return f"https://youtu.be/{video_id}?t={int(seconds)}"


def fetch_info(video_id: str) -> dict:
    """Pull metadata and available caption tracks. No media is downloaded."""
    import yt_dlp  # imported here so `--help` doesn't pay the import cost

    options = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "json3",
    }

    try:
        # `with` is a context manager: it guarantees the YoutubeDL object gets
        # cleaned up (sockets closed) even if extract_info raises.
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(canonical_url(video_id), download=False)
    except Exception as exc:  # yt-dlp raises a family of DownloadError subclasses
        raise FetchError(_friendly_error(exc)) from exc


def _friendly_error(exc: Exception) -> str:
    """Turn yt-dlp's stderr-flavored messages into one readable line."""
    message = str(exc)
    lowered = message.lower()

    if "private video" in lowered:
        return "This video is private."
    if "video unavailable" in lowered or "has been removed" in lowered:
        return "This video is unavailable or has been removed."
    if "age" in lowered and "confirm" in lowered:
        return "This video is age-restricted and can't be fetched without sign-in."
    if "sign in" in lowered and "bot" in lowered:
        return (
            "YouTube is asking this client to sign in (bot check). "
            "Try again later, or pass --cookies-from-browser to yt-dlp."
        )
    if "unable to download" in lowered or "urlopen" in lowered:
        return f"Network error reaching YouTube: {message.strip()}"

    return message.strip()


def extract_metadata(info: dict) -> dict:
    """Keep only the fields we actually display, so the cache stays small."""
    return {
        "id": info.get("id", ""),
        "title": info.get("title", "(untitled)"),
        "uploader": info.get("uploader") or info.get("channel") or "(unknown)",
        "duration": info.get("duration") or 0,
        "upload_date": info.get("upload_date", ""),
    }


# Preference order matters: human-written captions are markedly cleaner than
# ASR output — real punctuation, correct proper nouns, no rolling duplication.
# We take a manual track over an automatic one every time.
_MANUAL_PREFERENCE = ["en", "en-US", "en-GB", "en-CA", "en-AU"]
_AUTO_PREFERENCE = ["en", "en-orig", "en-US", "en-GB"]


def select_caption_track(info: dict) -> tuple[list[dict], str] | None:
    """Pick the best English caption track.

    Returns `(formats, source_label)` or `None` if there's no English track,
    which is the signal for the caller to fall back to Whisper.
    """
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}

    for lang in _MANUAL_PREFERENCE:
        if manual.get(lang):
            return manual[lang], f"youtube captions ({lang})"

    # Some channels label manual English tracks with a suffix, e.g. "en-US-ext".
    for lang, formats in manual.items():
        if lang.lower().startswith("en") and formats:
            return formats, f"youtube captions ({lang})"

    for lang in _AUTO_PREFERENCE:
        if automatic.get(lang):
            return automatic[lang], f"youtube auto-captions ({lang})"

    return None


def download_captions(formats: list[dict]) -> list[Segment]:
    """Download and parse the best available format from a caption track.

    Prefers json3 (trivially parseable, exact timings); falls back to WebVTT,
    which YouTube sometimes serves instead.
    """
    by_ext = {fmt.get("ext"): fmt for fmt in formats if fmt.get("url")}

    for ext, parser in (("json3", parse_json3), ("vtt", parse_vtt)):
        fmt = by_ext.get(ext)
        if not fmt:
            continue
        try:
            response = requests.get(fmt["url"], timeout=30)
            response.raise_for_status()
            segments = parser(response.text)
        except requests.RequestException as exc:
            raise FetchError(f"Failed to download captions: {exc}") from exc

        if segments:
            return dedupe_segments(segments)

    available = ", ".join(sorted(by_ext)) or "none"
    raise FetchError(f"No parseable caption format found (available: {available})")


def get_transcript(video_id: str) -> tuple[dict, list[Segment], str] | None:
    """Full caption path: metadata + segments + a label for where they came from.

    Returns `None` when the video has no English captions at all — the caller
    decides whether that means "fall back to Whisper" or "fail".
    """
    info = fetch_info(video_id)
    meta = extract_metadata(info)

    selected = select_caption_track(info)
    if selected is None:
        return None

    formats, source = selected
    return meta, download_captions(formats), source
