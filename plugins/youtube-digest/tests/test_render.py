"""Rendering tests: timestamp round-tripping and report structure."""

import pytest

from ytdigest.render import parse_timestamp, render_markdown, render_terminal
from ytdigest.transcript import format_timestamp

VIDEO_ID = "dQw4w9WgXcQ"

META = {"title": "A Talk", "uploader": "A Channel", "duration": 1800}

ANALYSIS = {
    "tldr": "A short summary of the video.",
    "highlights": [
        {
            "timestamp": "0:06:52",
            "timestamp_s": 412.0,
            "verified": True,
            "title": "The interesting part",
            "why_interesting": "Because it is.",
            "detail": "Here is the substance of it.",
        },
        {
            "timestamp": "20:00",
            "timestamp_s": 1200.0,
            "verified": True,
            "title": "Another moment",
            "why_interesting": "Also good.",
            "detail": "More substance.",
        },
    ],
    "key_claims": ["Claim one.", "Claim two."],
    "notable_quotes": [
        {"timestamp": "1:23", "timestamp_s": 83.0, "verified": True, "quote": "A memorable line."}
    ],
    "skip_it_if": "You already know this material.",
}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1:23", 83),
        ("0:06:52", 412),
        ("1:01:01", 3661),
        ("[2:30]", 150),  # tolerates bracket wrapping
        (" 5:00 ", 300),  # tolerates whitespace
        ("45", 45),  # bare seconds as a string
        (412, 412),  # already a number
        (412.7, 412.7),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize("value", ["not a time", "", None, [], "a:b:c"])
def test_parse_timestamp_degrades_to_zero_on_garbage(value):
    """A bad timestamp costs one wrong link, not the whole report."""
    assert parse_timestamp(value) == 0.0


@pytest.mark.parametrize("seconds", [0, 59, 60, 412, 3599, 3600, 3661, 7325])
def test_timestamp_round_trip(seconds):
    """format -> parse must return the original, or deep links drift."""
    assert parse_timestamp(format_timestamp(seconds)) == seconds


def test_terminal_report_contains_every_section():
    out = render_terminal(META, ANALYSIS, VIDEO_ID, "youtube captions (en)")

    assert "A Talk" in out
    assert "A Channel" in out
    assert "30:00" in out  # duration
    assert "A short summary of the video." in out
    assert "The interesting part" in out
    assert "Claim one." in out
    assert "A memorable line." in out
    assert "You already know this material." in out


def test_terminal_report_builds_correct_deep_links():
    out = render_terminal(META, ANALYSIS, VIDEO_ID, "captions")

    assert f"https://youtu.be/{VIDEO_ID}?t=412" in out  # 0:06:52
    assert f"https://youtu.be/{VIDEO_ID}?t=1200" in out  # 20:00


def test_markdown_report_builds_correct_links():
    out = render_markdown(META, ANALYSIS, VIDEO_ID, "captions")

    assert f"[6:52](https://youtu.be/{VIDEO_ID}?t=412)" in out
    assert out.startswith("# A Talk")
    assert "## Claims made" in out
    assert "> A memorable line." in out


def test_render_handles_missing_optional_sections():
    minimal = {"tldr": "Just a summary.", "highlights": []}

    terminal = render_terminal(META, minimal, VIDEO_ID, "captions")
    markdown = render_markdown(META, minimal, VIDEO_ID, "captions")

    assert "Just a summary." in terminal
    assert "KEY CLAIMS" not in terminal
    assert "## Key claims" not in markdown


def test_render_handles_completely_empty_analysis():
    """A thin video that yields nothing must still render without crashing."""
    assert render_terminal(META, {}, VIDEO_ID, "captions")
    assert render_markdown(META, {}, VIDEO_ID, "captions")


def test_verified_timestamp_wins_over_the_models_guess():
    """timestamp_s comes from locating the anchor, so it must override `timestamp`."""
    analysis = {
        "highlights": [
            {"timestamp": "5:00", "timestamp_s": 20.0, "verified": True, "title": "x"}
        ]
    }
    out = render_terminal(META, analysis, VIDEO_ID, "captions")

    assert f"?t=20" in out
    assert "?t=300" not in out
    assert "[0:20]" in out


def test_unverified_timestamps_are_marked_with_a_tilde():
    """An unconfirmed link must be visibly flagged, not presented as fact."""
    analysis = {"highlights": [{"timestamp": "5:00", "verified": False, "title": "x"}]}

    terminal = render_terminal(META, analysis, VIDEO_ID, "captions")
    markdown = render_markdown(META, analysis, VIDEO_ID, "captions")

    assert "~5:00" in terminal
    assert "~5:00" in markdown
    assert f"?t=300" in terminal  # still links to the model's best guess


def test_verified_timestamps_have_no_tilde():
    analysis = {"highlights": [{"timestamp": "5:00", "timestamp_s": 300.0, "verified": True}]}
    assert "~" not in render_terminal(META, analysis, VIDEO_ID, "captions")


def test_render_handles_missing_highlight_fields():
    analysis = {"highlights": [{"title": "Only a title"}]}

    out = render_terminal(META, analysis, VIDEO_ID, "captions")
    assert "Only a title" in out
    assert f"https://youtu.be/{VIDEO_ID}?t=0" in out
