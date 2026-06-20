"""Shared geometry helpers for templates.

Hole convention: a hole's (x, y) is measured in spec units from the part's
local origin. For plate-like parts the origin is the centroid of the top face,
so a hole at (0, 0) sits dead center. Coordinates are converted to mm here.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import ResolvedSpec
from app.schemas.design_spec import Hole, HoleType


def _drill_one(part: "cq.Workplane", spec, hole: Hole) -> "cq.Workplane":
    """Cut a single hole (simple / counterbore / countersink) from the +Z face."""
    x = spec.to_mm(hole.x)
    y = spec.to_mm(hole.y)
    dia = spec.to_mm(hole.diameter)
    wp = part.faces(">Z").workplane(centerOption="CenterOfBoundBox").moveTo(x, y)

    if hole.hole_type == HoleType.counterbore and hole.counterbore_diameter:
        return wp.cboreHole(
            dia,
            spec.to_mm(hole.counterbore_diameter),
            spec.to_mm(hole.counterbore_depth or hole.diameter),
        )
    if hole.hole_type == HoleType.countersink and hole.countersink_diameter:
        return wp.cskHole(
            dia,
            spec.to_mm(hole.countersink_diameter),
            hole.countersink_angle,
        )
    return wp.hole(dia)


def apply_holes(part: "cq.Workplane", resolved: ResolvedSpec) -> "cq.Workplane":
    """Drill every hole from the spec through the top (+Z) face."""
    spec = resolved.spec
    for hole in spec.holes:
        part = _drill_one(part, spec, hole)
    return part


def safe_fillet(
    part: "cq.Workplane", radius_mm: float, selector: str = "|Z"
) -> "cq.Workplane":
    """Fillet edges, swallowing kernel failures (too-large radius, etc.)."""
    if radius_mm <= 0:
        return part
    try:
        return part.edges(selector).fillet(radius_mm)
    except Exception:  # noqa: BLE001 - edge treatment is best-effort cosmetic
        return part


def safe_chamfer(
    part: "cq.Workplane", size_mm: float, selector: str = "|Z"
) -> "cq.Workplane":
    """Chamfer edges, swallowing kernel failures."""
    if size_mm <= 0:
        return part
    try:
        return part.edges(selector).chamfer(size_mm)
    except Exception:  # noqa: BLE001
        return part


def apply_edge_treatment(
    part: "cq.Workplane", spec, selector: str = "|Z"
) -> "cq.Workplane":
    """Apply the spec's fillet *or* chamfer to the selected edges (mm)."""
    if spec.fillet_radius:
        return safe_fillet(part, spec.to_mm(spec.fillet_radius), selector)
    if spec.chamfer_size:
        return safe_chamfer(part, spec.to_mm(spec.chamfer_size), selector)
    return part
