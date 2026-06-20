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

from app.cad.plan.schema import CadPlan, Expected, Feature
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
        return max(self.branch_od + (self.flange_od - self.main_od),
                   self.branch_od + 20)


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
        flange_od = main_od + 40
        assumptions.append(f"Flange OD {flange_od}mm inferred from the main pipe")
    if not branch_od:
        branch_od = round(main_od * 2 / 3, 1)
        assumptions.append(f"Branch pipe OD {branch_od}mm inferred from the main pipe")

    wall = _dim(scaled, "wall thickness", "wall")
    if not wall:
        wall = round(max(3.0, main_od * 0.07), 1)
        assumptions.append(f"{wall}mm pipe wall thickness assumed")
    flange_thk = _dim(scaled, "flange thickness")
    if not flange_thk:
        flange_thk = round(max(8.0, flange_od * 0.1), 1)
        assumptions.append(f"{flange_thk}mm flange thickness assumed")

    main_len = _dim(scaled, "main pipe length", "total height", "overall height",
                    "height") or round(flange_od * 1.25, 1)
    branch_len = _dim(scaled, "branch length", "branch pipe length") \
        or round(main_len / 2, 1)

    bolt_count, bolt_dia = 12, 10.0
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


def plan_from_spec(spec: DrawingPipeBranchSpec) -> CadPlan:
    """Spec → CadPlan with the drawing's anatomy: VERTICAL main run (Z) with
    top/bottom flanges, perpendicular branch (X) with its own flange."""
    s = spec
    pcd_branch = round(s.branch_flange_od - 2.5 * s.bolt_dia, 1)
    flange = lambda fid, axis, desc, od, bore, pcd, at: Feature(  # noqa: E731
        id=fid, kind="circular_flange", axis=axis, description=desc,
        params={"od": od, "thickness": s.flange_thk, "pcd": pcd,
                "bolt_count": s.bolt_count, "bolt_diameter": s.bolt_dia,
                "bore": bore},
        at=at,
    )
    features = [
        Feature(id="main_pipe", kind="pipe", axis="z",
                description="vertical main run pipe with a hollow main bore",
                params={"od": s.main_od, "id": s.main_bore, "length": s.main_len},
                at=[0, 0, -s.main_len / 2]),
        Feature(id="branch_pipe", kind="pipe", axis="x",
                description="perpendicular side branch pipe with a hollow branch bore",
                params={"od": s.branch_od, "id": s.branch_bore, "length": s.branch_len},
                at=[0, 0, 0]),
        flange("top_flange", "z", "top flange with repeated bolt pattern",
               s.flange_od, s.main_bore, s.pcd, [0, 0, s.main_len / 2 - s.flange_thk]),
        flange("bottom_flange", "z", "bottom flange with repeated bolt pattern",
               s.flange_od, s.main_bore, s.pcd, [0, 0, -s.main_len / 2]),
        flange("branch_flange", "x", "branch flange with repeated bolt pattern",
               s.branch_flange_od, s.branch_bore, pcd_branch,
               [s.branch_len - s.flange_thk, 0, 0]),
    ]
    return CadPlan(
        object_type="flanged_pipe_branch", name="flanged pipe branch (from drawing)",
        assumptions=list(s.assumptions), features=features, expected=Expected(),
    )


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
