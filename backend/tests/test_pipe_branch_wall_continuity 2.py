"""Flanged-pipe-branch side-wall continuity (spec Part 5, 7).

The field failure: the branch (side) cylinder came out with an accidental
rectangular/notched cut in its wall (a mis-read section/centre line). The
deterministic builder never carves such a notch, and ``validate_pipe_branch``
now PROVES the wall is continuous — so any future regression that does carve
one is caught as a CRITICAL failure, not shipped as "watertight".
"""
from __future__ import annotations

import cadquery as cq

from app.cad.pipe_branch import PipeBranchSpec, build_plan, validate_pipe_branch
from app.cad.plan.compiler import compile_cad_plan


def _spec() -> PipeBranchSpec:
    return PipeBranchSpec(
        main_od=40, main_id=30, main_len=120,
        branch_od=28, branch_id=20, branch_len=55,
        flange_od=80, flange_thk=10, bolt_count=8, bolt_dia=8)


def _check(checks, name):
    return next(c for c in checks if c.name == name)


def test_clean_branch_wall_is_continuous():
    plan = build_plan(_spec())
    result = compile_cad_plan(plan)
    checks = validate_pipe_branch(plan, result)
    wall = _check(checks, "branch_wall_continuous")
    assert wall.passed, wall.actual


def test_notched_branch_wall_fails_validation():
    """Carve a rectangular notch out of the branch cylinder wall — the continuity
    check must flag it CRITICAL (the model is watertight but drawing-unfaithful)."""
    spec = _spec()
    plan = build_plan(spec)
    result = compile_cad_plan(plan)
    # Rectangular notch slicing the upper half of the branch wall near the face.
    x_mid = spec.branch_len * 0.72
    notch = (cq.Workplane("XY")
             .box(14, 2 * spec.branch_od, 2 * spec.branch_od)
             .translate((x_mid, spec.branch_od, spec.branch_z_offset)))
    result.solid = result.solid.cut(notch)
    checks = validate_pipe_branch(plan, result)
    wall = _check(checks, "branch_wall_continuous")
    assert not wall.passed
    assert wall.severity == "critical"
    assert "notch" in wall.actual or "wall" in wall.actual
