"""Text-to-speech via a local Kokoro-FastAPI container.

Kokoro-82M is Apache 2.0, ~82M parameters, and runs comfortably faster than
real time on CPU — no GPU, no API key, no per-character billing. The container
exposes an OpenAI-shaped `/v1/audio/speech` endpoint, which is the reason to
prefer it over calling the model directly: swapping to OpenAI or ElevenLabs
later becomes a base-URL change rather than a rewrite.

Start it with:

    docker run -d --name kokoro -p 8880:8880 --restart unless-stopped \\
        ghcr.io/remsky/kokoro-fastapi-cpu:latest
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://localhost:8880/v1"
DEFAULT_VOICE = "af_heart"
DEFAULT_FORMAT = "mp3"

# Long inputs are chunked so a single request can't hang for minutes and so a
# failure costs one chunk rather than the whole episode. Kokoro handles long
# text internally, but bounded requests give better progress and error locality.
MAX_CHUNK_CHARS = 1800


class SpeechError(Exception):
    """Raised when the speech backend is unreachable or returns no audio."""


def health_check(base_url: str = DEFAULT_BASE_URL, timeout: int = 5) -> bool:
    """Is the TTS backend up? Used to fail early with a useful message."""
    root = base_url.rsplit("/v1", 1)[0]
    for path in ("/health", "/v1/models", "/"):
        try:
            response = requests.get(f"{root}{path}", timeout=timeout)
            if response.status_code < 500:
                return True
        except requests.RequestException:
            continue
    return False


def list_voices(base_url: str = DEFAULT_BASE_URL, timeout: int = 10) -> list[str]:
    """Available voice names, or an empty list if the endpoint isn't exposed."""
    try:
        response = requests.get(f"{base_url}/audio/voices", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return []

    if isinstance(payload, dict):
        voices = payload.get("voices") or payload.get("data") or []
    else:
        voices = payload

    if not isinstance(voices, list):
        return []

    # Entries come back either as bare strings or as {"id": ..., "name": ...}
    # depending on the backend version; normalize to plain names.
    names: list[str] = []
    for voice in voices:
        if isinstance(voice, dict):
            name = voice.get("id") or voice.get("name")
            if name:
                names.append(str(name))
        elif voice:
            names.append(str(voice))

    return names


def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split on paragraph then sentence boundaries, never mid-sentence.

    Splitting mid-sentence produces an audible seam because the engine resets
    its prosody at each request, so boundaries have to land where a speaker
    would pause anyway.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}" if current else paragraph
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(paragraph) <= max_chars:
            current = paragraph
            continue

        # Paragraph alone is oversized — fall back to sentence boundaries.
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if len(current) + len(sentence) + 1 <= max_chars:
                current = f"{current} {sentence}" if current else sentence
                continue

            if current:
                chunks.append(current)
                current = ""

            # A single "sentence" can still exceed the limit — unpunctuated
            # text has no sentence boundaries at all. Split on words rather
            # than emitting an oversized request.
            if len(sentence) <= max_chars:
                current = sentence
                continue

            for word in sentence.split():
                if len(current) + len(word) + 1 <= max_chars:
                    current = f"{current} {word}" if current else word
                else:
                    if current:
                        chunks.append(current)
                    current = word

    if current:
        chunks.append(current)

    return chunks


def _speak_chunk(
    text: str,
    base_url: str,
    voice: str,
    audio_format: str,
    speed: float,
    timeout: int,
) -> bytes:
    payload = {
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "response_format": audio_format,
        "speed": speed,
    }

    try:
        response = requests.post(f"{base_url}/audio/speech", json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise SpeechError(
            f"Could not reach the speech backend at {base_url}: {exc}\n"
            "Start it with:  docker run -d --name kokoro -p 8880:8880 "
            "ghcr.io/remsky/kokoro-fastapi-cpu:latest"
        ) from exc

    if response.status_code != 200:
        detail = response.text.strip()[:300]
        raise SpeechError(f"Speech backend returned {response.status_code}: {detail}")

    if not response.content:
        raise SpeechError("Speech backend returned an empty audio body.")

    return response.content


def synthesize(
    text: str,
    out_path: Path,
    base_url: str = DEFAULT_BASE_URL,
    voice: str = DEFAULT_VOICE,
    audio_format: str = DEFAULT_FORMAT,
    speed: float = 1.0,
    timeout: int = 300,
    verbose: bool = True,
) -> Path:
    """Render `text` to an audio file and return its path.

    Chunks are concatenated as raw bytes. That works for MP3 (frame-based, so
    naive concatenation plays fine in every podcast client) but would NOT be
    valid for WAV, where each chunk carries its own 44-byte header. Guarded
    below rather than left as a trap.
    """
    chunks = chunk_text(text)
    if not chunks:
        raise SpeechError("Nothing to synthesize — the script is empty.")

    if len(chunks) > 1 and audio_format.lower() == "wav":
        raise SpeechError(
            "Multi-chunk WAV output isn't supported — each chunk would carry its "
            "own header. Use mp3 (the default) for anything longer than a chunk."
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    audio = bytearray()

    for index, chunk in enumerate(chunks, start=1):
        if verbose and len(chunks) > 1:
            print(f"  Synthesizing part {index}/{len(chunks)}...", flush=True)
        audio.extend(
            _speak_chunk(chunk, base_url, voice, audio_format, speed, timeout)
        )

    out_path.write_bytes(bytes(audio))
    return out_path


def audio_duration(path: Path) -> int | None:
    """Real duration in seconds, read from the file. None if it can't be read.

    Measuring beats estimating from word count: Kokoro narrates at roughly
    166 wpm, so a 150-wpm estimate was overshooting by half a minute on a
    five-minute episode. `itunes:duration` showing a time the audio doesn't
    match is exactly the kind of small wrongness that erodes trust in a feed.
    """
    try:
        from mutagen.mp3 import MP3

        return int(round(MP3(str(path)).info.length))
    except Exception:
        # Any failure here is cosmetic — the caller falls back to an estimate.
        return None


def tag_audio(
    path: Path,
    title: str,
    artist: str = "ytdigest",
    album: str = "ytdigest",
    comment: str = "",
) -> None:
    """Write ID3 tags so the episode shows a real title in a podcast app."""
    try:
        from mutagen.id3 import ID3, TALB, TIT2, TPE1, COMM
        from mutagen.mp3 import MP3

        audio = MP3(str(path))
        if audio.tags is None:
            audio.add_tags()

        tags: ID3 = audio.tags
        tags.add(TIT2(encoding=3, text=title))
        tags.add(TPE1(encoding=3, text=artist))
        tags.add(TALB(encoding=3, text=album))
        if comment:
            # The timestamp list lives here as the always-works fallback, since
            # real MP3 chapter frames are ignored by many players.
            tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))

        audio.save()
    except Exception:
        # Tagging is a nicety; never let it fail an otherwise good episode.
        pass
