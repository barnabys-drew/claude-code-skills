"""Evidence gathering for the interest audit. No network, no model calls."""

import json
import subprocess
from types import SimpleNamespace

from ytdigest import audit
from ytdigest.audit import (
    format_evidence,
    gather_evidence,
    gather_github_repos,
    gather_local_projects,
    gather_memories,
)


def _make_project(root, name, readme=None):
    project = root / name
    project.mkdir(parents=True)
    if readme is not None:
        (project / "README.md").write_text(readme, encoding="utf-8")
    return project


def test_gather_local_projects_reads_heading_and_first_paragraph(tmp_path):
    _make_project(
        tmp_path,
        "rule-linter",
        "# rule-linter\n\nCLI that lints detection rules against a schema.\n",
    )

    projects = gather_local_projects(tmp_path)

    assert len(projects) == 1
    assert projects[0]["name"] == "rule-linter"
    assert "CLI that lints" in projects[0]["summary"]


def test_gather_local_projects_skips_badges_and_fences(tmp_path):
    _make_project(
        tmp_path,
        "proj",
        "# Title\n\n![badge](x.svg)\n\n```bash\ncode\n```\n\nThe real description.\n",
    )

    assert "The real description." in gather_local_projects(tmp_path)[0]["summary"]


def test_gather_local_projects_handles_a_missing_readme(tmp_path):
    _make_project(tmp_path, "bare")
    projects = gather_local_projects(tmp_path)

    assert projects[0]["name"] == "bare"
    assert projects[0]["summary"] == ""


def test_gather_local_projects_ignores_files_hidden_and_vendor_dirs(tmp_path):
    _make_project(tmp_path, "real")
    _make_project(tmp_path, ".hidden")
    _make_project(tmp_path, "node_modules")
    (tmp_path / "loose.json").write_text("{}", encoding="utf-8")

    assert [p["name"] for p in gather_local_projects(tmp_path)] == ["real"]


def test_gather_local_projects_on_a_missing_root(tmp_path):
    assert gather_local_projects(tmp_path / "nope") == []


def test_gather_local_projects_truncates_long_readmes(tmp_path):
    _make_project(tmp_path, "verbose", "# T\n\n" + "word " * 500)
    assert len(gather_local_projects(tmp_path)[0]["summary"]) <= 400


# --------------------------------------------------------------------------
# Memory frontmatter
# --------------------------------------------------------------------------

MEMORY = """---
name: project_example
description: "A thing worth remembering about this person"
metadata:
  type: project
---

Body text that must never be collected.
"""


def test_gather_memories_reads_frontmatter_only(tmp_path):
    (tmp_path / "a.md").write_text(MEMORY, encoding="utf-8")

    memories = gather_memories(tmp_path)

    assert len(memories) == 1
    assert memories[0]["note"] == "A thing worth remembering about this person"
    assert memories[0]["type"] == "project"  # nested under metadata:, still parsed
    assert "Body text" not in json.dumps(memories)


def test_gather_memories_skips_the_index_file(tmp_path):
    (tmp_path / "MEMORY.md").write_text("- [a](a.md) — hook\n", encoding="utf-8")
    (tmp_path / "a.md").write_text(MEMORY, encoding="utf-8")

    assert [m["name"] for m in gather_memories(tmp_path)] == ["a"]


def test_gather_memories_recurses_into_subdirectories(tmp_path):
    nested = tmp_path / "scope" / "memory"
    nested.mkdir(parents=True)
    (nested / "a.md").write_text(MEMORY, encoding="utf-8")

    assert len(gather_memories(tmp_path)) == 1


def test_gather_memories_ignores_files_without_frontmatter(tmp_path):
    (tmp_path / "plain.md").write_text("just a note\n", encoding="utf-8")
    (tmp_path / "unterminated.md").write_text("---\ndescription: x\n", encoding="utf-8")

    assert gather_memories(tmp_path) == []


def test_gather_memories_on_a_missing_root(tmp_path):
    assert gather_memories(tmp_path / "nope") == []


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

GH_JSON = json.dumps(
    [
        {
            "name": "rule-linter",
            "description": "Detection rule linting",
            "primaryLanguage": {"name": "Python"},
            "repositoryTopics": [{"name": "security"}, {"name": "mcp"}],
            "isPrivate": True,
        },
        {
            "name": "bare",
            "description": None,
            "primaryLanguage": None,
            "repositoryTopics": None,
            "isPrivate": False,
        },
    ]
)


def test_gather_github_repos_parses_the_gh_payload(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=GH_JSON, returncode=0)
    )

    repos = gather_github_repos()

    assert repos[0]["language"] == "Python"
    assert repos[0]["topics"] == ["security", "mcp"]
    # Null fields must degrade to empty, not crash.
    assert repos[1]["description"] == ""
    assert repos[1]["language"] == ""
    assert repos[1]["topics"] == []


def test_gather_github_repos_degrades_when_gh_is_unavailable(monkeypatch):
    """No gh, or not logged in — the audit still works off local evidence."""

    def missing(*a, **k):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(subprocess, "run", missing)
    assert gather_github_repos() == []


def test_gather_github_repos_degrades_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="", returncode=1)
    )
    assert gather_github_repos() == []


def test_gather_github_repos_degrades_on_bad_json(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="not json", returncode=0)
    )
    assert gather_github_repos() == []


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def test_gather_evidence_can_skip_github(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "gather_github_repos", lambda *a, **k: [{"name": "x"}])

    evidence = gather_evidence(tmp_path, tmp_path, use_github=False)
    assert evidence["github_repos"] == []


def test_format_evidence_renders_all_three_sources():
    evidence = {
        "local_projects": [{"name": "proj", "summary": "does a thing"}],
        "github_repos": [
            {"name": "repo", "description": "a repo", "language": "Python", "topics": ["x"]}
        ],
        "memories": [{"name": "m", "type": "project", "note": "a note"}],
    }

    text = format_evidence(evidence)

    assert "LOCAL PROJECTS" in text and "proj — does a thing" in text
    assert "GITHUB REPOSITORIES" in text and "topics: x" in text
    assert "ACCUMULATED NOTES" in text and "[project] a note" in text


def test_format_evidence_omits_empty_sections():
    text = format_evidence({"local_projects": [{"name": "p", "summary": ""}]})

    assert "LOCAL PROJECTS" in text
    assert "GITHUB REPOSITORIES" not in text
    assert "ACCUMULATED NOTES" not in text


def test_format_evidence_on_nothing():
    assert format_evidence({}) == ""
