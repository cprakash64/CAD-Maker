"""Deterministic fallback builders for Drawing-to-CAD.

When the (LLM) planner produces a model that FAILS the required-feature audit,
drawing mode falls back to a deterministic builder constructed from the
STRUCTURED drawing data (the scaled interpretation), not from a lossy text
prompt. The flanged pipe branch / tee gets a dedicated spec-driven builder with
the exact anatomy of the drawing:

    main_axis = Z (vertical run), branch_axis = X (perpendicular side branch),
    top_flange at +Z, bottom_flange at -Z, branch_flange at +X,
    hollow main + branch bores, a repeated bolt pattern on every flange.

Feature ids are the audit's stable ids: main_pipe, branch_pipe, top_flange,
bottom_flange, branch_flange (bores live as the pipes' hollow ``id`` params and
bolt patterns as each flange's ``bolt_count``/``bolt_diameter``).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.cad.plan.schema import CadPlan
from app.drawing.scale import ScaledDrawing, infer_scale
from app.schemas.drawing_spec import DrawingInterpretationSpec

PIPE_BRANCH_TYPES = {"flanged_pipe_branch", "pipe_tee"}


@dataclass
class DrawingPipeBranchSpec:
    """Structured, mm-scaled description of a flanged pipe branch drawing."""

    main_od: float
    main_len: float
    branch_od: float
    branch_len: float
    wall: float
    flange_od: float
    flange_thk: float
    bolt_count: int
    bolt_dia: float
    pcd: float
    assumptions: list[str]

    @property
    def main_bore(self) -> float:
        return max(1.0, self.main_od - 2 * self.wall)

    @property
    def branch_bore(self) -> float:
        return max(1.0, self.branch_od - 2 * self.wall)

    @property
    def branch_flange_od(self) -> float:
        # Proportional margin so literal small-unit drawings stay coherent
        # (an absolute +20mm dwarfs a 15mm fitting's branch).
        return max(self.branch_od + (self.flange_od - self.main_od),
                   self.branch_od * 1.4)


def _dim(scaled: ScaledDrawing, *needles: str) -> float | None:
    """First scaled dimension whose key contains every word of a needle."""
    for needle in needles:
        words = needle.split()
        for k, v in scaled.dimensions.items():
            key = k.lower().replace("_", " ")
            if v > 0 and all(w in key for w in words):
                return float(v)
    return None


def spec_from_interpretation(
    interp: DrawingInterpretationSpec, scaled: ScaledDrawing | None = None
) -> DrawingPipeBranchSpec:
    """Map the structured drawing data to a pipe-branch spec, filling gaps with
    PROPORTIONS of what is known and recording every inference."""
    scaled = scaled or infer_scale(interp)
    assumptions = list(scaled.assumptions)

    flange_od = _dim(scaled, "flange outer diameter", "flange diameter", "flange od")
    main_od = _dim(scaled, "main pipe outer diameter", "main outer diameter", "main pipe")
    branch_od = _dim(scaled, "branch pipe outer diameter", "branch outer diameter",
                     "branch pipe")
    if not main_od:
        main_od = round(flange_od * 0.6, 1) if flange_od else 75.0
        assumptions.append(f"Main pipe OD {main_od}mm inferred"
                           + (" from the flange OD" if flange_od else " (default)"))
    if not flange_od:
        flange_od = round(main_od + min(40.0, main_od * 0.55), 2)
        assumptions.append(f"Flange OD {flange_od}mm inferred from the main pipe")
    if not branch_od:
        branch_od = round(main_od * 2 / 3, 1)
        assumptions.append(f"Branch pipe OD {branch_od}mm inferred from the main pipe")

    wall = _dim(scaled, "wall thickness", "wall")
    if not wall:
        # Proportional, with a floor small enough for literal small-unit
        # drawings (a 15mm fitting must not get a 3mm wall).
        wall = round(max(0.5, main_od * 0.07), 2)
        assumptions.append(f"{wall}mm pipe wall thickness assumed")
    flange_thk = _dim(scaled, "flange thickness")
    if not flange_thk:
        flange_thk = round(max(1.0, flange_od * 0.1), 2)
        assumptions.append(f"{flange_thk}mm flange thickness assumed")

    main_len = _dim(scaled, "main pipe length", "total height", "overall height",
                    "height") or round(flange_od * 1.25, 1)
    branch_len = _dim(scaled, "branch length", "branch pipe length") \
        or round(main_len / 2, 1)

    bolt_count, bolt_dia = 12, max(1.0, round(flange_od * 0.08, 1))
    if scaled.holes:
        bolt_count = scaled.holes[0].count
        bolt_dia = scaled.holes[0].diameter
    else:
        n = _dim(scaled, "bolt count", "hole count")
        if n:
            bolt_count = int(n)
        assumptions.append(f"{bolt_count}× Ø{bolt_dia:g}mm bolt holes per flange assumed")

    pcd = _dim(scaled, "bolt circle diameter", "pcd") or round(flange_od - 2.5 * bolt_dia, 1)
    assumptions.append(
        f"Bolt circles: {bolt_count}× Ø{bolt_dia:g}mm on Ø{pcd:g}mm PCD per flange "
        f"({3 * bolt_count} flange holes total)")
    return DrawingPipeBranchSpec(
        main_od=main_od, main_len=main_len, branch_od=branch_od,
        branch_len=branch_len, wall=wall, flange_od=flange_od,
        flange_thk=flange_thk, bolt_count=bolt_count, bolt_dia=bolt_dia,
        pcd=pcd, assumptions=assumptions,
    )


def to_pipe_branch_spec(spec: DrawingPipeBranchSpec):
    """Bridge the drawing-scaled spec to the canonical parametric builder spec."""
    from app.cad.pipe_branch import PipeBranchSpec

    return PipeBranchSpec(
        main_od=spec.main_od, main_id=spec.main_bore, main_len=spec.main_len,
        branch_od=spec.branch_od, branch_id=spec.branch_bore,
        branch_len=spec.branch_len,
        flange_od=spec.flange_od, flange_thk=spec.flange_thk,
        branch_flange_od=spec.branch_flange_od,
        bolt_count=spec.bolt_count, bolt_dia=spec.bolt_dia, pcd=spec.pcd,
        assumptions=list(spec.assumptions),
    )


def plan_from_spec(spec: DrawingPipeBranchSpec) -> CadPlan:
    """Spec → CadPlan with the drawing's anatomy: VERTICAL main run (Z) with
    top/bottom flanges, perpendicular branch (X) with its own flange. Built by
    the canonical parametric builder (app.cad.pipe_branch) — solid union first,
    bores cut after, so the internal flow path is continuous."""
    from app.cad.pipe_branch import build_plan

    return build_plan(to_pipe_branch_spec(spec))


def plan_pipe_spool_from_interp(
    interp: DrawingInterpretationSpec, scaled: ScaledDrawing | None = None,
) -> CadPlan:
    """Structured straight flanged pipe spool (a pipe with a circular flange on
    EACH end) built directly from the drawing's scaled dimensions — no brittle
    prose parsing. Fills gaps with PROPORTIONS of what is known. This is what a
    ``flanged_pipe_spool`` detection builds: circular flanges, a continuous
    central bore, a per-flange bolt circle — never a wheel/rim/spokes."""
    from app.cad.plan.schema import Feature

    scaled = scaled or infer_scale(interp)
    assumptions = list(scaled.assumptions)

    flange_od = _dim(scaled, "flange outer diameter", "flange diameter",
                     "flange od", "flange rim diameter", "rim diameter")
    od = _dim(scaled, "main pipe outer diameter", "pipe outer diameter",
              "outer diameter", "main pipe", "pipe od", "od")
    bore = _dim(scaled, "bore", "inner diameter", "pipe id", "main pipe bore", "id")
    length = _dim(scaled, "main pipe length", "pipe length", "length",
                  "overall height", "total height", "height")
    wall = _dim(scaled, "wall thickness", "wall")
    flange_thk = _dim(scaled, "flange thickness")

    if not od:
        od = round(flange_od * 0.6, 1) if flange_od else 75.0
        assumptions.append(f"Main pipe OD {od}mm inferred"
                           + (" from the flange OD" if flange_od else " (default)"))
    if not flange_od:
        flange_od = round(od + min(40.0, od * 0.55), 1)
        assumptions.append(f"Flange OD {flange_od}mm inferred from the pipe OD")
    if not bore:
        bore = round(od - 2 * wall, 1) if wall else round(max(1.0, od * 0.7), 1)
        assumptions.append(f"Bore Ø{bore}mm inferred from the pipe OD")
    bore = min(bore, round(od - 1.0, 1))  # bore must stay inside the wall
    if not length:
        length = round(flange_od * 1.5, 1)
        assumptions.append(f"Spool length {length}mm inferred (no legible length)")
    if not flange_thk:
        flange_thk = round(max(1.0, flange_od * 0.1), 1)
        assumptions.append(f"{flange_thk}mm flange thickness assumed")

    bolt_count, bolt_dia = 8, max(1.0, round(flange_od * 0.08, 1))
    if scaled.holes:
        bolt_count = scaled.holes[0].count
        bolt_dia = scaled.holes[0].diameter
    pcd = _dim(scaled, "bolt circle diameter", "pcd") \
        or round(flange_od - 2.5 * bolt_dia, 1)
    total = bolt_count * 2
    assumptions.append(
        f"Straight flanged spool: {bolt_count}× Ø{bolt_dia:g}mm bolts per flange "
        f"on Ø{pcd:g}mm PCD ({total} total)")

    feature = Feature(
        id="pipe_body", kind="pipe_spool",
        description="straight pipe with a circular flange on each end",
        params={"length": length, "od": od, "id": bore, "flange_od": flange_od,
                "flange_thickness": flange_thk, "bolt_count": bolt_count,
                "bolt_diameter": bolt_dia, "pcd": pcd})
    from app.cad.plan.schema import Expected

    return CadPlan(
        object_type="pipe_spool", name=interp.title or "flanged pipe spool",
        assumptions=assumptions, features=[feature],
        expected=Expected(bbox_mm={"x": flange_od, "y": flange_od, "z": length},
                          hole_count=total, through_hole_count=total))


def drawing_fallback_plan(
    interp: DrawingInterpretationSpec, scaled: ScaledDrawing | None = None,
    prompt: str | None = None,
) -> CadPlan | None:
    """Deterministic plan for a drawing whose (LLM) plan failed the audit.

    Pipe branches/tees build from the STRUCTURED spec; every other recognized
    type falls back to the offline deterministic planner on the synthesized
    full-geometry prompt (spool, blind flange, brackets, bearing block,
    enclosure, …). Returns None when no deterministic builder exists."""
    scaled = scaled or infer_scale(interp)
    if interp.suggested_object_type in PIPE_BRANCH_TYPES:
        return plan_from_spec(spec_from_interpretation(interp, scaled))
    if prompt:
        from app.cad.plan import deterministic

        return deterministic.plan(prompt.lower())
    return None
