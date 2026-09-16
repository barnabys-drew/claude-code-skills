"""The running facts document."""

import json

from ytdigest.notes import (
    load_notes,
    make_note,
    render_notes,
    save_notes,
    update_notes,
    upsert_note,
)

META = {"title": "A Talk", "uploader": "A Channel"}

ANALYSIS = {
    "tldr": "A summary.",
    "for_you": "Matters because X.",
    "key_claims": ["Claim one.", "Claim two."],
    "highlights": [
        {
            "title": "The bug",
            "detail": "No bounds check.",
            "relevance": "Relevant because Y.",
            "timestamp_s": 812.0,
            "verified": True,
        }
    ],
    # verify.py always writes `verified` alongside `timestamp_s`; the renderer
    # only trusts the located time when that flag is set.
    "notable_quotes": [{"quote": "A line.", "timestamp_s": 250.0, "verified": True}],
}


def test_note_captures_the_durable_parts():
    note = make_note("abc12345678", META, ANALYSIS)

    assert note["title"] == "A Talk"
    assert [c["claim"] for c in note["claims"]] == ["Claim one.", "Claim two."]
    assert note["highlights"][0]["title"] == "The bug"
    assert note["quotes"][0]["quote"] == "A line."


def test_note_keeps_the_timestamped_links_narration_discards():
    """The whole point of the doc: it retains what the audio throws away."""
    note = make_note("abc12345678", META, ANALYSIS)

    assert note["highlights"][0]["time"] == "13:32"
    assert note["highlights"][0]["url"] == "https://youtu.be/abc12345678?t=812"
    assert note["quotes"][0]["url"] == "https://youtu.be/abc12345678?t=250"


def test_note_handles_an_empty_analysis():
    note = make_note("abc12345678", META, {})

    assert note["claims"] == []
    assert note["highlights"] == []
    assert note["quotes"] == []


def test_note_skips_malformed_entries():
    analysis = {"highlights": ["str", None], "notable_quotes": [{"no_quote": 1}]}
    note = make_note("abc12345678", META, analysis)

    assert note["highlights"] == []
    assert note["quotes"] == []


# --------------------------------------------------------------------------
# Upsert — re-running a video must update, not duplicate
# --------------------------------------------------------------------------


def test_rerunning_a_video_replaces_its_entry():
    first = make_note("abc12345678", META, ANALYSIS)
    second = make_note("abc12345678", {**META, "title": "A Talk (revised)"}, ANALYSIS)

    entries = upsert_note([first], second)

    assert len(entries) == 1
    assert entries[0]["title"] == "A Talk (revised)"


def test_rerunning_preserves_the_original_added_date():
    first = {**make_note("abc12345678", META, ANALYSIS), "added": "2026-01-01"}
    second = make_note("abc12345678", META, ANALYSIS)

    assert upsert_note([first], second)[0]["added"] == "2026-01-01"


def test_a_different_video_is_appended():
    a = make_note("aaaaaaaaaaa", META, ANALYSIS)
    b = make_note("bbbbbbbbbbb", META, ANALYSIS)

    assert len(upsert_note([a], b)) == 2


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def test_render_includes_content_and_an_index():
    doc = render_notes([make_note("abc12345678", META, ANALYSIS)])

    assert "# Video facts" in doc
    assert "## Index" in doc
    assert "A Talk" in doc
    assert "Claim one." in doc
    assert "https://youtu.be/abc12345678?t=812" in doc
    assert "A line." in doc


def test_render_marks_unverified_timestamps():
    analysis = {"highlights": [{"title": "T", "timestamp": "5:00", "verified": False}]}
    doc = render_notes([make_note("abc12345678", META, analysis)])

    assert "~" in doc


def test_render_orders_newest_first():
    old = {**make_note("aaaaaaaaaaa", {"title": "Older"}, ANALYSIS), "added": "2026-01-01"}
    new = {**make_note("bbbbbbbbbbb", {"title": "Newer"}, ANALYSIS), "added": "2026-07-26"}

    doc = render_notes([old, new])
    assert doc.index("Newer") < doc.index("Older")


def test_render_empty_document():
    assert "Nothing here yet" in render_notes([])


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_manifest_round_trip(tmp_path):
    entries = [make_note("abc12345678", META, ANALYSIS)]
    save_notes(tmp_path, entries)

    assert load_notes(tmp_path) == entries


def test_corrupt_manifest_is_treated_as_empty(tmp_path):
    (tmp_path / "facts.json").write_text("{broken", encoding="utf-8")
    assert load_notes(tmp_path) == []


def test_manifest_with_wrong_shape_is_empty(tmp_path):
    (tmp_path / "facts.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert load_notes(tmp_path) == []


def test_update_notes_writes_both_doc_and_manifest(tmp_path):
    doc = tmp_path / "facts.md"
    path, total = update_notes(doc, "abc12345678", META, ANALYSIS)

    assert path == doc
    assert total == 1
    assert doc.exists()
    assert (tmp_path / "facts.json").exists()


def test_update_notes_accumulates_across_videos(tmp_path):
    doc = tmp_path / "facts.md"
    update_notes(doc, "aaaaaaaaaaa", {"title": "First"}, ANALYSIS)
    _, total = update_notes(doc, "bbbbbbbbbbb", {"title": "Second"}, ANALYSIS)

    assert total == 2
    assert "First" in doc.read_text() and "Second" in doc.read_text()


def test_update_notes_is_idempotent_for_one_video(tmp_path):
    doc = tmp_path / "facts.md"
    update_notes(doc, "abc12345678", META, ANALYSIS)
    _, total = update_notes(doc, "abc12345678", META, ANALYSIS)

    assert total == 1


def test_custom_doc_name_gets_a_matching_manifest(tmp_path):
    doc = tmp_path / "research.md"
    update_notes(doc, "abc12345678", META, ANALYSIS)

    assert (tmp_path / "research.json").exists()
