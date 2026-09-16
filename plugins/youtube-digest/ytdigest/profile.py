"""Storage and prompt-rendering for the derived interest profile.

The profile is a plain JSON file you can open and edit. That's deliberate: an
inferred model of what someone cares about is worth nothing if they can't see
what it concluded or correct it when it's wrong.

`audit.py` writes it. This module loads it and renders it into the compact block
that gets prepended to the analysis prompt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# YTDIGEST_HOME keeps state outside the code directory, which a plugin update replaces.
DATA_HOME = Path(os.environ.get("YTDIGEST_HOME") or Path(__file__).resolve().parent.parent)
DEFAULT_PROFILE_PATH = DATA_HOME / "profile.json"

# Ordered strongest-first; used to sort interests when rendering the prompt.
WEIGHTS = {"high": 0, "medium": 1, "low": 2}


class ProfileError(Exception):
    """Raised when a profile file exists but can't be used."""


def load(path: Path = DEFAULT_PROFILE_PATH) -> dict | None:
    """Return the profile, or None if there isn't one yet.

    A missing profile is normal — it means "run --build-profile" or "stay in
    generic mode", not an error.
    """
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(
            f"{path} is not valid JSON ({exc}). Fix it, delete it, "
            f"or re-run with --build-profile."
        ) from exc

    if not isinstance(data, dict):
        raise ProfileError(f"{path} should contain a JSON object.")

    return data


def save(profile: dict, path: Path = DEFAULT_PROFILE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def interests(profile: dict) -> list[dict]:
    """Interests sorted strongest-first, ignoring malformed entries."""
    raw = profile.get("interests")
    if not isinstance(raw, list):
        return []

    valid = [i for i in raw if isinstance(i, dict) and i.get("topic")]
    return sorted(valid, key=lambda i: WEIGHTS.get(str(i.get("weight", "medium")).lower(), 1))


def render_for_prompt(profile: dict) -> str:
    """Compact text block describing this person, for the analysis prompt.

    Kept terse on purpose. The transcript is the bulk of the prompt and the
    thing the model should be reasoning hardest about; the profile is a lens,
    not the subject.
    """
    lines: list[str] = []

    summary = profile.get("summary")
    if summary:
        lines += [f"Who they are: {summary}", ""]

    items = interests(profile)
    if items:
        lines.append("What they care about, strongest first:")
        for item in items:
            weight = str(item.get("weight", "medium")).lower()
            signals = item.get("signals")
            hint = ""
            if isinstance(signals, list) and signals:
                hint = f" (cues: {', '.join(str(s) for s in signals[:6])})"
            lines.append(f"  - [{weight}] {item['topic']}{hint}")
        lines.append("")

    learning = profile.get("learning")
    if isinstance(learning, list) and learning:
        lines += ["Actively learning (explanations here have extra value):"]
        lines += [f"  - {item}" for item in learning]
        lines.append("")

    deprioritize = profile.get("deprioritize")
    if isinstance(deprioritize, list) and deprioritize:
        lines += ["Already fluent or uninterested — do not spend highlights on:"]
        lines += [f"  - {item}" for item in deprioritize]
        lines.append("")

    return "\n".join(lines).strip()


def describe(profile: dict) -> str:
    """Human-readable dump for --show-profile."""
    out = [
        f"Profile: {DEFAULT_PROFILE_PATH}",
        f"Generated: {profile.get('generated_at', 'unknown')}",
    ]

    evidence = profile.get("evidence")
    if isinstance(evidence, dict) and evidence:
        parts = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in evidence.items())
        out.append(f"Built from — {parts}")

    out += ["", render_for_prompt(profile) or "(empty profile)"]
    return "\n".join(out)
