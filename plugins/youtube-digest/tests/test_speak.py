"""TTS client. No container required — requests is monkeypatched."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from ytdigest import speak
from ytdigest.speak import MAX_CHUNK_CHARS, SpeechError, chunk_text, list_voices, synthesize


def _post_stub(monkeypatch, content=b"ID3audio", status=200):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return SimpleNamespace(status_code=status, content=content, text="err")

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


# --------------------------------------------------------------------------
# Chunking — boundaries must land where a speaker would pause
# --------------------------------------------------------------------------


def test_short_text_is_one_chunk():
    assert chunk_text("A short script.") == ["A short script."]


def test_empty_text_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_long_text_splits_on_paragraph_boundaries():
    para = "word " * 100
    chunks = chunk_text(f"{para}\n\n{para}\n\n{para}", max_chars=600)

    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)


def test_oversized_paragraph_falls_back_to_sentences():
    text = "This is a sentence. " * 60
    chunks = chunk_text(text, max_chars=300)

    assert all(len(c) <= 300 for c in chunks)
    # Every chunk should end at a sentence boundary, not mid-word.
    assert all(c.rstrip().endswith(".") for c in chunks)


def test_chunking_loses_no_words():
    text = "\n\n".join(f"Paragraph number {i} has some words in it." for i in range(40))
    rejoined = " ".join(chunk_text(text, max_chars=200)).split()

    assert rejoined == text.split()


def test_default_chunk_size_is_respected():
    chunks = chunk_text("word " * 2000)
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)


# --------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------


def test_synthesize_writes_audio(tmp_path, monkeypatch):
    _post_stub(monkeypatch)
    out = synthesize("Hello there.", tmp_path / "a.mp3", verbose=False)

    assert out.read_bytes() == b"ID3audio"


def test_synthesize_creates_parent_directories(tmp_path, monkeypatch):
    _post_stub(monkeypatch)
    out = synthesize("Hi.", tmp_path / "deep" / "nested" / "a.mp3", verbose=False)

    assert out.exists()


def test_synthesize_passes_voice_and_speed(tmp_path, monkeypatch):
    calls = _post_stub(monkeypatch)
    synthesize("Hi.", tmp_path / "a.mp3", voice="am_adam", speed=1.25, verbose=False)

    assert calls[0]["voice"] == "am_adam"
    assert calls[0]["speed"] == 1.25
    assert calls[0]["response_format"] == "mp3"


def test_multichunk_audio_is_concatenated(tmp_path, monkeypatch):
    calls = _post_stub(monkeypatch)
    synthesize("word " * 2000, tmp_path / "a.mp3", verbose=False)

    assert len(calls) > 1
    assert (tmp_path / "a.mp3").read_bytes() == b"ID3audio" * len(calls)


def test_empty_script_is_rejected(tmp_path, monkeypatch):
    _post_stub(monkeypatch)
    with pytest.raises(SpeechError, match="empty"):
        synthesize("   ", tmp_path / "a.mp3", verbose=False)


def test_multichunk_wav_is_refused(tmp_path, monkeypatch):
    """Naive WAV concatenation would embed a header mid-stream."""
    _post_stub(monkeypatch)

    with pytest.raises(SpeechError, match="WAV"):
        synthesize("word " * 2000, tmp_path / "a.wav", audio_format="wav", verbose=False)


def test_backend_error_status_is_reported(tmp_path, monkeypatch):
    _post_stub(monkeypatch, status=500)

    with pytest.raises(SpeechError, match="500"):
        synthesize("Hi.", tmp_path / "a.mp3", verbose=False)


def test_empty_audio_body_is_an_error(tmp_path, monkeypatch):
    _post_stub(monkeypatch, content=b"")

    with pytest.raises(SpeechError, match="empty audio"):
        synthesize("Hi.", tmp_path / "a.mp3", verbose=False)


def test_unreachable_backend_explains_how_to_start_it(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", refuse)

    with pytest.raises(SpeechError) as exc:
        synthesize("Hi.", tmp_path / "a.mp3", verbose=False)

    assert "docker run" in str(exc.value)


# --------------------------------------------------------------------------
# Voice listing
# --------------------------------------------------------------------------


def _get_stub(monkeypatch, payload, status=200):
    def fake_get(url, timeout=None):
        return SimpleNamespace(
            status_code=status,
            json=lambda: payload,
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(requests, "get", fake_get)


def test_list_voices_handles_dict_entries(monkeypatch):
    """The live backend returns [{'id': ..., 'name': ...}], not bare strings."""
    _get_stub(monkeypatch, {"voices": [{"id": "af_heart", "name": "af_heart"}]})
    assert list_voices() == ["af_heart"]


def test_list_voices_handles_bare_strings(monkeypatch):
    _get_stub(monkeypatch, {"voices": ["af_heart", "am_adam"]})
    assert list_voices() == ["af_heart", "am_adam"]


def test_list_voices_handles_a_bare_list(monkeypatch):
    _get_stub(monkeypatch, ["af_heart"])
    assert list_voices() == ["af_heart"]


def test_list_voices_degrades_when_unreachable(monkeypatch):
    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", refuse)
    assert list_voices() == []


# --------------------------------------------------------------------------
# Duration and tagging are best-effort and must never raise
# --------------------------------------------------------------------------


def test_audio_duration_returns_none_on_a_bad_file(tmp_path):
    bad = tmp_path / "not-audio.mp3"
    bad.write_bytes(b"definitely not an mp3")

    assert speak.audio_duration(bad) is None


def test_tagging_a_bad_file_does_not_raise(tmp_path):
    bad = tmp_path / "not-audio.mp3"
    bad.write_bytes(b"nope")

    speak.tag_audio(bad, title="T")  # must not raise
