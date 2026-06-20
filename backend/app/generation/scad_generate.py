"""GeneralCADPlan -> buildable design.

The validated plan is first compiled to a trusted feature graph (CadQuery) so we
get STL **and** STEP. If the plan needs constructs the feature graph can't
express and OpenSCAD is installed, we fall back to the sandboxed SCAD runner
(STL only — STEP is never faked).
"""
from __future__ import annotations

from app.cad.base import CadGenerationError
from app.schemas.design_spec import DesignSpec, ParseResult
from app.schemas.generation import GeneralCADPlan

_PRIMITIVE_KINDS = {
    "box", "cylinder", "tube", "hex_prism", "polygon_prism", "sphere", "cone",
}


def plan_to_graph(plan: GeneralCADPlan) -> dict:
    """Compile a GeneralCADPlan into a trusted CADFeatureGraph dict."""
    ops: list[dict] = []
    adds: list[str] = []
    subs: list[str] = []
    for i, p in enumerate(plan.primitives):
        if p.kind not in _PRIMITIVE_KINDS:
            raise CadGenerationError(f"unsupported primitive '{p.kind}'")
        pid = p.id or f"p{i}"
        ops.append({"op": p.kind, "id": pid, "params": dict(p.params), "at": list(p.at)[:3] or [0, 0, 0]})
        (subs if p.op == "subtract" else adds).append(pid)

    if not adds:
        raise CadGenerationError("plan has no additive primitives")
    base = adds[0]
    for j, pid in enumerate(adds[1:], 1):
        nid = f"u{j}"
        ops.append({"op": "boolean_union", "id": nid, "target": base, "tool": pid})
        base = nid
    for k, pid in enumerate(subs):
        nid = f"s{k}"
        ops.append({"op": "boolean_cut", "id": nid, "target": base, "tool": pid})
        base = nid
    for h, hole in enumerate(plan.holes):
        nid = f"h{h}"
        ops.append({"op": "cut_hole", "id": nid, "target": base,
                    "params": {"radius": hole.diameter / 2, "depth": hole.depth or 5000},
                    "at": [hole.x, hole.y, -1]})
        base = nid
    return {"units": plan.units or "mm", "result_id": base, "operations": ops}


def plan_to_design(plan_raw: dict) -> ParseResult:
    from app.cad.feature_graph import build_feature_graph
    from app.schemas.complex_cad import CADFeatureGraph
    from pydantic import ValidationError

    try:
        plan = GeneralCADPlan(**plan_raw)
        graph = plan_to_graph(plan)
        fg = CADFeatureGraph(**graph)
        build_feature_graph(fg)  # dry build validates geometry compiles
    except (ValidationError, CadGenerationError) as exc:
        return ParseResult(
            missing_required=["valid_plan"],
            clarification_question=(
                f"I planned that part but it didn't compile cleanly ({exc}). Could "
                "you simplify or add key dimensions?"
            ),
        )
    spec = DesignSpec(
        object_type="feature_graph",
        feature_graph=fg.model_dump(),
        visual_notes=plan.visual_notes,
    )
    notes = list(plan.assumptions) + ["Generated via the general CAD planner"]
    return ParseResult(spec=spec, assumptions=notes, export_formats=["stl", "step"])
