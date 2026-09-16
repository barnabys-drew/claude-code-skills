"""Feed generation. These guard the three things that break real podcast feeds."""

import json
import xml.dom.minidom
from xml.etree import ElementTree

import pytest

from ytdigest.feed import (
    build_feed,
    episode_guid,
    format_duration,
    load_manifest,
    make_episode,
    save_manifest,
    upsert_episode,
    write_feed,
)

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def _episode(guid="ytdigest-abc", published="Mon, 27 Jul 2026 01:00:00 +0000", **kw):
    base = {
        "guid": guid,
        "video_id": "abc",
        "title": "A Talk",
        "description": "About things",
        "filename": "abc.mp3",
        "bytes": 4704903,
        "duration": 294,
        "published": published,
    }
    return {**base, **kw}


# --------------------------------------------------------------------------
# GUID stability — wrong here means duplicate episodes and lost playback position
# --------------------------------------------------------------------------


def test_guid_is_derived_from_the_video_id():
    assert episode_guid("dQw4w9WgXcQ") == "ytdigest-dQw4w9WgXcQ"


def test_guid_is_stable_across_calls():
    assert episode_guid("abc") == episode_guid("abc")


def test_rebuilding_the_same_episode_does_not_duplicate_it():
    episodes = [_episode()]
    rebuilt = upsert_episode(episodes, _episode(title="A Talk (re-run)"))

    assert len(rebuilt) == 1
    assert rebuilt[0]["title"] == "A Talk (re-run)"


def test_rerunning_preserves_the_original_publication_date():
    """Otherwise a re-run shuffles an old episode to the top as though it were new."""
    episodes = [_episode(published="Mon, 01 Jun 2026 00:00:00 +0000")]
    rebuilt = upsert_episode(episodes, _episode(published="Mon, 27 Jul 2026 00:00:00 +0000"))

    assert rebuilt[0]["published"] == "Mon, 01 Jun 2026 00:00:00 +0000"


def test_a_different_video_is_appended_not_merged():
    episodes = [_episode(guid="ytdigest-abc")]
    rebuilt = upsert_episode(episodes, _episode(guid="ytdigest-xyz"))

    assert len(rebuilt) == 2


# --------------------------------------------------------------------------
# Enclosure length — the most common cause of truncated downloads
# --------------------------------------------------------------------------


def test_make_episode_reads_the_real_byte_size(tmp_path):
    audio = tmp_path / "abc.mp3"
    audio.write_bytes(b"x" * 12345)

    episode = make_episode("abc", "T", "D", audio, 60)
    assert episode["bytes"] == 12345


def test_enclosure_length_matches_the_manifest_byte_count():
    xml = build_feed([_episode(bytes=999888)], "https://example.com")
    root = ElementTree.fromstring(xml)
    enclosure = root.find(".//item/enclosure")

    assert enclosure.get("length") == "999888"
    assert enclosure.get("type") == "audio/mpeg"
    assert enclosure.get("url") == "https://example.com/abc.mp3"


def test_base_url_trailing_slash_does_not_double_up():
    xml = build_feed([_episode()], "https://example.com/feed/")
    assert "https://example.com/feed/abc.mp3" in xml
    assert "//abc.mp3" not in xml.replace("https://", "")


# --------------------------------------------------------------------------
# Escaping — unescaped & or raw HTML is a hard XML parse failure
# --------------------------------------------------------------------------


def test_ampersands_and_html_survive_in_titles():
    xml = build_feed([_episode(title="Tom & Jerry <script>alert(1)</script>")], "https://e.com")

    # Must parse; the CDATA wrapper is what makes that true.
    ElementTree.fromstring(xml)
    assert "Tom & Jerry" in xml


def test_cdata_terminator_in_content_cannot_break_out():
    xml = build_feed([_episode(description="evil ]]> injection")], "https://e.com")

    ElementTree.fromstring(xml)  # would raise if the CDATA escaped
    assert "]]]]><![CDATA[>" in xml


def test_ampersand_in_the_enclosure_url_is_escaped():
    xml = build_feed([_episode()], "https://e.com/a?x=1&y=2")

    ElementTree.fromstring(xml)
    assert "&amp;" in xml


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_feed_is_wellformed_xml():
    feed = build_feed([_episode()], "https://e.com")

    assert feed.lstrip().startswith("<?xml")
    root = ElementTree.fromstring(feed)
    assert root.tag == "rss"
    assert root.get("version") == "2.0"


def test_required_channel_tags_are_present():
    root = ElementTree.fromstring(build_feed([_episode()], "https://e.com"))
    channel = root.find("channel")

    for tag in ("title", "description", "language"):
        assert channel.find(tag) is not None, tag
    assert channel.find(f"{ITUNES}explicit") is not None
    assert channel.find(f"{ITUNES}category") is not None


def test_required_item_tags_are_present():
    root = ElementTree.fromstring(build_feed([_episode()], "https://e.com"))
    item = root.find(".//item")

    assert item.find("title") is not None
    assert item.find("enclosure") is not None
    assert item.find("guid") is not None
    assert item.find("pubDate") is not None
    assert item.find(f"{ITUNES}duration") is not None


def test_feed_is_marked_unlisted():
    """itunes:block tells directories not to index a private feed."""
    root = ElementTree.fromstring(build_feed([_episode()], "https://e.com"))
    assert root.find(f"channel/{ITUNES}block").text == "yes"


def test_guid_is_not_a_permalink():
    root = ElementTree.fromstring(build_feed([_episode()], "https://e.com"))
    assert root.find(".//item/guid").get("isPermaLink") == "false"


def test_episodes_are_ordered_newest_first():
    older = _episode(guid="a", published="Mon, 01 Jun 2026 00:00:00 +0000", filename="a.mp3")
    newer = _episode(guid="b", published="Mon, 27 Jul 2026 00:00:00 +0000", filename="b.mp3")

    root = ElementTree.fromstring(build_feed([older, newer], "https://e.com"))
    filenames = [i.find("enclosure").get("url") for i in root.findall(".//item")]

    assert filenames[0].endswith("b.mp3")


def test_empty_feed_still_parses():
    ElementTree.fromstring(build_feed([], "https://e.com"))


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00:00"), (59, "00:00:59"), (294, "00:04:54"), (3661, "01:01:01"), (-5, "00:00:00")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


def test_manifest_round_trip(tmp_path):
    episodes = [_episode()]
    save_manifest(tmp_path, episodes)
    assert load_manifest(tmp_path) == episodes


def test_missing_manifest_is_empty(tmp_path):
    assert load_manifest(tmp_path) == []


def test_corrupt_manifest_is_empty(tmp_path):
    (tmp_path / "episodes.json").write_text("{broken", encoding="utf-8")
    assert load_manifest(tmp_path) == []


def test_manifest_with_wrong_shape_is_empty(tmp_path):
    (tmp_path / "episodes.json").write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert load_manifest(tmp_path) == []


def test_write_feed_creates_the_directory(tmp_path):
    target = tmp_path / "nested" / "podcast"
    path = write_feed(target, [_episode()], "https://e.com")

    assert path.exists()
    xml.dom.minidom.parse(str(path))
