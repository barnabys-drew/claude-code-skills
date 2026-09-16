"""Transcript parsing, cleanup, chunking, and cache tests. No network."""

import json

import pytest

from ytdigest.transcript import (
    Segment,
    as_plain_text,
    as_timestamped_text,
    chunk_segments,
    dedupe_segments,
    format_timestamp,
    load_cache,
    parse_json3,
    parse_vtt,
    save_cache,
    word_count,
)


# --------------------------------------------------------------------------
# json3
# --------------------------------------------------------------------------


def test_parse_json3_basic():
    raw = json.dumps(
        {
            "events": [
                {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
                {"tStartMs": 2000, "dDurationMs": 1500, "segs": [{"utf8": "second cue"}]},
            ]
        }
    )
    segments = parse_json3(raw)

    assert len(segments) == 2
    assert segments[0] == Segment(start=0.0, end=2.0, text="hello world")
    assert segments[1].start == 2.0
    assert segments[1].end == 3.5


def test_parse_json3_skips_events_without_segs():
    raw = json.dumps(
        {
            "events": [
                {"tStartMs": 0, "dDurationMs": 1000},  # formatting noise, no segs
                {"tStartMs": 1000, "dDurationMs": 1000, "segs": [{"utf8": "real text"}]},
                {"tStartMs": 2000, "dDurationMs": 1000, "segs": [{"utf8": "  \n "}]},  # blank
            ]
        }
    )
    segments = parse_json3(raw)

    assert len(segments) == 1
    assert segments[0].text == "real text"


def test_parse_json3_unescapes_entities_and_drops_sound_cues():
    raw = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 0,
                    "dDurationMs": 1000,
                    "segs": [{"utf8": "[Music] Tom &amp; Jerry &#39;s   rules"}],
                }
            ]
        }
    )
    assert parse_json3(raw)[0].text == "Tom & Jerry 's rules"


def test_parse_json3_handles_empty_document():
    assert parse_json3(json.dumps({"events": []})) == []
    assert parse_json3(json.dumps({})) == []


# --------------------------------------------------------------------------
# WebVTT
# --------------------------------------------------------------------------


def test_parse_vtt_basic():
    raw = """WEBVTT

00:00:01.000 --> 00:00:04.500
first line of speech

00:01:02.250 --> 00:01:05.000
second line
continued on the next row
"""
    segments = parse_vtt(raw)

    assert len(segments) == 2
    assert segments[0] == Segment(start=1.0, end=4.5, text="first line of speech")
    assert segments[1].start == 62.25
    assert segments[1].text == "second line continued on the next row"


def test_parse_vtt_strips_inline_timing_tags():
    raw = """WEBVTT

00:00:00.000 --> 00:00:03.000 align:start position:0%
so<00:00:00.500> the<00:00:01.000> traffic<00:00:01.500> spiked
"""
    assert parse_vtt(raw)[0].text == "so the traffic spiked"


def test_parse_vtt_ignores_headers_and_blank_input():
    assert parse_vtt("WEBVTT\n\nKind: captions\nLanguage: en\n") == []
    assert parse_vtt("") == []


# --------------------------------------------------------------------------
# Auto-caption de-duplication
# --------------------------------------------------------------------------


def test_dedupe_drops_exact_repeats():
    segments = [
        Segment(0, 1, "the same line"),
        Segment(1, 2, "the same line"),
        Segment(2, 3, "a new line"),
    ]
    assert [s.text for s in dedupe_segments(segments)] == ["the same line", "a new line"]


def test_dedupe_collapses_rolling_prefix_growth():
    """The classic auto-caption pattern: each cue is the last one plus more words."""
    segments = [
        Segment(0, 1, "so the first thing we"),
        Segment(1, 2, "so the first thing we noticed was"),
    ]
    result = dedupe_segments(segments)

    assert len(result) == 1
    assert result[0].text == "so the first thing we noticed was"
    assert result[0].start == 0  # keeps the earliest start
    assert result[0].end == 2  # extends to the latest end


