"""Gear / pulley template.

Three shapes, chosen by parameters (a *gear* prompt never falls back to a plain
pulley):
  * hex > 0           -> hexagonal outer profile (gear blank / hex part)
  * tooth_count > 0   -> spur gear with that many rim teeth
  * otherwise         -> V/flat-belt pulley with a rim groove
Plus a center bore (shaft hole), optional keyway, and optional hub.
Defaults: 60mm OD, 12mm thick, 10mm bore, 24 teeth when a gear is requested.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class GearPulleyTemplate(BaseTemplate):
    object_type = "simple_gear_or_pulley"
    name = "Gear / Pulley"
    description = "Spur gear (teeth), hexagonal gear blank, or grooved pulley with a bore."
    dimensions = [
        DimensionSpec("outer_diameter_mm", "Outer diameter", 60.0, 8.0, 600.0),
        DimensionSpec("thickness_mm", "Thickness", 12.0, 2.0, 300.0),
        DimensionSpec("bore_diameter_mm", "Bore / shaft hole", 10.0, 0.0, 400.0),
        DimensionSpec("tooth_count", "Teeth (0 = none)", 0.0, 0.0, 200.0),
        DimensionSpec("tooth_height_mm", "Tooth height", 4.0, 0.0, 50.0),
        DimensionSpec("hex", "Hexagonal outer profile (1 = yes)", 0.0, 0.0, 1.0),
        DimensionSpec("hub_diameter_mm", "Hub diameter (0 = none)", 0.0, 0.0, 400.0),
        DimensionSpec("hub_height_mm", "Hub height", 0.0, 0.0, 200.0),
        DimensionSpec("keyway_width_mm", "Keyway width (0 = none)", 0.0, 0.0, 40.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        od = r["outer_diameter_mm"]
        thk = r["thickness_mm"]
        bore = r["bore_diameter_mm"]
        teeth = int(round(r["tooth_count"]))
        th = r["tooth_height_mm"]
        hexagonal = r["hex"] > 0.5

        if bore >= od:
            raise CadGenerationError("bore must be smaller than the outer diameter")

        if hexagonal:
            # Hexagonal outer profile (across-corners diameter = od).
            part = cq.Workplane("XY").polygon(6, od).extrude(thk)
        elif teeth > 0 and th > 0:
            root_r = od / 2.0 - th
            if root_r <= bore / 2.0:
                raise CadGenerationError("teeth too tall for this diameter/bore")
            # Build the cog as a SINGLE closed profile (one wire -> one manifold
            # prism). Unioning N separate tooth boxes onto a disc produced tangent
            # / coincident faces and a non-manifold, non-watertight mesh that
            # failed validation; an extruded gear outline is watertight by
            # construction. Tooth profile = trapezoidal castellation alternating
            # between tip radius (tooth) and root radius (gap).
            part = cq.Workplane("XY").polyline(
                _gear_profile_points(od / 2.0, root_r, teeth)
            ).close().extrude(thk)
        else:
            # Pulley: disc with a central rim groove.
            part = cq.Workplane("XY").circle(od / 2.0).extrude(thk)
            groove_r = od / 2.0 - max(2.0, th)
            if groove_r > bore / 2.0 + 1:
                groove = (
                    cq.Workplane("XY")
                    .workplane(offset=thk / 3.0)
                    .circle(od / 2.0 + 1)
                    .circle(groove_r)
                    .extrude(thk / 3.0)
                )
                part = part.cut(groove)

        # Optional hub on top.
        hub_d, hub_h = r["hub_diameter_mm"], r["hub_height_mm"]
        if hub_d > bore and hub_h > 0:
            hub = cq.Workplane("XY").workplane(offset=thk).circle(hub_d / 2.0).extrude(hub_h)
            part = part.union(hub)

        # Center bore through everything (+ optional keyway).
        if bore > 0:
            total_h = thk + (hub_h if (hub_d > bore and hub_h > 0) else 0) + 2
            cutter = cq.Workplane("XY").workplane(offset=-1).circle(bore / 2.0).extrude(total_h)
            part = part.cut(cutter)
            kw = r["keyway_width_mm"]
            if kw > 0:
                key = (
                    cq.Workplane("XY")
                    .workplane(offset=-1)
                    .moveTo(bore / 2.0, 0)
                    .rect(kw, kw)
                    .extrude(total_h)
                )
                part = part.cut(key)
        return part


def _gear_profile_points(tip_r: float, root_r: float, teeth: int,
                         tooth_fraction: float = 0.5) -> list[tuple[float, float]]:
    """Vertices of a single closed cog outline, traversed once anticlockwise.

    Each pitch contributes a tooth (at ``tip_r``) followed by a gap (at
    ``root_r``); the resulting polygon is simple (non self-intersecting) and
    extrudes to one watertight, manifold solid with ``teeth`` outer corners.
    """
    pts: list[tuple[float, float]] = []
    pitch = 2 * math.pi / teeth
    half = max(0.1, min(0.9, tooth_fraction)) * pitch
    for k in range(teeth):
        a0 = k * pitch
        # Tooth flank up to the tip, across the tip, and back down to the root.
        pts.append((root_r * math.cos(a0), root_r * math.sin(a0)))
        pts.append((tip_r * math.cos(a0), tip_r * math.sin(a0)))
        pts.append((tip_r * math.cos(a0 + half), tip_r * math.sin(a0 + half)))
        pts.append((root_r * math.cos(a0 + half), root_r * math.sin(a0 + half)))
    return pts
