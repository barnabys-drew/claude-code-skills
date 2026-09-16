"""Profile storage and prompt rendering."""

import json

import pytest

from ytdigest.profile import ProfileError, describe, interests, load, render_for_prompt, save

PROFILE = {
    "summary": "A detection engineer who builds agent tooling.",
    "interests": [
        {"topic": "Home networking", "weight": "medium", "signals": ["VLANs"]},
        {"topic": "Detection engineering", "weight": "high", "signals": ["Sigma", "ATT&CK"]},
        {"topic": "Python structure", "weight": "low"},
    ],
    "learning": ["Idiomatic Python", "Terraform"],
    "deprioritize": ["Intro security content", "Kubernetes"],
}


def test_load_missing_profile_returns_none(tmp_path):
    """No profile is a normal state, not an error."""
    assert load(tmp_path / "absent.json") is None


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "profile.json"
    save(PROFILE, path)
    assert load(path) == PROFILE


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "profile.json"
    save(PROFILE, path)
    assert path.exists()


def test_corrupt_profile_raises_with_actionable_guidance(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ProfileError) as exc:
        load(path)

    assert "--build-profile" in str(exc.value)


def test_non_object_profile_is_rejected(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(["a", "list"]), encoding="utf-8")

    with pytest.raises(ProfileError):
        load(path)


def test_interests_sort_strongest_first():
    topics = [i["topic"] for i in interests(PROFILE)]
    assert topics == ["Detection engineering", "Home networking", "Python structure"]


def test_interests_skips_malformed_entries():
    profile = {"interests": [{"topic": "Real"}, {"no_topic": True}, "a string", None]}
    assert [i["topic"] for i in interests(profile)] == ["Real"]


def test_interests_on_missing_or_wrong_type():
    assert interests({}) == []
    assert interests({"interests": "not a list"}) == []


def test_render_includes_every_section():
    rendered = render_for_prompt(PROFILE)

    assert "A detection engineer who builds agent tooling." in rendered
    assert "[high] Detection engineering" in rendered
    assert "cues: Sigma, ATT&CK" in rendered
    assert "Idiomatic Python" in rendered
    assert "Kubernetes" in rendered


def test_render_orders_interests_by_weight():
    rendered = render_for_prompt(PROFILE)
    assert rendered.index("Detection engineering") < rendered.index("Home networking")


def test_render_handles_an_empty_profile():
    assert render_for_prompt({}) == ""


def test_render_omits_sections_that_are_absent():
    rendered = render_for_prompt({"summary": "Just a summary."})

    assert "Just a summary." in rendered
    assert "Actively learning" not in rendered
    assert "do not spend highlights" not in rendered


def test_render_caps_the_number_of_cues():
    """Long cue lists would crowd out the transcript in the prompt."""
    profile = {"interests": [{"topic": "X", "signals": [f"cue{i}" for i in range(20)]}]}
    rendered = render_for_prompt(profile)

    assert "cue5" in rendered
    assert "cue9" not in rendered


def test_describe_reports_provenance():
    profile = {**PROFILE, "generated_at": "2026-07-26", "evidence": {"github_repos": 18}}
    described = describe(profile)

    assert "2026-07-26" in described
    assert "github repos: 18" in described
