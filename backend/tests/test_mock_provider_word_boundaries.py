"""Regression coverage for a word-boundary bug found via the phase-4/5
evaluation baseline (root cause: family routing / classification).

app.llm.mock_provider.MockLLMProvider.plan_general_cad used a bare substring
check (`"ring" in text`) to detect ring/washer/bushing-shaped requests. Since
"bearing" contains "ring" as a substring (bea-RING), any prompt mentioning a
bearing without a recognized housing/standard context was silently
misrouted into the ring/tube primitive branch, which ignores width/height
entirely (rings are parameterized by diameter, not "how wide"). Fixed with
`\\bring\\b`-style word-boundary regex, matching this file's own established
convention for every OTHER keyword check nearby.
"""
from __future__ import annotations

from app.llm.mock_provider import MockLLMProvider

provider = MockLLMProvider()


def test_bearing_no_longer_false_matches_ring_substring():
    """The exact bug: 'bearing' must never trigger the ring/tube branch."""
    result = provider.plan_general_cad(
        "A bearing holder for a 22mm outer diameter bearing, 8mm bore, 7mm wide."
    )
    assert result is None or result.get("object_name") != "ring"


def test_genuine_ring_washer_bushing_prompts_still_work():
    """The fix must not break the real, intended matches."""
    for word in ("ring", "washer", "bushing"):
        result = provider.plan_general_cad(
            f"A {word}, 20mm outer diameter, 10mm bore, 3mm thick."
        )
        assert result is not None, word
        assert result["object_name"] == "ring", word
        assert result["primitives"][0]["kind"] == "tube", word


def test_genuine_cube_block_box_prompts_still_work():
    for word in ("cube", "block", "box"):
        result = provider.plan_general_cad(f"A {word}, 40mm, with a 10mm hole.")
        assert result is not None, word
        assert result["object_name"] == "block", word
        assert result["primitives"][0]["kind"] == "box", word


def test_word_that_merely_contains_block_or_box_does_not_false_match():
    """Same class of bug, the other branch: a word containing 'block'/'box'
    as a substring (not the standalone word) must not trigger the block
    branch either."""
    result = provider.plan_general_cad("An unblockable widget, 40mm across.")
    assert result is None or result.get("object_name") != "block"


def test_bearing_prompt_asks_rather_than_silently_building_wrong_geometry(client, auth):
    """End-to-end: after the fix, a generic (unnamed-standard) bearing
    description with an unspecified fit correctly asks for clarification
    instead of silently generating a ring that ignores the requested width."""
    r = client.post(
        "/api/designs/create",
        json={"prompt": "A bearing holder for a 22mm outer diameter bearing, "
                        "8mm bore, 7mm wide."},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is True
    assert not d["exports"]


def test_bearing_housing_two_word_phrase_still_routes_to_its_own_builder(client, auth):
    """Unaffected control: 'bearing housing' (two words) has always matched
    its own dedicated feature-graph builder (app.cad.fallback_graphs.
    bearing_housing), checked before plan_general_cad is ever reached -- the
    fix must not disturb this working path."""
    r = client.post(
        "/api/designs/create",
        json={"prompt": "A simple bearing housing for a 20mm shaft"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["needs_clarification"] is False
    assert d["exports"]
