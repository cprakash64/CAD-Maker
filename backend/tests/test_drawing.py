"""Drawing views (render + export) and Drawing-to-CAD Assist interpretation."""
import base64

from app.drawing import STANDARD_VIEWS
from app.drawing.interpret import interpret_image, to_design_spec
from app.drawing.render import render_view
from app.llm.mock_provider import MockLLMProvider
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.drawing_spec import DrawingInterpretationSpec


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 5},
        holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
    )


# --- Rendering ------------------------------------------------------------
def test_render_all_views_png_and_svg():
    spec = _bracket()
    for view in STANDARD_VIEWS:
        png = render_view(spec, view, "png")
        svg = render_view(spec, view, "svg")
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
        assert b"<svg" in svg[:600]


# --- Drawing view export endpoint ----------------------------------------
def test_view_export_endpoint(client, auth):
    h = auth["headers"]
    did = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x5mm with two M6 holes"},
        headers=h,
    ).json()["id"]

    r = client.get(f"/api/designs/{did}/views/top?fmt=png", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert len(r.content) > 1000

    rs = client.get(f"/api/designs/{did}/views/front?fmt=svg", headers=h)
    assert rs.status_code == 200 and "svg" in rs.headers["content-type"]

    assert client.get(f"/api/designs/{did}/views/nope", headers=h).status_code == 404


def test_view_export_requires_owner(client, auth, auth2):
    did = client.post(
        "/api/designs/create", json={"prompt": "bracket 80x40x5mm"}, headers=auth["headers"]
    ).json()["id"]
    assert client.get(
        f"/api/designs/{did}/views/top", headers=auth2["headers"]
    ).status_code == 404


# --- DrawingInterpretationSpec validation --------------------------------
def test_interpretation_actionable_logic():
    good = DrawingInterpretationSpec(
        suggested_object_type="rectangular_bracket",
        overall_dimensions={"width": 80, "depth": 40, "thickness": 5},
        overall_confidence=0.85,  # v0.3.6: must clear the 0.75 threshold
    )
    assert good.is_actionable()

    needs_info = DrawingInterpretationSpec(
        suggested_object_type="rectangular_bracket",
        clarification_questions=[{"field": "thickness", "question": "How thick?"}],
    )
    assert not needs_info.is_actionable()

    unsupported = DrawingInterpretationSpec(unsupported_reason="No matching template")
    assert not unsupported.is_actionable()


def test_interpretation_maps_to_design_spec():
    interp = DrawingInterpretationSpec(
        suggested_object_type="rectangular_bracket",
        overall_dimensions={"width": 80, "depth": 40, "thickness": 5},
        holes=[{"diameter": 6.6, "count": 2}],
        overall_confidence=0.85,
    )
    spec = to_design_spec(interp)
    assert spec is not None
    assert spec.object_type == "rectangular_bracket"
    assert spec.dimensions["width"] == 80
    assert len(spec.holes) == 2


# --- Mocked image-to-drawing flow ----------------------------------------
def test_mock_interpret_image_returns_actionable():
    # v0.3.6: the mock can't read images, so it needs a 'correct interpretation'
    # hint to classify (otherwise it asks for clarification).
    interp = interpret_image(
        b"x" * 400, "image/png", provider=MockLLMProvider(),
        hint="rectangular mounting bracket 80mm wide 40mm deep 5mm thick",
    )
    assert interp.suggested_object_type == "rectangular_bracket"
    assert interp.is_actionable()
    assert interp.assumptions  # uncertainty surfaced


def test_mock_interpret_blank_image_asks_clarification():
    interp = interpret_image(b"x", "image/png", provider=MockLLMProvider())
    assert not interp.is_actionable()
    assert interp.clarification_questions


def test_drawing_interpret_and_confirm_endpoints(client, auth):
    h = auth["headers"]
    img = base64.b64decode(
        # tiny but >40-char payload so the mock treats it as a real drawing
        base64.b64encode(b"a mechanical drawing of a plate" * 5)
    )
    r = client.post(
        "/api/drawings/interpret",
        files={"file": ("drawing.png", img, "image/png")},
        data={"hint": "rectangular mounting plate 80mm wide 40mm deep 5mm thick, two M6 holes"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    interp = r.json()
    assert interp["suggested_object_type"] == "rectangular_bracket"

    c = client.post("/api/drawings/confirm", json=interp, headers=h)
    assert c.status_code == 200, c.text
    design = c.json()
    assert design["object_type"] == "rectangular_bracket"
    assert len(design["exports"]) == 2


def test_drawing_confirm_rejects_non_actionable(client, auth):
    interp = DrawingInterpretationSpec(
        unsupported_reason="No matching template"
    ).model_dump(mode="json")
    r = client.post("/api/drawings/confirm", json=interp, headers=auth["headers"])
    assert r.status_code == 422
