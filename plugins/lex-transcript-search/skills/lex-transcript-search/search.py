#!/usr/bin/env python3
"""Find where a word or topic comes up in a Lex Fridman Podcast transcript.

Usage:
    search.py URL TERM [TERM ...] [--context N] [--json] [--refresh]

URL   a transcript page (https://lexfridman.com/<slug>-transcript/) or the
      episode page (https://lexfridman.com/<slug>/), which is resolved to its
      transcript link.
TERM  a case-insensitive Python regex. A plain word is a stem match, so
      "inferenc" hits inference / inferencing. Use \\b for whole words.

Stdlib only. Fetched HTML is cached in ~/.cache/lex-transcript-search/.
"""

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "lex-transcript-search"
UA = "Mozilla/5.0 (X11; Linux x86_64) lex-transcript-search"
TS_RE = re.compile(r"\((\d{1,2}):(\d{2}):(\d{2})\)|\((\d{1,2}):(\d{2})\)")
YT_RE = re.compile(r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})")


# ---------------------------------------------------------------- fetching
def fetch(url, refresh=False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = CACHE_DIR / f"{key}.html"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    path.write_text(body, encoding="utf-8")
    return body


def resolve_transcript_url(url, refresh=False):
    """Return (transcript_url, html). Follows episode page -> transcript link."""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    url = url.split("#")[0].split("?")[0].replace("http://", "https://")
    url = url.replace("://www.lexfridman.com", "://lexfridman.com").rstrip("/") + "/"
    page = fetch(url, refresh)
    if "-transcript" in url or 'class="ts-segment"' in page:
        return url, page
    # Episode page: prefer the transcript link whose slug matches this page;
    # the sidebar also links other episodes' transcripts.
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    links = re.findall(r'href="(https?://lexfridman\.com/[^"#?]*-transcript/?)"', page)
    for link in links:
        if link.rstrip("/").rsplit("/", 1)[-1] == f"{slug}-transcript":
            link = link.rstrip("/") + "/"
            return link, fetch(link, refresh)
    if links:
        sys.stderr.write(f"warning: no transcript link matching '{slug}', "
                         f"using first one found: {links[0]}\n")
        return links[0], fetch(links[0], refresh)
    raise SystemExit(f"error: no transcript link found on {url}. "
                     f"Try https://lexfridman.com/{slug}-transcript/ directly.")


# ---------------------------------------------------------------- parsing
def to_seconds(m):
    if m.group(1) is not None:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        h, mi, s = 0, int(m.group(4)), int(m.group(5))
    return h * 3600 + mi * 60 + s


def fmt(sec):
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def clean(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def video_id(page):
    ids = Counter(YT_RE.findall(page))
    return ids.most_common(1)[0][0] if ids else None


def title(page):
    m = re.search(r'<h1 class="entry-title">(.*?)</h1>', page, re.S) or \
        re.search(r"<title>(.*?)</title>", page, re.S)
    return clean(m.group(1)) if m else ""


def parse_segments(page):
    """Structured parse of <div class="ts-segment"> blocks (current site format).

    Chapter headings (<h2 id="chapterN_...">) are tracked so each segment knows
    its chapter. The table of contents is a plain link list, not ts-segments,
    so it is never counted as a match.
    """
    segs = []
    # Tokenise chapter headings and segments in document order.
    token_re = re.compile(
        r'<h2 id="chapter\d+_[^"]*">(?P<chap>.*?)</h2>'
        r'|<div class="ts-segment">(?P<seg>.*?)</div>', re.S)
    chapter, last_speaker = "", ""
    for m in token_re.finditer(page):
        if m.group("chap") is not None:
            chapter = clean(m.group("chap"))
            continue
        body = m.group("seg")
        name = re.search(r'<span class="ts-name">(.*?)</span>', body, re.S)
        ts = re.search(r'<span class="ts-timestamp">(.*?)</span>', body, re.S)
        txt = re.search(r'<span class="ts-text">(.*?)</span>', body, re.S)
        if not (ts and txt):
            continue
        tsm = TS_RE.search(clean(ts.group(1)))
        if not tsm:
            continue
        speaker = clean(name.group(1)) if name else ""
        speaker = speaker or last_speaker or "Unknown"
        last_speaker = speaker
        segs.append({"speaker": speaker, "seconds": to_seconds(tsm),
                     "timestamp": fmt(to_seconds(tsm)), "chapter": chapter,
                     "text": clean(txt.group(1))})
    return segs


def parse_plaintext(page):
    """Fallback for other layouts: speaker line, "(hh:mm:ss)" line, paragraph."""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", page)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|span)>", "\n", body)
    lines = [html.unescape(re.sub(r"<[^>]+>", "", l)).strip() for l in body.split("\n")]
    lines = [l for l in lines if l]
    segs = []
    for i, line in enumerate(lines):
        m = TS_RE.fullmatch(line)
        if not m or i == 0 or i + 1 >= len(lines):
            continue
        segs.append({"speaker": lines[i - 1], "seconds": to_seconds(m),
                     "timestamp": fmt(to_seconds(m)), "chapter": "",
                     "text": lines[i + 1]})
    return segs


def mark_highlights(segs):
    """The "Episode highlight" chapter at the top replays clips that appear
    again later in the episode. Flag them so a match isn't reported twice.

    Older episodes have no such chapter; the cold-open clips sit under
    "Introduction" before Lex's "The following is a conversation with ..." line,
    so everything before that line (if it is near the top) counts too."""
    intro = next((i for i, s in enumerate(segs[:40])
                  if re.search(r"following is a conversation with", s["text"], re.I)), 0)
    for i, s in enumerate(segs):
        s["highlight"] = "highlight" in s["chapter"].lower() or i < intro
    return segs


# ---------------------------------------------------------------- searching
def find_spans(text, patterns):
    spans = []
    for p in patterns:
        spans += [(m.start(), m.end(), p.pattern) for m in p.finditer(text) if m.end() > m.start()]
    return sorted(spans)


def search(segs, patterns, include_highlights=False):
    """Return (hits, skipped_highlight_hits); hits are (segment_index, spans)."""
    hits, skipped = [], 0
    for i, s in enumerate(segs):
        spans = find_spans(s["text"], patterns)
        if not spans:
            continue
        if s["highlight"] and not include_highlights:
            skipped += 1
            continue
        hits.append((i, spans))
    return hits, skipped


def whole_word(text, start, end):
    """Expand a stem match to the full word: 'inferenc' -> 'inference'."""
    while start > 0 and text[start - 1].isalnum():
        start -= 1
    while end < len(text) and (text[end].isalnum() or text[end] in "-'"):
        end += 1
    return text[start:end].strip("-'")


def highlight(text, spans, color):
    out, pos = [], 0
    for start, end, _ in spans:
        if start < pos:
            continue
        out.append(text[pos:start])
        word = text[start:end]
        out.append(f"\033[1;33m{word}\033[0m" if color else f">>{word}<<")
        pos = end
    out.append(text[pos:])
    return "".join(out)


def deep_link(vid, sec):
    return f"https://www.youtube.com/watch?v={vid}&t={sec}s" if vid else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("terms", nargs="+", help="case-insensitive regexes (stems ok)")
    ap.add_argument("-c", "--context", type=int, default=0,
                    help="also show N segments before/after each match")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--include-highlights", action="store_true",
                    help="also search the 'Episode highlight' clips at the start")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    ap.add_argument("--color", action="store_true", help="ANSI highlight instead of >>word<<")
    args = ap.parse_args()

    try:
        patterns = [re.compile(t, re.I) for t in args.terms]
    except re.error as e:
        raise SystemExit(f"error: bad regex: {e}")

    t_url, page = resolve_transcript_url(args.url, args.refresh)
    segs = parse_segments(page) or parse_plaintext(page)
    if not segs:
        raise SystemExit(f"error: could not parse any timestamped segments from {t_url}")
    mark_highlights(segs)
    vid = video_id(page)
    hits, skipped = search(segs, patterns, args.include_highlights)

    results = []
    for i, spans in hits:
        s = segs[i]
        end = segs[i + 1]["timestamp"] if i + 1 < len(segs) else None
        lo, hi = max(0, i - args.context), min(len(segs), i + args.context + 1)
        results.append({
            "speaker": s["speaker"], "start": s["timestamp"], "seconds": s["seconds"],
            "end": end, "chapter": s["chapter"], "link": deep_link(vid, s["seconds"]),
            "highlight_clip": s["highlight"],
            "matched": sorted({whole_word(s["text"], a, b).lower() for a, b, _ in spans}),
            "text": s["text"],
            "marked": highlight(s["text"], spans, False),
            "context": [{"offset": j - i, "speaker": segs[j]["speaker"],
                         "start": segs[j]["timestamp"], "text": segs[j]["text"]}
                        for j in range(lo, hi) if j != i],
        })

    if args.json:
        print(json.dumps({"transcript_url": t_url, "title": title(page), "video_id": vid,
                          "segments": len(segs), "terms": args.terms,
                          "skipped_highlight_clip_hits": skipped,
                          "matches": results}, indent=2, ensure_ascii=False))
        return

    print(f"{title(page)}\n{t_url}")
    print(f"video id: {vid or 'NOT FOUND (no YouTube links on page; timestamps only)'}"
          f" | {len(segs)} segments | terms: {', '.join(args.terms)}\n")
    skip_note = (f"({skipped} more hit(s) in the 'Episode highlight' clips at the start, "
                 f"which repeat later material; --include-highlights to show)") if skipped else ""
    if not results:
        print("No matches." + (" " + skip_note if skip_note else ""))
        stems = sorted({re.sub(r"(ing|ed|es|s|e|y|tion|ence|ance)$", "", t)
                        for t in args.terms} - set(args.terms))
        print("Try: " + (f"a shorter stem ({', '.join(map(repr, stems))}), " if stems else "") +
              "synonyms or related names, alternate spellings "
              "(transcripts are human-written: 'open source' vs 'open-source'), "
              "or --include-highlights.")
        return
    for n, r in enumerate(results, 1):
        rng = f"{r['start']} -> {r['end']}" if r["end"] else r["start"]
        tag = " [episode-highlight clip]" if r["highlight_clip"] else ""
        print(f"[{n}] {r['speaker']}  {rng}{tag}")
        if r["chapter"]:
            print(f"    chapter: {r['chapter']}")
        print(f"    link:    {r['link'] or '(no video id)'}")
        print(f"    matched: {', '.join(r['matched'])}")
        for c in r["context"]:
            if c["offset"] < 0:
                print(f"    ({c['start']}) {c['speaker']}: {c['text']}")
        marked = highlight(r["text"], find_spans(r["text"], patterns), args.color)
        print(f"  * ({r['start']}) {r['speaker']}: {marked}")
        for c in r["context"]:
            if c["offset"] > 0:
                print(f"    ({c['start']}) {c['speaker']}: {c['text']}")
        print()
    print(f"{len(results)} matching segment(s). Timestamps are the START of the "
          f"paragraph; the word may be up to a minute later.")
    if skip_note:
        print(skip_note)


if __name__ == "__main__":
    main()
