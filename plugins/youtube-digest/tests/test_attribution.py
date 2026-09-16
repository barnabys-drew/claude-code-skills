"""Attribution discipline.

The failure mode these guard against is a summary that reads as the tool's own
view of the subject rather than a report of what someone said — and one that
flattens "he measured this" and "he asserted this" into identically confident
bullets.
"""

from ytdigest.analyze import build_prompt
from ytdigest.notes import make_note, render_notes
from ytdigest.render import BASIS_LABELS, claim_parts, render_markdown, render_terminal
from ytdigest.script import build_script_prompt

META = {"title": "A Talk", "uploader": "A Channel", "duration": 600}


# --------------------------------------------------------------------------
# Claim normalization — cached analyses still hold the old string shape
# --------------------------------------------------------------------------


def test_claim_parts_accepts_the_new_object_shape():
    text, basis, support = claim_parts(
        {"claim": "X is true", "basis": "measured", "support": "ran it twice"}
    )

    assert text == "X is true"
    assert basis == "measured"
    assert support == "ran it twice"


def test_claim_parts_accepts_a_legacy_string():
    assert claim_parts("A bare claim") == ("A bare claim", "", "")


def test_claim_parts_normalizes_basis_case():
    assert claim_parts({"claim": "X", "basis": "MEASURED"})[1] == "measured"


def test_claim_parts_tolerates_missing_fields():
    assert claim_parts({"claim": "X"}) == ("X", "", "")
    assert claim_parts({}) == ("", "", "")


# --------------------------------------------------------------------------
# The analysis prompt must forbid speaking in its own voice
# --------------------------------------------------------------------------


def test_analysis_prompt_requires_attribution():
    prompt = build_prompt(META, "body")

    assert "Attribute every claim to the speaker" in prompt
    assert "Never write a claim in your own voice" in prompt


def test_analysis_prompt_forbids_editorializing():
    prompt = build_prompt(META, "body")
    assert "Never write evaluative editorial" in prompt


def test_analysis_prompt_requires_hedges_to_survive():
    """Stripping "I think" turns speculation into an assertion."""
    prompt = build_prompt(META, "body")

    assert "Preserve the speaker's own hedging" in prompt
    assert "Stripping a hedge turns speculation into an assertion" in prompt


def test_analysis_prompt_forbids_resolving_disputes():
    prompt = build_prompt(META, "body")
    assert "Do not resolve disputes" in prompt


def test_analysis_prompt_defines_every_basis_value():
    prompt = build_prompt(META, "body")

    for value in ("measured", "demonstrated", "cited", "asserted", "speculation"):
        assert f'"{value}"' in prompt or f'- "{value}"' in prompt or value in prompt


def test_analysis_prompt_blocks_grading_claims_on_outside_knowledge():
    prompt = build_prompt(META, "body")

    assert "Do not upgrade a claim because you know from elsewhere" in prompt
    assert "asserted" in prompt


def test_script_prompt_requires_attribution_always():
    prompt = build_script_prompt(META, {"tldr": "x"})

    assert "attribution ALWAYS" in prompt
    assert "never a bare" in prompt
    assert "reporting someone's views, not holding them" in prompt


# --------------------------------------------------------------------------
# Rendering must show the basis
# --------------------------------------------------------------------------

ANALYSIS = {
    "key_claims": [
        {"claim": "It took 27 minutes.", "basis": "measured", "support": "he ran it"},
        {"claim": "It is the best model.", "basis": "asserted", "support": ""},
        {"claim": "They delayed on purpose.", "basis": "speculation", "support": ""},
    ]
}


def test_terminal_shows_the_basis_for_each_claim():
    out = render_terminal(META, ANALYSIS, "dQw4w9WgXcQ", "captions")

    assert "It took 27 minutes." in out
    assert "measured" in out
    assert "asserted, no evidence given" in out
    assert "speculation" in out


def test_bare_assertions_are_visibly_distinguished_from_measurements():
    """The point of the whole feature."""
    out = render_terminal(META, ANALYSIS, "dQw4w9WgXcQ", "captions")

    measured_line = out.index("It took 27 minutes.")
    asserted_line = out.index("It is the best model.")

    assert "measured" in out[measured_line:asserted_line]
    assert "no evidence given" in out[asserted_line:]


def test_markdown_shows_the_basis():
    out = render_markdown(META, ANALYSIS, "dQw4w9WgXcQ", "captions")

    assert "## Claims made" in out
    assert "measured: he ran it" in out
    assert "asserted, no evidence given" in out


def test_markdown_labels_the_claims_section_as_the_speakers():
    out = render_markdown(META, ANALYSIS, "dQw4w9WgXcQ", "captions")
    assert "What the speaker asserts" in out


def test_legacy_string_claims_still_render():
    out = render_terminal(META, {"key_claims": ["An old-style claim."]}, "dQw4w9WgXcQ", "c")
    assert "An old-style claim." in out


def test_empty_claim_text_is_skipped():
    out = render_terminal(META, {"key_claims": [{"claim": "", "basis": "measured"}]}, "id", "c")
    assert "measured" not in out


# --------------------------------------------------------------------------
# The facts doc carries it through
# --------------------------------------------------------------------------


def test_notes_preserve_the_basis():
    note = make_note("abc12345678", META, ANALYSIS)

    assert note["claims"][0]["basis"] == "measured"
    assert note["claims"][1]["basis"] == "asserted"


def test_notes_doc_attributes_the_claims_section():
    doc = render_notes([make_note("abc12345678", META, ANALYSIS)])

    assert "Claims made by the speaker" in doc
    assert "asserted, no evidence given" in doc
    assert "speculation" in doc


def test_notes_doc_renders_legacy_string_claims():
    note = make_note("abc12345678", META, {"key_claims": ["Old style."]})
    assert "Old style." in render_notes([note])


def test_every_basis_value_has_a_label():
    for value in ("measured", "demonstrated", "cited", "asserted", "speculation"):
        assert value in BASIS_LABELS
