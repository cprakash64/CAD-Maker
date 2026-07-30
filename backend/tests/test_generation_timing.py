"""Compile/validate/export timing breakdown (docs/ops/observability.md)."""


def test_semantic_json_carries_timing_breakdown(client, auth):
    r = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    design_id = r.json()["id"]

    from app.database import SessionLocal
    from app.models import Design

    db = SessionLocal()
    try:
        design = db.get(Design, design_id)
        timing = (design.semantic_json or {}).get("timing")
        assert timing is not None
        assert timing["compile_ms"] >= 0
        assert timing["validate_ms"] >= 0
        assert timing["export_ms"] >= 0
    finally:
        db.close()


def test_generation_result_carries_compile_and_export_ms():
    from app.export.exporter import generate
    from app.schemas.design_spec import DesignSpec

    spec = DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 6},
    )
    result = generate(spec)
    assert result.compile_ms > 0
    assert result.export_ms > 0
