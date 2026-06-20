"""One-shot drawing → CAD with drawing-scale dimension inference.

The provided flanged pipe branch drawing is dimensioned in drawing-scale units
(14.8 / 15 / Ø12 with a "12xØ1" bolt callout). The pipeline must:
  * infer ONE consistent mm scale (×10 here) instead of building a 15mm-tall
    fitting with twelve 1mm holes,
  * auto-generate via POST /api/drawings/generate (no second confirm step),
  * produce main pipe + side branch + top/bottom/branch flanges with a 12-hole
    bolt circle EACH (36 flange holes total),
  * fail the position-aware audit for wrong geometry (12 total holes, missing
    branch flange, generic default sizes) even though STEP/STL export fine,
  * not auto-generate non-mechanical images.
"""
from __future__ import annotations

import io

from app.drawing.scale import infer_scale
from app.schemas.drawing_spec import DrawingInterpretationSpec

# The provided drawing: values are drawing-scale (cm-like), units not marked.
DRAWING_INTERP = {
    "title": "Flanged pipe branch drawing",
    "suggested_object_type": "flanged_pipe_branch",
    "detected_object_type": "flanged pipe branch / tee",
    "overall_confidence": 0.78,
    "drawing_units_confidence": 0.4,
    "overall_dimensions": {
        "flange_outer_diameter_mm": 12,     # Ø12  -> Ø120mm
        "main_pipe_length_mm": 14.8,        # 14.8 -> 148mm
        "branch_pipe_outer_diameter_mm": 4.8,
        "main_pipe_outer_diameter_mm": 7.2,
        "wall_thickness_mm": 0.5,           # 0.5  -> 5mm
        "flange_thickness_mm": 1.0,
    },
    "holes": [{"diameter": 1, "count": 12, "callout": "12xØ1"}],
    "clarification_questions": [
        {"field": "bolt_circle_diameter", "question": "What is the flange PCD?"},
    ],
}


def test_scale_inference_cm_drawing():
    scaled = infer_scale(DrawingInterpretationSpec(**DRAWING_INTERP))
    assert scaled.scale == 10.0
    assert scaled.dimensions["flange_outer_diameter_mm"] == 120
    assert scaled.dimensions["main_pipe_length_mm"] == 148
    assert scaled.holes[0].diameter == 10 and scaled.holes[0].count == 12
    assert any("centimetres" in a or "drawing-scale" in a for a in scaled.assumptions)


def test_scale_explicit_mm_is_obeyed_with_warning():
    tiny = DrawingInterpretationSpec(**{
        **DRAWING_INTERP, "units": "mm", "drawing_units_confidence": 0.95,
    })
    scaled = infer_scale(tiny)
    assert scaled.scale == 1.0, "explicit mm must be obeyed"
    assert any("double-check" in w for w in scaled.warnings), \
        "a physically tiny explicit-mm part must warn"


def test_scale_real_size_drawing_untouched():
    real = DrawingInterpretationSpec(**{
        **DRAWING_INTERP,
        "overall_dimensions": {"flange_outer_diameter_mm": 120,
                               "main_pipe_length_mm": 148},
        "holes": [{"diameter": 1, "count": 12, "callout": "12xØ1"}],
    })
    scaled = infer_scale(real)
    assert scaled.scale == 1.0
    # ...but the Ø1 callout on a Ø120 flange is still a drawing-scale value.
    assert scaled.holes[0].diameter == 10


