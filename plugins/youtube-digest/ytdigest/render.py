"""The report stage: structured analysis in, something readable out.

The single most useful thing this module produces is a clickable deep link per
highlight. Most terminals turn a bare URL into a link, so `https://youtu.be/ID?t=412`
drops you at the exact moment rather than making you scrub for it.
"""

from __future__ import annotations

import sys

from .fetch import timestamp_url
from .transcript import format_timestamp

# ANSI escape codes. \033[1m is bold, \033[0m resets. Disabled automatically
# when stdout isn't a terminal, so piping to a file doesn't produce escape soup.
_CODES = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "reset": "\033[0m",
}


def _use_color() -> bool:
    return sys.stdout.isatty()


def _c(text: str, *styles: str) -> str:
    if not _use_color():
        return text
    prefix = "".join(_CODES[s] for s in styles)
    return f"{prefix}{text}{_CODES['reset']}"


def item_seconds(item: dict) -> tuple[float, bool]:
    """Get an item's time, preferring the verified one.

    `timestamp_s` is written by verify.py after locating the anchor phrase in
    the transcript, so it's authoritative. We only fall back to the model's own
    `timestamp` string when verification couldn't find the anchor.
    """
    if item.get("verified") and "timestamp_s" in item:
        return float(item["timestamp_s"]), True
    return parse_timestamp(item.get("timestamp", 0)), False


# How much weight a claim's stated backing actually carries. Shown next to every
# claim so the reader can tell a measurement from a bare assertion at a glance —
# without it, a summary flattens "he ran this and got 27 minutes" and "he says
# it's the best model" into the same confident-looking bullet.
BASIS_LABELS = {
    "measured": "measured",
    "demonstrated": "demonstrated",
    "cited": "cited",
    "asserted": "asserted, no evidence given",
    "speculation": "speculation",
}


def claim_parts(claim) -> tuple[str, str, str]:
    """Normalize a claim to `(text, basis, support)`.

    Claims used to be plain strings, and cached analyses on disk still hold
    that shape, so both are accepted.
    """
    if isinstance(claim, str):
        return claim, "", ""

    if isinstance(claim, dict):
        text = str(claim.get("claim") or claim.get("text") or "")
        basis = str(claim.get("basis") or "").lower().strip()
        return text, basis, str(claim.get("support") or "")

    return str(claim), "", ""


