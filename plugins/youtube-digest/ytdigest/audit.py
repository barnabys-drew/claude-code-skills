"""Derive an interest profile by auditing what someone actually builds.

Rather than asking "what are you into?" and taking the answer, this reads the
evidence: the projects on disk, the repos they publish, and the notes their
tooling has accumulated about them. Stated interests are aspirational; a
directory of fourteen half-finished projects is not.

What gets collected is deliberately shallow — directory names, README opening
lines, repo descriptions, topics, and memory frontmatter. No source code, no
file contents beyond a README's first paragraph, no commit diffs. The distilled
evidence is what goes to the model, and `--dry-run` prints exactly that so you
can see it before anything is sent.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path

from .analyze import DEFAULT_MODEL, DEFAULT_TIMEOUT, extract_json, run_claude

# Directories that are never interesting as "projects".
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build"}
_README_CHARS = 400


def _readme_summary(project: Path) -> str:
    """First heading plus first real paragraph of a project's README."""
    for name in ("README.md", "readme.md", "README.rst", "README.txt"):
        readme = project / name
        if not readme.exists():
            continue
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

        lines = [line.strip() for line in text.splitlines()]
        heading = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), "")

        # Walk for the first line of real prose. Fenced blocks are tracked with
        # a toggle rather than skipped line-by-line — otherwise the *contents*
        # of a code block sail through and end up in the evidence we send.
        body = ""
        in_fence = False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not line:
                continue
            if line.startswith(("#", "!", "[!", "---", "|", ">", "<", "*", "-", "=")):
                continue
            body = line
            break

        return f"{heading}. {body}"[:_README_CHARS].strip()

    return ""


def gather_local_projects(root: Path) -> list[dict]:
    """Project directories with a one-line description each."""
    if not root.is_dir():
        return []

    projects: list[dict] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue
        projects.append({"name": entry.name, "summary": _readme_summary(entry)})

    return projects


# Leading whitespace is allowed because `type:` is nested under `metadata:`.
_FRONTMATTER_FIELD = re.compile(r"^\s*(description|name|type):\s*(.+?)\s*$", re.M)


def gather_memories(memory_root: Path) -> list[dict]:
    """Descriptions from memory-file frontmatter.

    These are the highest-signal evidence available — they're already curated
    statements about this person, written over time rather than inferred in one
    pass. Only the frontmatter is read, never the body.
    """
    if not memory_root.is_dir():
        return []

    memories: list[dict] = []
    for path in sorted(memory_root.rglob("*.md")):
        if path.name == "MEMORY.md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Frontmatter is the block between the first two --- lines.
        if not text.startswith("---"):
            continue
        end = text.find("---", 3)
        if end == -1:
            continue

        fields = dict(_FRONTMATTER_FIELD.findall(text[3:end]))
        description = fields.get("description", "").strip("\"'")
        if description:
            memories.append({"name": path.stem, "type": fields.get("type", ""), "note": description})

    return memories


def gather_github_repos(limit: int = 100) -> list[dict]:
    """Repo names, descriptions, languages, and topics via the gh CLI.

    Returns an empty list if gh is missing or unauthenticated — the audit still
    works off local evidence alone, just with less to go on.
    """
    try:
        result = subprocess.run(
            [
                "gh", "repo", "list",
                "--limit", str(limit),
                "--json", "name,description,primaryLanguage,repositoryTopics,isPrivate",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    repos: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        language = (item.get("primaryLanguage") or {}).get("name", "")
        topics = [
            t.get("name", "")
            for t in (item.get("repositoryTopics") or [])
            if isinstance(t, dict)
        ]
        repos.append(
            {
                "name": item.get("name", ""),
                "description": item.get("description", "") or "",
                "language": language,
                "topics": [t for t in topics if t],
            }
        )

    return repos


def gather_evidence(code_root: Path, memory_root: Path, use_github: bool = True) -> dict:
    """Collect everything the audit will reason over."""
    return {
        "local_projects": gather_local_projects(code_root),
        "memories": gather_memories(memory_root),
        "github_repos": gather_github_repos() if use_github else [],
    }


def format_evidence(evidence: dict) -> str:
    """Render the evidence as the text that will be sent to the model."""
    lines: list[str] = []

    projects = evidence.get("local_projects") or []
    if projects:
        lines.append("LOCAL PROJECTS")
        for item in projects:
            summary = f" — {item['summary']}" if item.get("summary") else ""
            lines.append(f"  {item['name']}{summary}")
        lines.append("")

    repos = evidence.get("github_repos") or []
    if repos:
        lines.append("GITHUB REPOSITORIES")
        for item in repos:
            bits = [item.get("description", "")]
            if item.get("language"):
                bits.append(f"lang: {item['language']}")
            if item.get("topics"):
                bits.append(f"topics: {', '.join(item['topics'])}")
            detail = " | ".join(b for b in bits if b)
            lines.append(f"  {item['name']} — {detail}")
        lines.append("")

    memories = evidence.get("memories") or []
    if memories:
        lines.append("ACCUMULATED NOTES ABOUT THIS PERSON")
        for item in memories:
            lines.append(f"  [{item.get('type', '?')}] {item['note']}")
        lines.append("")

    return "\n".join(lines).strip()


_PROFILE_PROMPT = """\
Below is evidence about one person: the projects on their machine, the \
repositories they publish, and notes accumulated about them over time.

Build a profile of what they would find genuinely interesting in a technical \
video. This profile will be used to rank highlights from video transcripts.

Ground every interest in the evidence. If something appears once, it is weak \
evidence; if it recurs across projects, repos, and notes, it is strong. Do not \
pad the list with plausible-sounding adjacent topics they show no sign of \
caring about — a short accurate profile beats a long speculative one.

Pay attention to the difference between what they are expert in and what they \
are actively learning. Both matter, differently: for expert areas, only novel \
or contrarian material is worth their time, and basics are noise. For areas \
they are learning, clear explanation of fundamentals has real value.

Return ONLY this JSON object, no prose, no markdown fences:

{
  "summary": "2-3 sentences on who this person is technically",
  "interests": [
    {
      "topic": "specific area, not a broad category",
      "weight": "high | medium | low",
      "why": "the evidence that supports this, naming projects or repos",
      "signals": ["words or concepts in a transcript that indicate this topic"]
    }
  ],
  "learning": ["areas where they are a beginner and fundamentals have value"],
  "deprioritize": ["areas where they are already fluent, so introductory \
material is noise, plus topics the evidence suggests they do not care about"]
}

Aim for 6-12 interests. Be specific: "detection engineering and SIEM alert \
schemas" is useful, "security" is not.

---
EVIDENCE

{evidence}
---

Return the JSON object now."""


def build_profile(
    evidence: dict,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Send the evidence to the model and get back a structured profile."""
    prompt = _PROFILE_PROMPT.replace("{evidence}", format_evidence(evidence))
    profile = extract_json(run_claude(prompt, model=model, timeout=timeout))

    profile["generated_at"] = date.today().isoformat()
    profile["evidence"] = {
        "local_projects": len(evidence.get("local_projects") or []),
        "github_repos": len(evidence.get("github_repos") or []),
        "memories": len(evidence.get("memories") or []),
    }

    return profile
