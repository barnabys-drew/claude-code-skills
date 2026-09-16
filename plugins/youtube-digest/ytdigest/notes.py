"""A running reference document of facts across every video you process.

The episode is for listening once; this is for finding something again three
months later. Different jobs, so different shapes: the doc keeps the exact
claims, the verbatim quotes, and the timestamped links that the narration
deliberately throws away.

Same structure as the podcast feed — `facts.json` is the source of truth and
`facts.md` is rendered from it. That means re-running a video updates its entry
in place instead of appending a second copy, and the markdown can be regenerated
or restyled at any time without re-analyzing anything.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

MANIFEST_NAME = "facts.json"
DOC_NAME = "facts.md"


def load_notes(root: Path, manifest_name: str = MANIFEST_NAME) -> list[dict]:
    path = root / manifest_name
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    return data if isinstance(data, list) else []


def save_notes(root: Path, entries: list[dict], manifest_name: str = MANIFEST_NAME) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / manifest_name).write_text(json.dumps(entries, indent=2), encoding="utf-8")


def make_note(video_id: str, meta: dict, analysis: dict) -> dict:
    """Extract the durable, referenceable parts of an analysis.

    Deliberately keeps `claims`, `highlights`, and `quotes` separate rather than
    flattening them. Claims are what the video asserts; highlights are where to
    go in the video; quotes are what was actually said. Looking something up
    later, you usually know which of those three you want.
    """
    from .fetch import timestamp_url
    from .render import item_seconds
    from .transcript import format_timestamp

    highlights = []
    for item in analysis.get("highlights") or []:
        if not isinstance(item, dict):
            continue
        seconds, verified = item_seconds(item)
        highlights.append(
            {
                "title": item.get("title", ""),
                "detail": item.get("detail", ""),
                "relevance": item.get("relevance") or "",
                "time": format_timestamp(seconds),
                "url": timestamp_url(video_id, seconds),
                "verified": verified,
            }
        )

    quotes = []
    for item in analysis.get("notable_quotes") or []:
        if not isinstance(item, dict) or not item.get("quote"):
            continue
        seconds, _ = item_seconds(item)
        quotes.append(
            {
                "quote": item["quote"],
                "time": format_timestamp(seconds),
                "url": timestamp_url(video_id, seconds),
            }
        )

    from .render import claim_parts

    claims = []
    for raw in analysis.get("key_claims") or []:
        text, basis, support = claim_parts(raw)
        if text:
            claims.append({"claim": text, "basis": basis, "support": support})

    return {
        "video_id": video_id,
        "title": meta.get("title", "Untitled"),
        "channel": meta.get("uploader", ""),
        "url": f"https://youtu.be/{video_id}",
        "added": date.today().isoformat(),
        "tldr": analysis.get("tldr", ""),
        "for_you": analysis.get("for_you") or "",
        "claims": claims,
        "highlights": highlights,
        "quotes": quotes,
    }


def upsert_note(entries: list[dict], note: dict) -> list[dict]:
    """Replace an existing entry for this video, keeping its original add date."""
    for index, existing in enumerate(entries):
        if existing.get("video_id") == note["video_id"]:
            updated = list(entries)
            updated[index] = {**note, "added": existing.get("added", note["added"])}
            return updated

    return [*entries, note]


def render_notes(entries: list[dict], title: str = "Video facts") -> str:
    """Render the whole document, newest first, with a linked index on top."""
    if not entries:
        return f"# {title}\n\n_Nothing here yet._\n"

    ordered = sorted(entries, key=lambda e: e.get("added", ""), reverse=True)

    out = [f"# {title}", "", f"_{len(ordered)} video(s)._", ""]

    # An index makes the doc usable once it's long — which is the whole point.
    out.append("## Index")
    out.append("")
    for entry in ordered:
        out.append(f"- [{entry.get('title', 'Untitled')}](#{_anchor(entry)}) — {entry.get('added', '')}")
    out += ["", "---", ""]

    for entry in ordered:
        out += _render_entry(entry)

    return "\n".join(out).rstrip() + "\n"


def _anchor(entry: dict) -> str:
    """GitHub-style heading anchor for the index links."""
    text = f"{entry.get('title', 'untitled')} {entry.get('video_id', '')}".lower()
    return "".join(c if c.isalnum() or c == " " else "" for c in text).strip().replace(" ", "-")


def _render_entry(entry: dict) -> list[str]:
    out = [
        f"## {entry.get('title', 'Untitled')} <a id=\"{_anchor(entry)}\"></a>",
        "",
        f"{entry.get('channel', '')} · [watch]({entry.get('url', '')}) · added {entry.get('added', '')}",
        "",
    ]

    if entry.get("tldr"):
        out += [entry["tldr"], ""]

    if entry.get("for_you"):
        out += [f"**Why it matters to you:** {entry['for_you']}", ""]

    if entry.get("claims"):
        from .render import BASIS_LABELS

        out += ["### Claims made by the speaker", ""]
        for claim in entry["claims"]:
            if isinstance(claim, str):  # entries written before basis existed
                out.append(f"- {claim}")
                continue
            label = BASIS_LABELS.get(claim.get("basis", ""))
            support = claim.get("support") or ""
            suffix = f" _({label}{': ' + support if support else ''})_" if label else ""
            out.append(f"- {claim.get('claim', '')}{suffix}")
        out.append("")

    if entry.get("highlights"):
        out += ["### Where to look", ""]
        for item in entry["highlights"]:
            mark = "" if item.get("verified", True) else "~"
            line = f"- **[{mark}{item.get('time', '')}]({item.get('url', '')})** {item.get('title', '')}"
            out.append(line)
            if item.get("detail"):
                out.append(f"  {item['detail']}")
            if item.get("relevance"):
                out.append(f"  _For you: {item['relevance']}_")
        out.append("")

    if entry.get("quotes"):
        out += ["### Quotes", ""]
        for item in entry["quotes"]:
            out.append(f"- \"{item['quote']}\" — [{item.get('time', '')}]({item.get('url', '')})")
        out.append("")

    out += ["---", ""]
    return out


def update_notes(
    doc_path: Path,
    video_id: str,
    meta: dict,
    analysis: dict,
) -> tuple[Path, int]:
    """Add or refresh this video in the reference doc. Returns (path, total)."""
    root = doc_path.parent
    manifest_name = doc_path.stem + ".json"

    entries = upsert_note(
        load_notes(root, manifest_name),
        make_note(video_id, meta, analysis),
    )
    save_notes(root, entries, manifest_name)

    root.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(render_notes(entries), encoding="utf-8")

    return doc_path, len(entries)