def test_dedupe_strips_overlapping_window_tails():
    """Consecutive cues that share words at the seam shouldn't double up."""
    segments = [
        Segment(0, 2, "the traffic spiked at midnight"),
        Segment(2, 4, "at midnight and stayed high"),
    ]
    result = dedupe_segments(segments)

    assert as_plain_text(result) == "the traffic spiked at midnight and stayed high"


def test_dedupe_preserves_genuinely_distinct_text():
    segments = [
        Segment(0, 1, "alpha bravo"),
        Segment(1, 2, "charlie delta"),
        Segment(2, 3, "echo foxtrot"),
    ]
    assert len(dedupe_segments(segments)) == 3


def test_dedupe_handles_empty_input():
    assert dedupe_segments([]) == []


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def _segments(count, words_each=10):
    return [
        Segment(i * 5.0, i * 5.0 + 5.0, " ".join(["word"] * words_each)) for i in range(count)
    ]


def test_short_transcript_is_a_single_chunk():
    chunks = chunk_segments(_segments(10), max_words=1000)
    assert len(chunks) == 1
    assert len(chunks[0]) == 10


def test_long_transcript_splits_on_the_word_budget():
    # 100 segments x 10 words = 1000 words, budget 250 -> 4 chunks
    chunks = chunk_segments(_segments(100), max_words=250)

    assert len(chunks) == 4
    assert all(word_count(chunk) <= 250 for chunk in chunks)


def test_chunking_never_splits_a_segment_or_loses_one():
    original = _segments(50)
    chunks = chunk_segments(original, max_words=100)

    flattened = [seg for chunk in chunks for seg in chunk]
    assert flattened == original  # every segment survives, in order


def test_oversized_single_segment_still_produces_a_chunk():
    segments = [Segment(0, 5, " ".join(["word"] * 500))]
    chunks = chunk_segments(segments, max_words=100)

    assert len(chunks) == 1
    assert chunks[0] == segments


def test_chunk_empty_input():
    assert chunk_segments([], max_words=100) == []


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0:00"),
        (5, "0:05"),
        (65, "1:05"),
        (599, "9:59"),
        (600, "10:00"),
        (3600, "1:00:00"),
        (3661, "1:01:01"),
        (7325.9, "2:02:05"),  # truncates, doesn't round
    ],
)
def test_format_timestamp(seconds, expected):
    assert format_timestamp(seconds) == expected


def test_timestamped_text_inserts_markers_at_the_interval():
    segments = [Segment(i * 10.0, i * 10.0 + 10.0, f"chunk{i}") for i in range(6)]
    text = as_timestamped_text(segments, interval=30.0)

    # Markers at 0s and 30s; not one per segment.
    assert "[0:00]" in text
    assert "[0:30]" in text
    assert text.count("[") == 2
    for i in range(6):
        assert f"chunk{i}" in text


def test_timestamped_text_empty():
    assert as_timestamped_text([]) == ""


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------


def test_cache_round_trip(tmp_path):
    meta = {"id": "abc", "title": "Test Video", "duration": 120}
    segments = _segments(3)

    save_cache(tmp_path, "abc", meta, segments)
    loaded = load_cache(tmp_path, "abc")

    assert loaded is not None
    loaded_meta, loaded_segments = loaded
    assert loaded_meta == meta
    assert loaded_segments == segments


def test_cache_miss_returns_none(tmp_path):
    assert load_cache(tmp_path, "nonexistent") is None


def test_corrupt_cache_is_treated_as_a_miss(tmp_path):
    (tmp_path / "abc.json").write_text("{not valid json", encoding="utf-8")
    assert load_cache(tmp_path, "abc") is None


def test_cache_with_wrong_shape_is_treated_as_a_miss(tmp_path):
    (tmp_path / "abc.json").write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")
    assert load_cache(tmp_path, "abc") is None
