"""Episode manifest and podcast RSS generation.

Three things break real podcast feeds, and all three are handled deliberately
here rather than discovered later on a phone:

1. **GUIDs must be stable across rebuilds.** We regenerate the whole feed on
   every run, so a GUID derived from a timestamp or a list index would make the
   same episode reappear as a new one each time and destroy saved playback
   position. Ours is derived from the YouTube video ID, which is stable forever.
2. **`enclosure length` must be the real byte size.** A wrong value is the most
   common cause of truncated or refused downloads. We stat the file.
3. **Unescaped `&` or raw HTML kills the parse.** Titles and descriptions go in
   CDATA, and any nested `]]>` is split.

The manifest (`episodes.json`) is the source of truth, not the directory
listing — it has to survive a rebuilt container, and it's what keeps GUIDs and
publication dates from drifting.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from html import escape as _html_escape

MANIFEST_NAME = "episodes.json"
FEED_NAME = "feed.xml"


def escape(value: str) -> str:
    """Escape &, < and > for XML text and attribute values (output only)."""
    return _html_escape(value, quote=False)

DEFAULT_TITLE = "ytdigest"
DEFAULT_DESCRIPTION = "Auto-generated audio summaries of YouTube videos."
DEFAULT_AUTHOR = "ytdigest"
DEFAULT_CATEGORY = "Technology"
DEFAULT_LANGUAGE = "en-us"


def _cdata(text: str) -> str:
    """Wrap in CDATA, splitting any embedded terminator so it can't break out."""
    safe = str(text or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def episode_guid(video_id: str) -> str:
    """Stable across every rebuild, because it's derived from the video itself."""
    return f"ytdigest-{video_id}"


def load_manifest(root: Path) -> list[dict]:
    """Existing episodes, oldest first. A missing or corrupt manifest is empty."""
    path = root / MANIFEST_NAME
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    return [e for e in data] if isinstance(data, list) else []


def save_manifest(root: Path, episodes: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(json.dumps(episodes, indent=2), encoding="utf-8")


def upsert_episode(episodes: list[dict], episode: dict) -> list[dict]:
    """Add an episode, or replace it in place if the video was done before.

    Replacing in place preserves the original `published` date, so re-running a
    video doesn't shuffle it to the top of the feed as though it were new.
    """
    guid = episode["guid"]

    for index, existing in enumerate(episodes):
        if existing.get("guid") == guid:
            merged = {**existing, **episode, "published": existing.get("published")}
            if not merged["published"]:
                merged["published"] = episode["published"]
            updated = list(episodes)
            updated[index] = merged
            return updated

    return [*episodes, episode]


def make_episode(
    video_id: str,
    title: str,
    description: str,
    audio_path: Path,
    duration_seconds: int,
    channel: str = "",
) -> dict:
    """Build a manifest entry, reading the real byte size off disk."""
    return {
        "guid": episode_guid(video_id),
        "video_id": video_id,
        "title": title,
        "description": description,
        "channel": channel,
        "filename": audio_path.name,
        "bytes": audio_path.stat().st_size,
        "duration": int(duration_seconds),
        "published": format_datetime(datetime.now(timezone.utc)),
    }


def format_duration(seconds: int) -> str:
    """`HH:MM:SS`, which is the form itunes:duration is safest with."""
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_feed(
    episodes: list[dict],
    base_url: str,
    title: str = DEFAULT_TITLE,
    description: str = DEFAULT_DESCRIPTION,
    author: str = DEFAULT_AUTHOR,
    image_url: str = "",
    language: str = DEFAULT_LANGUAGE,
    category: str = DEFAULT_CATEGORY,
) -> str:
    """Render the full RSS document.

    `base_url` is where the audio files will be served from. Locally that's a
    `file://` or `http://localhost` root; once published it becomes the R2
    custom-domain URL. Nothing else about the feed changes between the two.
    """
    base = base_url.rstrip("/")
    now = format_datetime(datetime.now(timezone.utc))

    items: list[str] = []
    # Newest first — the order podcast clients expect.
    for episode in sorted(episodes, key=lambda e: e.get("published", ""), reverse=True):
        audio_url = f"{base}/{episode['filename']}"
        items.append(
            f"""    <item>
      <title>{_cdata(episode.get('title', 'Untitled'))}</title>
      <description>{_cdata(episode.get('description', ''))}</description>
      <itunes:summary>{_cdata(episode.get('description', ''))}</itunes:summary>
      <enclosure url="{escape(audio_url)}" length="{episode.get('bytes', 0)}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(episode.get('guid', ''))}</guid>
      <pubDate>{escape(episode.get('published', now))}</pubDate>
      <itunes:duration>{format_duration(episode.get('duration', 0))}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
        )

    image_tag = f'\n    <itunes:image href="{escape(image_url)}"/>' if image_url else ""
    items_xml = "\n".join(items)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{_cdata(title)}</title>
    <description>{_cdata(description)}</description>
    <link>{escape(base)}/</link>
    <language>{escape(language)}</language>
    <lastBuildDate>{escape(now)}</lastBuildDate>
    <itunes:author>{_cdata(author)}</itunes:author>
    <itunes:summary>{_cdata(description)}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="{escape(category)}"/>
    <itunes:block>yes</itunes:block>{image_tag}
{items_xml}
  </channel>
</rss>
"""


def write_feed(root: Path, episodes: list[dict], base_url: str, **kwargs) -> Path:
    """Regenerate feed.xml from the manifest."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / FEED_NAME
    path.write_text(build_feed(episodes, base_url, **kwargs), encoding="utf-8")
    return path