def _generate(client, auth, hint: str | None = None, body: dict | None = None):
    """One-shot endpoint with a tiny png; mock provider classifies via hint."""
    files = {"file": ("drawing.png", io.BytesIO(b"\x89PNG fake image bytes"), "image/png")}
    data = {"hint": hint} if hint else {}
    r = client.post("/api/drawings/generate", files=files, data=data,
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def test_one_shot_generate_no_second_button(client, auth):
    out = _generate(client, auth,
                    hint="flanged pipe branch, 12 holes per flange, 90mm main pipe")
    assert out["generated"] is True, out["interpretation"]
    d = out["design"]
    assert d is not None and not d["needs_clarification"]
    fmts = {e["fmt"] for e in d["exports"]}
    assert {"step", "stl"} <= fmts
    ids = {f["id"] for f in d["features"]}
    assert {"main_pipe", "branch_pipe",
            "top_flange", "bottom_flange", "branch_flange"} <= ids
    assert d["feature_audit_passed"] is True


def test_one_shot_does_not_generate_non_mechanical(client, auth):
    out = _generate(client, auth, hint="a watercolor painting of a sunset")
    assert out["generated"] is False
    assert out["design"] is None


def test_confirm_drawing_scale_model_36_holes(client, auth):
    """The full drawing case: scaled dims honored, 12-hole pattern on each of
    the three flanges (36 total), STEP+STL, passing positional audit."""
    r = client.post("/api/drawings/confirm", json=DRAWING_INTERP, headers=auth["headers"])
    assert r.status_code == 200, r.text
    d = r.json()
    assert {e["fmt"] for e in d["exports"]} >= {"step", "stl"}

    # Drawing proportions honored: flange Ø120, height 148 — not generic defaults.
    bb = d["bounding_box_mm"]
    assert abs(bb["x"] - 120) < 1 or abs(bb["y"] - 120) < 1, bb
    assert abs(bb["z"] - 148) < 1, bb

    audit = {i["feature_id"]: i for i in d["feature_audit"]}
    for fid in ("main_pipe", "branch_pipe", "main_bore", "branch_bore",
                "top_flange", "bottom_flange", "branch_flange",
                "top_bolt_pattern", "bottom_bolt_pattern", "branch_bolt_pattern"):
        assert fid in audit, f"audit missing {fid}"
        assert audit[fid]["satisfied"], audit[fid]
    assert d["feature_audit_passed"] is True

    # 36 flange bolt holes (12 per flange × 3) stated and audited above.
    text = " ".join(d["assumptions"]).lower()
    assert "36 flange holes total" in text or "36" in text
    assert "drawing-scale" in text or "centimetres" in text, \
        "scale inference must be a visible assumption"


def test_drawing_prompt_preserves_full_geometry():
    """The synthesized prompt must carry the whole detected anatomy + scaled
    dimensions — never collapse to 'pipe branch, 12x1mm holes'."""
    from app.cad.plan import deterministic
    from app.routers.drawings import _drawing_to_prompt

    interp = DrawingInterpretationSpec(**DRAWING_INTERP)
    prompt = _drawing_to_prompt(interp)
    low = prompt.lower()
    # Structural anatomy is explicit.
    for phrase in ("main run pipe", "central bore", "side branch pipe",
                   "three circular flanges", "top, bottom, and branch",
                   "bolt-hole circle"):
        assert phrase in low, f"prompt lost anatomy: {phrase!r}\n{prompt}"
    # Scaled dimensions + per-flange callout + scale note are preserved.
    assert "120mm flange outer diameter" in low
    assert "148mm main pipe length" in low
    assert "12x 10mm bolt holes per flange" in low
    assert "converted to millimetres" in low
    assert "1mm holes" not in low, "drawing-scale Ø1 must not survive"

    # And it round-trips: the deterministic planner rebuilds the SAME geometry.
    plan = deterministic.plan(low)
    assert plan is not None and plan.object_type == "flanged_pipe_branch"
    flanges = [f for f in plan.features if f.kind.value == "circular_flange"]
    assert len(flanges) == 3
    assert all(int(f.p("bolt_count")) == 12 for f in flanges)
    top = next(f for f in flanges if f.id == "top_flange")
    assert abs(top.p("od") - 120) < 0.01 and abs(top.p("bolt_diameter") - 10) < 0.01


def test_drawing_prompt_rescues_callout_without_dimensions():
    """Vision read only the '12xØ1' callout, no overall dims: the prompt must
    still describe the full part with plausible Ø10 bolt holes, not Ø1."""
    from app.routers.drawings import _drawing_to_prompt

    interp = DrawingInterpretationSpec(**{
        **DRAWING_INTERP, "overall_dimensions": {},
    })
    prompt = _drawing_to_prompt(interp).lower()
    assert "12x 10mm bolt holes per flange" in prompt, prompt
    assert "three circular flanges" in prompt


# --- wrong geometry must FAIL the audit even though it exports ----------------
# The real synthesized full-geometry prompt for DRAWING_INTERP (kept in the
# current _drawing_to_prompt format — anatomy + scaled dims + callout).
DRAWING_PROMPT = (
    "Create a flanged pipe branch: a vertical main run pipe with a central "
    "bore, a perpendicular side branch pipe with its own bore, and three "
    "circular flanges (top, bottom, and branch), each carrying a repeated "
    "bolt-hole circle. Dimensions from the drawing: 120mm flange outer "
    "diameter, 148mm main pipe length, 48mm branch pipe outer diameter, "
    "72mm main pipe outer diameter, 5mm wall thickness, 10mm flange "
    "thickness, 12x 10mm bolt holes per flange. Drawing-scale dimensions "
    "were converted to millimetres. All dimensions in mm.")


def _tee_plan(**overrides):
    from app.cad.plan import deterministic
    plan = deterministic.plan(DRAWING_PROMPT.lower())
    assert plan is not None
    for f in plan.features:
        for key, val in overrides.get(f.id, {}).items():
            f.params[key] = val
    if "drop" in overrides:
        plan.features = [f for f in plan.features if f.id not in overrides["drop"]]
    return plan


def test_audit_fails_with_only_12_total_holes():
    """4 holes per flange (12 total) must fail when the drawing says 12 each."""
    from app.cad.plan.audit import audit_plan
    plan = _tee_plan(**{
        "top_flange": {"bolt_count": 4}, "bottom_flange": {"bolt_count": 4},
        "branch_flange": {"bolt_count": 4},
    })
    audit = audit_plan(DRAWING_PROMPT, plan)
    failed = {i.feature_id for i in audit.failures()}
    assert {"top_bolt_pattern", "bottom_bolt_pattern", "branch_bolt_pattern"} <= failed


def test_audit_fails_without_branch_flange():
    from app.cad.plan.audit import audit_plan
    plan = _tee_plan(drop={"branch_flange"})
    audit = audit_plan(DRAWING_PROMPT, plan)
    failed = {i.feature_id for i in audit.failures()}
    assert "branch_flange" in failed and "branch_bolt_pattern" in failed


def test_audit_fails_generic_default_tee():
    """A generic tee whose flange OD ignores the drawing's Ø120 must fail."""
    from app.cad.plan.audit import audit_plan
    from app.cad.plan import deterministic
    generic = deterministic._plan_pipe_tee("t pipe fitting")  # default proportions
    audit = audit_plan(DRAWING_PROMPT, generic, None)
    failed = {i.feature_id for i in audit.failures()}
    assert "top_flange" in failed or "top_bolt_pattern" in failed, \
        f"generic default tee must fail the drawing audit, failures={failed}"
