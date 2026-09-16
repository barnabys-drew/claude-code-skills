"""Tests for anchor-based timestamp correction — the thing that makes deep links land."""

import pytest

from ytdigest.transcript import Segment
from ytdigest.verify import build_index, locate, normalize, verify_analysis

# Stand-in for auto-caption output: no punctuation, inconsistent casing.
SEGMENTS = [
    Segment(0, 10, "welcome everyone to this talk about kernel exploitation"),
    Segment(10, 20, "the first thing we need to understand is the memory layout"),
    Segment(20, 30, "there is no bounds check on the digipeater field at all"),
    Segment(30, 40, "so we use return oriented programming to mark the stack executable"),
    Segment(40, 50, "and finally we install a backdoor in the icmp handler"),
]


def test_normalize_strips_punctuation_and_case():
    assert normalize("Hello, World! It's -- fine.") == "hello world it s fine"
    assert normalize("  MULTIPLE   spaces  ") == "multiple spaces"


def test_index_maps_offsets_back_to_segment_start_times():
    index = build_index(SEGMENTS)

    assert index.time_at(0) == 0.0
    assert index.time_at(index.offsets[2]) == 20.0
    assert index.time_at(index.offsets[4]) == 40.0


def test_index_skips_empty_segments():
    index = build_index([Segment(0, 1, "real text"), Segment(1, 2, "   "), Segment(2, 3, "more")])
    assert len(index.offsets) == 2
    assert index.starts == [0.0, 2.0]


def test_locate_exact_anchor():
    index = build_index(SEGMENTS)
    assert locate(index, "there is no bounds check on the digipeater field") == 20.0


def test_locate_is_insensitive_to_case_and_punctuation():
    """The model tidies up quotes; that must not break the lookup."""
    index = build_index(SEGMENTS)
    assert locate(index, "There is no bounds check on the digipeater field!") == 20.0


def test_locate_tolerates_an_anchor_that_runs_long():
    """Model extends the anchor past what was said -> prefix match still works."""
    index = build_index(SEGMENTS)
    found = locate(index, "so we use return oriented programming to mark the stack and then some extra")
    assert found == 30.0


def test_locate_tolerates_an_anchor_that_starts_early():
    """Anchor begins a few words before a real match -> window search catches it."""
    index = build_index(SEGMENTS)
    found = locate(index, "blah blah nonsense install a backdoor in the icmp handler")
    assert found == 40.0


def test_locate_returns_none_for_absent_text():
    index = build_index(SEGMENTS)
    assert locate(index, "this phrase never appears anywhere in the transcript") is None


def test_locate_rejects_anchors_that_are_too_short():
    """Sub-4-word anchors match by coincidence, so we refuse them outright."""
    index = build_index(SEGMENTS)
    assert locate(index, "the first") is None


def test_locate_on_empty_inputs():
    assert locate(build_index([]), "anything at all here") is None
    assert locate(build_index(SEGMENTS), "") is None


# --------------------------------------------------------------------------
# End-to-end correction
# --------------------------------------------------------------------------


def test_verify_overrides_a_wrong_model_timestamp():
    """The whole point: a badly-guessed timestamp gets corrected to the real one."""
    analysis = {
        "highlights": [
            {
                "timestamp": "5:00",  # model guessed 300s; truth is 20s
                "anchor": "there is no bounds check on the digipeater field",
                "title": "The bug",
            }
        ]
    }

    corrected, stats = verify_analysis(analysis, SEGMENTS)

    assert corrected["highlights"][0]["timestamp_s"] == 20.0
    assert corrected["highlights"][0]["verified"] is True
    assert stats == {"verified": 1, "unverified": 0}


def test_verify_falls_back_to_detail_when_anchor_misses():
    analysis = {
        "highlights": [
            {
                "timestamp": "9:99",
                "anchor": "a completely fabricated anchor phrase not present",
                "detail": "and finally we install a backdoor in the icmp handler",
            }
        ]
    }

    corrected, stats = verify_analysis(analysis, SEGMENTS)

    assert corrected["highlights"][0]["timestamp_s"] == 40.0
    assert stats["verified"] == 1


def test_verify_marks_unlocatable_items_rather_than_dropping_them():
    analysis = {
        "highlights": [
            {"timestamp": "1:00", "anchor": "nothing here resembles the transcript at all"}
        ]
    }

    corrected, stats = verify_analysis(analysis, SEGMENTS)

    assert corrected["highlights"][0]["verified"] is False
    assert "timestamp_s" not in corrected["highlights"][0]
    assert stats["unverified"] == 1


def test_verify_corrects_quote_timestamps_too():
    analysis = {
        "notable_quotes": [
            {"timestamp": "0:00", "quote": "we use return oriented programming to mark the stack"}
        ]
    }

    corrected, _ = verify_analysis(analysis, SEGMENTS)
    assert corrected["notable_quotes"][0]["timestamp_s"] == 30.0


def test_verify_preserves_all_other_fields():
    analysis = {
        "tldr": "a summary",
        "key_claims": ["one", "two"],
        "skip_it_if": "you know this",
        "highlights": [{"anchor": "welcome everyone to this talk about kernel", "title": "Intro"}],
    }

    corrected, _ = verify_analysis(analysis, SEGMENTS)

    assert corrected["tldr"] == "a summary"
    assert corrected["key_claims"] == ["one", "two"]
    assert corrected["skip_it_if"] == "you know this"
    assert corrected["highlights"][0]["title"] == "Intro"


@pytest.mark.parametrize(
    "analysis",
    [{}, {"highlights": []}, {"highlights": None}, {"notable_quotes": None}],
)
def test_verify_handles_degenerate_analyses(analysis):
    corrected, stats = verify_analysis(analysis, SEGMENTS)
    assert isinstance(corrected, dict)
    assert stats["verified"] == 0


def test_verify_ignores_malformed_highlight_entries():
    analysis = {"highlights": ["not a dict", 42, None]}
    corrected, _ = verify_analysis(analysis, SEGMENTS)
    assert corrected["highlights"] == ["not a dict", 42, None]
