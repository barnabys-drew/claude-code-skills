"""Local speech-to-text, used only when a video has no English captions.

Everything here is deliberately lazy: `faster_whisper` pulls in ctranslate2 and
onnxruntime, which take a noticeable moment to import. Videos that have captions
should never pay that cost, so the import lives inside the function rather than
at module top level.

We download audio as m4a and hand the file straight to faster-whisper, whose
bundled PyAV decodes it. That's why this works without a system `ffmpeg`
install — there's no remux or format-conversion step in the path.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .fetch import _SilentLogger, FetchError, canonical_url, extract_metadata
from .transcript import Segment

# Available sizes, smallest first. `base.en` is the default: ~150MB, good enough
# for summarization, and tolerable on CPU. `small.en` is noticeably better on
# technical jargon if you're willing to wait roughly 2-3x longer.
MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3"]
DEFAULT_MODEL = "base.en"


def download_audio(video_id: str, cache_dir: Path) -> tuple[Path, dict]:
    """Download audio-only and return `(path, metadata)`.

    The format string asks for m4a first and falls back to whatever the best
    audio-only stream is. We never request a postprocessor, so yt-dlp writes
    the container as-is and no ffmpeg binary is needed.
    """
    import yt_dlp

    audio_dir = cache_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    options = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(audio_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "logger": _SilentLogger(),
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(canonical_url(video_id), download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as exc:
        raise FetchError(f"Failed to download audio: {exc}") from exc

    if not path.exists():
        # yt-dlp occasionally settles on a different extension than the template
        # predicted; find whatever it actually wrote for this video ID.
        matches = list(audio_dir.glob(f"{video_id}.*"))
        if not matches:
            raise FetchError("Audio download reported success but produced no file.")
        path = matches[0]

    return path, extract_metadata(info)


def transcribe(
    audio_path: Path,
    model_name: str = DEFAULT_MODEL,
    verbose: bool = True,
) -> list[Segment]:
    """Run faster-whisper over an audio file and return normalized segments."""
    from faster_whisper import WhisperModel

    if verbose:
        print(
            f"  Loading Whisper model {model_name!r} "
            f"(first run downloads it, ~150MB for base.en)...",
            file=sys.stderr,
            flush=True,
        )

    # int8 quantization on CPU: roughly 4x faster than float32 with a barely
    # perceptible accuracy cost for this use case.
    model = WhisperModel(model_name, device="cpu", compute_type="int8")

    # vad_filter drops silence before it reaches the model. On a talk with long
    # pauses that's a large speedup, and it prevents Whisper's habit of
    # hallucinating text during silence.
    raw_segments, info = model.transcribe(str(audio_path), vad_filter=True)

    if verbose:
        detected = getattr(info, "language", "?")
        print(f"  Transcribing (detected language: {detected})...", file=sys.stderr, flush=True)

    # `raw_segments` is a generator — faster-whisper transcribes lazily as you
    # iterate, so this loop is where the actual work happens. Wrapping it in
    # list() forces it to run to completion.
    segments = [
        Segment(start=s.start, end=s.end, text=s.text.strip())
        for s in raw_segments
        if s.text.strip()
    ]

    return segments


def transcribe_video(
    video_id: str,
    cache_dir: Path,
    model_name: str = DEFAULT_MODEL,
    keep_audio: bool = False,
    verbose: bool = True,
) -> tuple[dict, list[Segment], str]:
    """Whole fallback path: download audio, transcribe, clean up."""
    if verbose:
        print("  No captions available — falling back to local Whisper.", file=sys.stderr)
        print("  This runs on CPU and can take several minutes.", file=sys.stderr, flush=True)

    audio_path, meta = download_audio(video_id, cache_dir)

    try:
        segments = transcribe(audio_path, model_name=model_name, verbose=verbose)
    finally:
        # `finally` runs whether or not transcription raised, so a crash mid-way
        # doesn't leave a stray 50MB audio file behind.
        if not keep_audio and audio_path.exists():
            audio_path.unlink()

    if not segments:
        raise FetchError("Whisper produced no transcript — the video may have no speech.")

    return meta, segments, f"whisper ({model_name})"
