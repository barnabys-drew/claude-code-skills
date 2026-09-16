"""URL parsing tests. No network — these are all pure functions."""

import pytest

from ytdigest.fetch import parse_video_id, timestamp_url

VALID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VALID}",
        f"http://youtube.com/watch?v={VALID}",
        f"https://m.youtube.com/watch?v={VALID}",
        f"https://music.youtube.com/watch?v={VALID}",
        f"www.youtube.com/watch?v={VALID}",  # no scheme
        f"https://youtu.be/{VALID}",
        f"https://youtu.be/{VALID}?si=abc123xyz",
        f"https://www.youtube.com/shorts/{VALID}",
        f"https://www.youtube.com/live/{VALID}",
        f"https://www.youtube.com/embed/{VALID}",
        f"https://www.youtube.com/v/{VALID}",
        VALID,  # bare ID
        f"  https://youtu.be/{VALID}  ",  # surrounding whitespace
    ],
)
def test_parses_every_url_shape(url):
    assert parse_video_id(url) == VALID


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VALID}&t=42s",
        f"https://www.youtube.com/watch?v={VALID}&list=PLabc&index=3",
        f"https://youtu.be/{VALID}?t=120",
    ],
)
def test_extra_query_params_are_ignored(url):
    assert parse_video_id(url) == VALID


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/12345",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=tooshort",
        "https://www.youtube.com/@somechannel",
        "not a url at all",
        "",
    ],
)
def test_rejects_non_video_urls(url):
    with pytest.raises(ValueError):
        parse_video_id(url)


def test_playlist_url_without_video_id_is_rejected():
    with pytest.raises(ValueError):
        parse_video_id("https://www.youtube.com/playlist?list=PLabcdefghij")


def test_timestamp_url_truncates_to_whole_seconds():
    assert timestamp_url(VALID, 412.9) == f"https://youtu.be/{VALID}?t=412"
    assert timestamp_url(VALID, 0) == f"https://youtu.be/{VALID}?t=0"