def parse_timestamp(value) -> float:
    """Turn `H:MM:SS`, `M:SS`, or a bare number of seconds into seconds.

    The model returns timestamps as strings, but we need integers to build seek
    URLs. Malformed values return 0.0 rather than raising — a highlight with a
    wrong link is still worth showing, whereas a crash loses the whole report.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0

    parts = value.strip().strip("[]").split(":")
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return 0.0

    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 1:
        return numbers[0]
    return 0.0


def _header_lines(meta: dict, source: str) -> list[str]:
    duration = format_timestamp(meta["duration"]) if meta.get("duration") else "unknown"
    return [
        _c(meta.get("title", "(untitled)"), "bold"),
        _c(f"{meta.get('uploader', '(unknown)')}  ·  {duration}  ·  via {source}", "dim"),
    ]


def render_terminal(meta: dict, analysis: dict, video_id: str, source: str) -> str:
    """Build the human-facing report."""
    out: list[str] = ["", *_header_lines(meta, source), ""]

    if analysis.get("tldr"):
        out += [_c("TL;DR", "bold", "yellow"), analysis["tldr"], ""]

    # Sits directly under the TL;DR because it's the decision the reader is
    # actually trying to make: is this worth my time?
    if analysis.get("for_you"):
        out += [_c("FOR YOU", "bold", "yellow"), analysis["for_you"], ""]

    highlights = analysis.get("highlights") or []
    if highlights:
        out.append(_c("INTERESTING BITS", "bold", "yellow"))
        out.append("")
        for index, item in enumerate(highlights, start=1):
            seconds, verified = item_seconds(item)
            # A "~" prefix means we couldn't confirm this one against the
            # transcript, so treat the link as approximate.
            stamp = format_timestamp(seconds) if verified else f"~{format_timestamp(seconds)}"
            title = item.get("title", "(untitled)")

            out.append(f"{_c(f'{index}.', 'bold')} {_c(f'[{stamp}]', 'cyan')} {_c(title, 'bold')}")
            if item.get("why_interesting"):
                out.append(f"   {item['why_interesting']}")
            if item.get("detail"):
                out.append(f"   {_c(item['detail'], 'dim')}")
            if item.get("relevance"):
                out.append(f"   {_c('→ for you: ' + item['relevance'], 'yellow')}")
            out.append(f"   {_c(timestamp_url(video_id, seconds), 'cyan')}")
            out.append("")

    claims = analysis.get("key_claims") or []
    if claims:
        out.append(_c("CLAIMS MADE", "bold", "yellow"))
        for claim in claims:
            text, basis, support = claim_parts(claim)
            if not text:
                continue
            out.append(f"  • {text}")
            label = BASIS_LABELS.get(basis)
            if label:
                detail = f" — {support}" if support else ""
                out.append(f"    {_c(f'[{label}]{detail}', 'dim')}")
        out.append("")

    quotes = analysis.get("notable_quotes") or []
    if quotes:
        out.append(_c("QUOTES", "bold", "yellow"))
        for quote in quotes:
            seconds, verified = item_seconds(quote)
            stamp = format_timestamp(seconds) if verified else f"~{format_timestamp(seconds)}"
            out.append(f'  {_c(f"[{stamp}]", "cyan")} "{quote.get("quote", "")}"')
        out.append("")

    if analysis.get("skip_it_if"):
        out += [_c("SKIP IT IF", "bold", "yellow"), analysis["skip_it_if"], ""]

    return "\n".join(out)


def render_markdown(meta: dict, analysis: dict, video_id: str, source: str) -> str:
    """Build a saveable report. Same content, no ANSI, real markdown links."""
    duration = format_timestamp(meta["duration"]) if meta.get("duration") else "unknown"
    out = [
        f"# {meta.get('title', '(untitled)')}",
        "",
        f"**{meta.get('uploader', '(unknown)')}** · {duration} · "
        f"[watch]({ 'https://youtu.be/' + video_id })",
        "",
        f"*Transcript source: {source}*",
        "",
    ]

    if analysis.get("tldr"):
        out += ["## TL;DR", "", analysis["tldr"], ""]

    if analysis.get("for_you"):
        out += ["## For you", "", analysis["for_you"], ""]

    highlights = analysis.get("highlights") or []
    if highlights:
        out += ["## Interesting bits", ""]
        for item in highlights:
            seconds, verified = item_seconds(item)
            stamp = format_timestamp(seconds) if verified else f"~{format_timestamp(seconds)}"
            link = timestamp_url(video_id, seconds)
            out.append(f"### [{stamp}]({link}) — {item.get('title', '(untitled)')}")
            out.append("")
            if item.get("why_interesting"):
                out += [f"*{item['why_interesting']}*", ""]
            if item.get("detail"):
                out += [item["detail"], ""]
            if item.get("relevance"):
                out += [f"**For you:** {item['relevance']}", ""]

    claims = analysis.get("key_claims") or []
    if claims:
        out += ["## Claims made", "", "_What the speaker asserts, and what backs it._", ""]
        for claim in claims:
            text, basis, support = claim_parts(claim)
            if not text:
                continue
            label = BASIS_LABELS.get(basis)
            suffix = f" _({label}{': ' + support if support else ''})_" if label else ""
            out.append(f"- {text}{suffix}")
        out.append("")

    quotes = analysis.get("notable_quotes") or []
    if quotes:
        out += ["## Quotes", ""]
        for quote in quotes:
            seconds, verified = item_seconds(quote)
            stamp = format_timestamp(seconds) if verified else f"~{format_timestamp(seconds)}"
            link = timestamp_url(video_id, seconds)
            out += [f"> {quote.get('quote', '')}", "", f"— [{stamp}]({link})", ""]

    if analysis.get("skip_it_if"):
        out += ["## Skip it if", "", analysis["skip_it_if"], ""]

    return "\n".join(out)
