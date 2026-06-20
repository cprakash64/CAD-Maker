"""P0-3/5: Drawing-to-CAD correctness (no bad bracket fallback, confidence gating)."""
from app.drawing.interpret import interpret_image, to_design_spec
from app.export.exporter import generate
from app.llm.mock_provider import MockLLMProvider
from app.schemas.drawing_spec import CONFIDENCE_THRESHOLD, DrawingInterpretationSpec

PROVIDER = MockLLMProvider()


def test_complex_drawing_without_hint_is_not_bracket():
    # A real-looking (random) image, no hint: mock cannot read it.
    interp = interpret_image(b"\x89PNG" + b"x" * 5000, "image/png", provider=PROVIDER)
    assert interp.suggested_object_type != "rectangular_bracket"
    assert interp.suggested_object_type is None
    assert not interp.is_actionable()
    assert interp.overall_confidence < CONFIDENCE_THRESHOLD
    assert interp.clarification_questions


def test_low_confidence_returns_clarification():
    interp = interpret_image(b"x" * 5000, "image/png", provider=PROVIDER)
    assert not interp.is_actionable()
    assert interp.clarification_questions or interp.missing_critical_dimensions


def test_flange_hint_routes_to_flanged_pipe_branch():
    interp = interpret_image(
        b"x" * 2000, "image/png", provider=PROVIDER,
        hint="Flanged pipe branch with circular flanges, a bolt circle of 12 bolts "
        "per flange, main pipe 90mm, section view A-A and an isometric reference.",
    )
    assert interp.suggested_object_type == "flanged_pipe_branch"
    assert interp.overall_confidence >= CONFIDENCE_THRESHOLD
    assert interp.is_actionable()
    assert interp.detected_object_type == "flanged_pipe_branch"


def test_complex_pipe_without_enough_data_is_unsupported_not_bracket():
    interp = interpret_image(
        b"x" * 2000, "image/png", provider=PROVIDER,
        hint="some complicated pipe spool assembly with branches and bolt circles",
    )
    # Recognized as pipe-ish but no clean template/data -> unsupported, not bracket.
    assert interp.suggested_object_type != "rectangular_bracket"
    if interp.suggested_object_type is None:
        assert interp.unsupported_reason or interp.clarification_questions


def test_confidence_threshold_blocks_low_confidence_actionable():
    interp = DrawingInterpretationSpec(
        suggested_object_type="rectangular_bracket",
        overall_dimensions={"width": 80, "depth": 40, "thickness": 5},
        overall_confidence=0.6,
    )
    assert not interp.is_actionable()  # below 0.75
    interp.overall_confidence = 0.8
    assert interp.is_actionable()


def test_flanged_pipe_branch_interpretation_maps_and_builds():
    interp = interpret_image(
        b"x" * 2000, "image/png", provider=PROVIDER,
        hint="flanged pipe branch, main pipe 100mm, 8 bolts per flange",
    )
    spec = to_design_spec(interp)
    assert spec is not None and spec.object_type == "flanged_pipe_branch"
    gen = generate(spec)
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"


# --- API confirm flow -----------------------------------------------------
def test_interpret_with_hint_and_confirm(client, auth):
    h = auth["headers"]
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("drawing.png", b"x" * 3000, "image/png")},
        data={"hint": "flanged pipe branch, main pipe 90mm, 8 bolts per flange"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    interp = r.json()
    assert interp["suggested_object_type"] == "flanged_pipe_branch"
    assert interp["overall_confidence"] >= CONFIDENCE_THRESHOLD

    c = client.post("/api/drawings/confirm", json=interp, headers=h)
    assert c.status_code == 200, c.text
    assert c.json()["object_type"] == "flanged_pipe_branch"
    assert len(c.json()["exports"]) == 2


def test_interpret_without_hint_cannot_confirm(client, auth):
    h = auth["headers"]
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("complex.png", b"x" * 5000, "image/png")},
        headers=h,
    )
    interp = r.json()
    assert interp["suggested_object_type"] != "rectangular_bracket"
    # Not actionable -> confirm must be refused.
    c = client.post("/api/drawings/confirm", json=interp, headers=h)
    assert c.status_code == 422
