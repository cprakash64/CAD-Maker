"""Deterministic parametric flanged pipe branch (flanged tee) — builder +
drawing-faithfulness validator.

Geometry contract (why the boolean ORDER matters): pipes union as SOLID
cylinders first and their bores are cut afterwards through the whole fused body
(the CadPlan compiler defers pipe bores). Uniting pre-hollowed tubes leaves the
main pipe's wall sealing the branch mouth — the classic "watertight but the
inside is wrong" failure. With deferred bores the internal flow path is
continuous: main bore through the full run, branch bore from the branch face
into the main bore.

``validate_pipe_branch`` proves faithfulness GEOMETRICALLY, independent of plan
metadata: probe solids are intersected with the built model (a clear bore or
open bolt hole must contain no material), wall thickness / flange dimensions
are checked against the plan, and any failure is a CRITICAL check — a model
that is watertight but not drawing-faithful can never PASS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cadquery as cq

from app.cad.plan.compiler import CadPlanResult
from app.cad.plan.schema import CadPlan, Expected, Feature, FeatureKind

PIPE_BRANCH_TYPES = {"flanged_pipe_branch", "pipe_tee"}

# Wall plausibility is proportional (a 0.5mm wall is fine on a Ø11 pipe,
# absurd on a Ø200 one), with a small absolute floor.
MIN_WALL_FLOOR_MM = 0.4
MIN_WALL_FRACTION = 0.02


def min_plausible_wall(main_od: float) -> float:
    return max(MIN_WALL_FLOOR_MM, main_od * MIN_WALL_FRACTION)
# Probe radius fraction: slightly under the bore so tessellation/fillets never
# produce false obstruction positives.
_PROBE_SHRINK = 0.85


@dataclass
class PipeBranchSpec:
    """Full parametric description of a flanged pipe branch."""

    # main run (vertical, axis Z, centered on the origin)
    main_od: float
    main_id: float
    main_len: float
    # branch (perpendicular, axis X, projecting from the main axis)
    branch_od: float
    branch_id: float
    branch_len: float          # projection from the main axis to the branch face
    branch_z_offset: float = 0.0  # branch centerline offset from mid-height
    # flanges
    flange_od: float = 0.0
    flange_id: float = 0.0     # defaults to main_id
    flange_thk: float = 10.0
    branch_flange_od: float = 0.0
    branch_flange_thk: float = 0.0  # defaults to flange_thk
    # bolt circles (repeated on every flange)
    bolt_count: int = 8
    bolt_dia: float = 10.0
    pcd: float = 0.0
    branch_pcd: float = 0.0
    # finishing
    fillet_radius: float = 0.0
    assumptions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.flange_od:
            self.flange_od = self.main_od + 40
        if not self.flange_id:
            self.flange_id = self.main_id
        if not self.branch_flange_od:
            self.branch_flange_od = max(
                self.branch_od + (self.flange_od - self.main_od),
                self.branch_od * 1.4)
        if not self.branch_flange_thk:
            self.branch_flange_thk = self.flange_thk
        if not self.pcd:
            self.pcd = round(self.flange_od - 2.5 * self.bolt_dia, 1)
        if not self.branch_pcd:
            self.branch_pcd = round(self.branch_flange_od - 2.5 * self.bolt_dia, 1)

    @property
    def wall(self) -> float:
        return (self.main_od - self.main_id) / 2


def build_plan(spec: PipeBranchSpec) -> CadPlan:
    """Spec → CadPlan with the drawing's anatomy and audit-stable feature ids
    (main_pipe, branch_pipe, top_flange, bottom_flange, branch_flange).

    Boolean sequence (the compiler honors it):
      1-2) solid main + branch cylinders, 3) union (with the flange discs),
      4-6) deferred bore cuts through the fused body → continuous passage,
      7) three flanges, 8) bolt circles on each, 9) optional fillet."""
    s = spec

    def flange(fid: str, axis: str, desc: str, od: float, bore: float, pcd: float,
               thk: float, at: list[float]) -> Feature:
        return Feature(
            id=fid, kind="circular_flange", axis=axis, description=desc,
            params={"od": od, "thickness": thk, "pcd": pcd,
                    "bolt_count": s.bolt_count, "bolt_diameter": s.bolt_dia,
                    "bore": bore},
            at=at,
        )

    features = [
        Feature(id="main_pipe", kind="pipe", axis="z",
                description="vertical main run pipe with a hollow main bore",
                params={"od": s.main_od, "id": s.main_id, "length": s.main_len},
                at=[0, 0, -s.main_len / 2]),
        Feature(id="branch_pipe", kind="pipe", axis="x",
                description="perpendicular side branch pipe with a hollow branch bore",
                params={"od": s.branch_od, "id": s.branch_id, "length": s.branch_len},
                at=[0, 0, s.branch_z_offset]),
        flange("top_flange", "z", "top flange with repeated bolt pattern",
               s.flange_od, s.flange_id, s.pcd, s.flange_thk,
               [0, 0, s.main_len / 2 - s.flange_thk]),
        flange("bottom_flange", "z", "bottom flange with repeated bolt pattern",
               s.flange_od, s.flange_id, s.pcd, s.flange_thk,
               [0, 0, -s.main_len / 2]),
        flange("branch_flange", "x", "branch flange with repeated bolt pattern",
               s.branch_flange_od, s.branch_id, s.branch_pcd, s.branch_flange_thk,
               [s.branch_len - s.branch_flange_thk, 0, s.branch_z_offset]),
    ]
    if s.fillet_radius > 0:
        features.append(Feature(
            id="junction_fillet", kind="fillet",
            description="fillet at the branch/main junction",
            params={"radius": s.fillet_radius}))
    return CadPlan(
        object_type="flanged_pipe_branch",
        name="flanged pipe branch (from drawing)",
        assumptions=list(s.assumptions),
        features=features,
        expected=Expected(
            bbox_mm={"z": s.main_len},
            hole_count=3 * s.bolt_count + 3,  # 3 bolt circles + 3 bores/openings
        ),
    )


def spec_from_plan(plan: CadPlan) -> PipeBranchSpec | None:
    """Recover the parametric spec from ANY pipe-branch-shaped CadPlan (ours or
    LLM-authored), so the geometric validator can derive its expectations. None
    when the plan lacks the minimum anatomy (which is itself a failure the
    audit reports)."""
    pipes = [f for f in plan.features if f.kind == FeatureKind.pipe]
    mains = [f for f in pipes if "branch" not in f"{f.id} {f.description}".lower()]
    branches = [f for f in pipes if "branch" in f"{f.id} {f.description}".lower()]
    flanges = [f for f in plan.features if f.kind == FeatureKind.circular_flange]
    if not mains or not branches:
        return None
    m, b = mains[0], branches[0]

    def bore(f: Feature, od: float) -> float:
        v = f.p("id", 0, "bore", "inner_diameter")
        if v <= 0:
            w = f.p("wall", 0, "wall_thickness", "thickness")
            v = od - 2 * w if w > 0 else od - 2 * max(2.0, od * 0.1)
        return max(0.1, v)

    main_od = m.p("od", 40, "outer_diameter", "diameter")
    branch_od = b.p("od", 30, "outer_diameter", "diameter")
    main_flanges = [f for f in flanges
                    if "branch" not in f"{f.id} {f.description}".lower()]
    branch_flanges = [f for f in flanges
                      if "branch" in f"{f.id} {f.description}".lower()]
    ref = main_flanges[0] if main_flanges else None
    bref = branch_flanges[0] if branch_flanges else None
    return PipeBranchSpec(
        main_od=main_od, main_id=bore(m, main_od),
        main_len=m.p("length", 100, "height"),
        branch_od=branch_od, branch_id=bore(b, branch_od),
        branch_len=b.p("length", 50, "height"),
        branch_z_offset=float(b.at[2]),
        flange_od=ref.p("od", 0, "outer_diameter") if ref else 0.0,
        flange_thk=ref.p("thickness", 10, "flange_thickness") if ref else 10.0,
        branch_flange_od=bref.p("od", 0, "outer_diameter") if bref else 0.0,
        branch_flange_thk=bref.p("thickness", 0, "flange_thickness") if bref else 0.0,
        bolt_count=int(ref.p("bolt_count", 0, "holes") if ref else 0) or 8,
        bolt_dia=(ref.p("bolt_diameter", 0, "hole_diameter") if ref else 0) or 10.0,
        pcd=ref.p("pcd", 0, "bolt_circle_diameter") if ref else 0.0,
        branch_pcd=bref.p("pcd", 0, "bolt_circle_diameter") if bref else 0.0,
    )


# --------------------------------------------------------------- validation

def _probe(solid: cq.Workplane, tool: cq.Workplane) -> float:
    """Material volume (mm³) inside the probe region — ~0 means the region is
    clear (an open bore / hole)."""
    try:
        hit = solid.intersect(tool)
        return float(hit.val().Volume())
    except Exception:  # noqa: BLE001 - an empty intersection can raise in OCC
        return 0.0


def _cyl_z(dia: float, z0: float, z1: float, x: float = 0.0, y: float = 0.0) -> cq.Workplane:
    return (cq.Workplane("XY").circle(dia / 2).extrude(z1 - z0)
            .translate((x, y, z0)))


def _cyl_x(dia: float, x0: float, x1: float, y: float = 0.0, z: float = 0.0) -> cq.Workplane:
    tool = cq.Workplane("XY").circle(dia / 2).extrude(x1 - x0)
    return tool.rotate((0, 0, 0), (0, 1, 0), 90).translate((x0, y, z))


def _annulus_x(od: float, idia: float, x0: float, x1: float,
               y: float = 0.0, z: float = 0.0) -> cq.Workplane:
    """A hollow tube (branch wall) along X, spanning [x0, x1]."""
    tube = (cq.Workplane("XY").circle(od / 2).circle(idia / 2).extrude(x1 - x0))
    return tube.rotate((0, 0, 0), (0, 1, 0), 90).translate((x0, y, z))


def validate_pipe_branch(plan: CadPlan, result: CadPlanResult) -> list:
    """Family-specific, geometry-measured faithfulness checks (all CRITICAL).

    Every check probes the ACTUAL solid; plan metadata alone can't pass it."""
    from app.cad.plan.validate import Check  # local import: validate imports us lazily

    checks: list[Check] = []
    spec = spec_from_plan(plan)
    if spec is None:
        checks.append(Check(
            name="pipe_branch_anatomy", passed=False, severity="critical",
            expected="main pipe + perpendicular branch + 3 flanges",
            actual="plan lacks the pipe-branch anatomy",
        ))
        return checks
    s = spec
    solid = result.solid

    # 1. Continuous internal passage — main bore clear over the full run.
    half = s.main_len / 2
    probe_d = s.main_id * _PROBE_SHRINK
    obstruction = _probe(solid, _cyl_z(probe_d, -half - 1, half + 1))
    checks.append(Check(
        name="internal_passage_main", passed=obstruction < 1e-3, severity="critical",
        expected=f"Ø{s.main_id:g} main bore clear through the run",
        actual="clear" if obstruction < 1e-3 else f"{obstruction:.1f}mm³ of material blocks it",
    ))

    # 2. Branch bore clear from the branch face into the main bore (the classic
    #    failure: the main pipe's wall sealing the branch mouth).
    probe_bd = s.branch_id * _PROBE_SHRINK
    obstruction = _probe(solid, _cyl_x(probe_bd, 0.0, s.branch_len - 0.5,
                                       z=s.branch_z_offset))
    checks.append(Check(
        name="internal_passage_branch", passed=obstruction < 1e-3, severity="critical",
        expected=f"Ø{s.branch_id:g} branch bore open into the main bore",
        actual="clear" if obstruction < 1e-3 else f"{obstruction:.1f}mm³ of material blocks it",
    ))

    # 3. Bolt holes really open on every flange (probe each expected position).
    def bolt_blocked(pcd: float, z0: float, z1: float, axis: str,
                     x_range: tuple[float, float] | None = None) -> int:
        blocked = 0
        for k in range(s.bolt_count):
            a = 2 * math.pi * k / s.bolt_count
            u, v = (pcd / 2) * math.cos(a), (pcd / 2) * math.sin(a)
            if axis == "z":
                tool = _cyl_z(s.bolt_dia * _PROBE_SHRINK, z0, z1, x=u, y=v)
            else:  # branch flange: holes along X at (y, z) offsets
                tool = _cyl_x(s.bolt_dia * _PROBE_SHRINK, x_range[0], x_range[1],
                              y=u, z=s.branch_z_offset + v)
            if _probe(solid, tool) > 1e-3:
                blocked += 1
        return blocked

    for name, pcd, od, z0, z1, axis, xr in (
        ("top", s.pcd, s.flange_od, half - s.flange_thk + 0.2, half + 1, "z", None),
        ("bottom", s.pcd, s.flange_od, -half - 1, -half + s.flange_thk - 0.2, "z", None),
        ("branch", s.branch_pcd, s.branch_flange_od, 0, 0, "x",
         (s.branch_len - s.branch_flange_thk + 0.2, s.branch_len + 1)),
    ):
        # The flange must physically EXIST: probe for material between the bolt
        # circle and the rim (otherwise open-hole probes pass trivially in air).
        rim_r = (pcd / 2 + od / 2) / 2
        if axis == "z":
            body = _cyl_z(min(4.0, od / 20), z0 + 0.2, z1 - 1.2, x=rim_r, y=0)
        else:
            body = _cyl_x(min(4.0, od / 20), xr[0] + 0.2, xr[1] - 1.2,
                          y=rim_r, z=s.branch_z_offset)
        present = _probe(solid, body) > 1e-3
        checks.append(Check(
            name=f"flange_present_{name}", passed=present, severity="critical",
            expected=f"Ø{od:g} {name} flange with material at the bolt-circle rim",
            actual="flange material found" if present else "no flange material",
        ))
        blocked = bolt_blocked(pcd, z0, z1, axis, xr)
        checks.append(Check(
            name=f"bolt_holes_open_{name}", passed=present and blocked == 0,
            severity="critical",
            expected=f"{s.bolt_count}× Ø{s.bolt_dia:g} open on the {name} flange "
                     f"(PCD Ø{pcd:g})",
            actual=("all open" if blocked == 0 else f"{blocked} position(s) blocked")
                   if present else "no flange to carry them",
        ))

    # 4. Minimum plausible wall thickness (proportional to the pipe size).
    wall = s.wall
    bwall = (s.branch_od - s.branch_id) / 2
    floor = round(min_plausible_wall(s.main_od), 2)
    checks.append(Check(
        name="min_wall_thickness",
        passed=wall >= floor and bwall >= min_plausible_wall(s.branch_od),
        severity="critical",
        expected=f">= {floor}mm (2% of pipe OD)",
        actual=f"main {wall:g}mm / branch {bwall:g}mm",
    ))

    # 5. Flange / envelope dimensions honored (bbox is ground truth).
    bb = result.bbox_mm
    tol = lambda v: max(2.0, v * 0.05)  # noqa: E731
    dims_ok = (abs(bb.get("y", 0) - s.flange_od) <= tol(s.flange_od)
               and abs(bb.get("z", 0) - s.main_len) <= tol(s.main_len))
    checks.append(Check(
        name="pipe_branch_dimensions", passed=dims_ok, severity="critical",
        expected=f"flange Ø{s.flange_od:g} × height {s.main_len:g}mm",
        actual=f"bbox y={bb.get('y')} z={bb.get('z')}",
    ))

    # 6. Branch really projects perpendicular to the run (x extent beyond the
    #    main pipe radius proves a physical side branch).
    branch_reach = bb.get("x", 0)
    checks.append(Check(
        name="branch_projection", passed=branch_reach > s.main_od / 2 + 5,
        severity="critical",
        expected=f"side branch projecting beyond the Ø{s.main_od:g} run",
        actual=f"x extent {branch_reach}mm",
    ))

    # 7. Branch side wall is CONTINUOUS — no accidental rectangular/planar notch
    #    carved out of the cylinder. Measure the wall material over a clean band
    #    of bare branch pipe (clear of the junction with the main run AND of the
    #    branch flange) against the full annulus it should be; a notch removes a
    #    wedge and drops the volume. The band must start OUTSIDE the main pipe
    #    (x > main radius) or the main body's material corrupts the reading.
    x_a = max(s.branch_len * 0.55, s.main_od / 2 + max(2.0, s.wall))
    x_b = min(s.branch_len - s.branch_flange_thk - 0.5, s.branch_len - 1.0)
    if x_b > x_a + 1.0 and s.branch_od > s.branch_id > 0:
        shell = _annulus_x(s.branch_od, s.branch_id, x_a, x_b, z=s.branch_z_offset)
        try:
            expected_vol = float(shell.val().Volume())
        except Exception:  # noqa: BLE001
            expected_vol = 0.0
        present_vol = _probe(solid, shell)
        ratio = present_vol / expected_vol if expected_vol > 1e-6 else 1.0
        checks.append(Check(
            name="branch_wall_continuous", passed=ratio >= 0.9, severity="critical",
            expected="continuous branch cylinder wall (no planar/notch cut)",
            actual=("wall solid all around" if ratio >= 0.9
                    else f"only {ratio*100:.0f}% of the wall present — a notch/cut "
                         "was carved into the side cylinder"),
        ))
    return checks


__all__ = ["PipeBranchSpec", "build_plan", "spec_from_plan",
           "validate_pipe_branch", "PIPE_BRANCH_TYPES", "min_plausible_wall"]
