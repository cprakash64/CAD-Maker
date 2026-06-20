"""Ambiguity handling: a prompt lacking essential data must EITHER ask a
clarification OR generate with VISIBLE, stored defaults — never silently guess a
critical dimension and never crash.

These exercise the production feature-graph route (CAD_ENGINE=feature_graph) end
to end via the API on the offline mock provider.
"""
from __future__ import annotations

import pytest

# Recognizable part, but a build-critical dimension is missing -> must clarify.
CLARIFY_PROMPTS = [
    "make me a blind flange",
    "Create a rectangular mounting plate with four M6 holes",
]

# Recognizable part with enough to build; secondary dims are filled by documented
# defaults that must be surfaced as assumptions (not hidden).
DEFAULTS_PROMPTS = [
    "Create a bearing block for a 20mm shaft",
    "Create an L bracket",
]


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("prompt", CLARIFY_PROMPTS)
def test_missing_critical_dimension_asks_clarification(client, auth, prompt):
    d = _create(client, auth, prompt)
    assert d["needs_clarification"] is True, f"{prompt!r} should clarify"
    assert d["clarification_questions"], "a clarification must list the missing info"
    assert d["preview"] is None, "no geometry should be built when clarifying"
    assert not d["exports"], "nothing to export when clarifying"


@pytest.mark.parametrize("prompt", DEFAULTS_PROMPTS)
def test_underspecified_part_builds_with_visible_defaults(client, auth, prompt):
    d = _create(client, auth, prompt)
    assert d["needs_clarification"] is False, f"{prompt!r} should build with defaults"
    assert d["assumptions"], "defaults used to fill gaps MUST be surfaced as assumptions"
    assert {e["fmt"] for e in d["exports"]} == {"stl", "step"}
    assert all(e["size_bytes"] > 0 for e in d["exports"])
    # The trust artifact is present and the part is printable.
    assert d["dimension_report"] is not None
    assert d["print_readiness"] is not None
    assert d["print_readiness"]["printable"] is True


def test_requested_dimension_is_honored_not_defaulted(client, auth):
    """A dimension the user DID give (20mm shaft bore) must appear in the model,
    proving we don't override explicit values with defaults."""
    d = _create(client, auth, "Create a bearing block for a 20mm shaft")
    blob = (str(d.get("assumptions")) + str(d.get("features"))).lower()
    assert "20" in blob, "the explicit 20mm shaft dimension should be preserved"


@pytest.mark.parametrize("prompt", CLARIFY_PROMPTS + DEFAULTS_PROMPTS)
def test_never_silently_guesses(client, auth, prompt):
    """The core contract: either clarify (with questions) or build (with
    assumptions). A design that built silently with no assumptions, or clarified
    with no questions, is a hidden-assumption bug."""
    d = _create(client, auth, prompt)
    if d["needs_clarification"]:
        assert d["clarification_questions"], "clarified without saying what's missing"
    else:
        assert d["assumptions"], "built without surfacing any assumption"
