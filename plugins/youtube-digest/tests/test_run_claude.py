"""Tests for how we interpret the claude CLI's response envelope.

No subprocess actually runs — `subprocess.run` is monkeypatched, so these are
fast and deterministic.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from ytdigest import analyze
from ytdigest.analyze import AnalysisError, RefusalError, run_claude

# Captured before the autouse fixture below rebinds the module attribute, so the
# one test that needs the genuine lookup can still reach it.
_REAL_FIND_CLAUDE = analyze.find_claude


@pytest.fixture(autouse=True)
def fake_claude_binary(monkeypatch):
    monkeypatch.setattr(analyze, "find_claude", lambda: "/fake/claude")


def _stub(monkeypatch, *, stdout="", stderr="", returncode=0):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_extracts_the_result_field_from_the_envelope(monkeypatch):
    _stub(monkeypatch, stdout=json.dumps({"is_error": False, "result": "the analysis"}))
    assert run_claude("prompt") == "the analysis"


def test_falls_back_to_raw_stdout_when_there_is_no_envelope(monkeypatch):
    _stub(monkeypatch, stdout="plain text reply")
    assert run_claude("prompt") == "plain text reply"


def test_refusal_raises_its_own_type(monkeypatch):
    """A refusal must be distinguishable so callers can retry it."""
    _stub(
        monkeypatch,
        stdout=json.dumps({"is_error": True, "stop_reason": "refusal", "result": ""}),
        returncode=1,
    )

    with pytest.raises(RefusalError) as exc:
        run_claude("prompt")

    assert "declined" in str(exc.value)
    assert "retry" in str(exc.value)


def test_non_refusal_envelope_error_is_reported_readably(monkeypatch):
    """Errors must not surface as a wall of raw JSON."""
    _stub(
        monkeypatch,
        stdout=json.dumps({"is_error": True, "result": "usage limit reached"}),
        returncode=1,
    )

    with pytest.raises(AnalysisError) as exc:
        run_claude("prompt")

    message = str(exc.value)
    assert "usage limit reached" in message
    assert "is_error" not in message  # the envelope itself isn't leaked


def test_nonzero_exit_without_an_envelope_uses_stderr(monkeypatch):
    _stub(monkeypatch, stderr="command not found", returncode=127)

    with pytest.raises(AnalysisError) as exc:
        run_claude("prompt")

    assert "127" in str(exc.value)
    assert "command not found" in str(exc.value)


def test_timeout_is_reported_clearly(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=600)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AnalysisError) as exc:
        run_claude("prompt", timeout=600)

    assert "timed out" in str(exc.value)


def test_missing_binary_gives_install_guidance(monkeypatch):
    monkeypatch.setattr(analyze.shutil, "which", lambda _: None)

    with pytest.raises(AnalysisError) as exc:
        _REAL_FIND_CLAUDE()

    message = str(exc.value)
    assert "not found on PATH" in message
    assert "claude.com/claude-code" in message


def test_refusal_is_retried_once_and_can_succeed(monkeypatch):
    """The whole reason RefusalError exists: a second attempt often works."""
    calls = {"n": 0}

    def flaky(prompt, model=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RefusalError("declined, retry")
        return json.dumps({"tldr": "worked on the retry"})

    monkeypatch.setattr(analyze, "run_claude", flaky)

    result = analyze._parse_with_retry("prompt", "model", 60)

    assert result == {"tldr": "worked on the retry"}
    assert calls["n"] == 2


def test_a_second_refusal_propagates(monkeypatch):
    def always_refuses(prompt, model=None, timeout=None):
        raise RefusalError("declined again")

    monkeypatch.setattr(analyze, "run_claude", always_refuses)

    with pytest.raises(RefusalError):
        analyze._parse_with_retry("prompt", "model", 60)
