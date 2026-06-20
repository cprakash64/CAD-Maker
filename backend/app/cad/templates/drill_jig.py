"""Drill jig plate: a guide plate with precisely positioned guide holes.

Demo-ready details: an optional downstand registration lip to hook over a
workpiece edge, chamfered hole lead-ins so a bit self-centers, and rounded
corners. Explicit holes (with type/counterbore/countersink) are honored; absent
those, a clean grid of chamfered guide holes is generated.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, DimensionSpec
from app.cad.helpers import apply_holes, safe_fillet
from app.schemas.design_spec import DesignSpec


class DrillJigTemplate(BaseTemplate):
    object_type = "drill_jig"
    name = "Drill Jig Plate"
    description = "Guide plate with a registration lip and chamfered drill-guide holes."
    dimensions = [
        DimensionSpec("length", "Length (X)", 100.0, 10.0, 1000.0),
        DimensionSpec("width", "Width (Y)", 60.0, 10.0, 1000.0),
        DimensionSpec("thickness", "Thickness (Z)", 6.0, 1.0, 100.0),
        DimensionSpec("hole_diameter", "Guide hole diameter", 5.0, 0.0, 100.0),
        DimensionSpec("hole_spacing", "Grid spacing", 20.0, 2.0, 500.0),
        DimensionSpec("lip_height", "Registration lip height (0 = none)", 8.0, 0.0, 300.0),
        DimensionSpec("corner_radius", "Corner radius", 3.0, 0.0, 200.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        length, width, t = r["length"], r["width"], r["thickness"]
        corner = r["corner_radius"]

        part = cq.Workplane("XY").box(length, width, t)
        if corner > 0:
            part = safe_fillet(part, min(corner, length / 2 - 0.5, width / 2 - 0.5), "|Z")

        # Downstand registration lip on the -Y edge (hooks over the work edge).
        lip = r["lip_height"]
        if lip > 0:
            lip_block = (
                cq.Workplane("XY")
                .box(length, t, lip, centered=(True, True, False))
                .translate((0, -width / 2 + t / 2, -t / 2 - lip))
            )
            part = part.union(lip_block)

        if spec.holes:
            return apply_holes(part, r)

        hd, spacing = r["hole_diameter"], r["hole_spacing"]
        if hd <= 0:
            return part
        wp = part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
        margin = max(hd, 6.0)
        nx = max(1, int((length - 2 * margin) // spacing) + 1)
        ny = max(1, int((width - 2 * margin) // spacing) + 1)
        x0 = -(nx - 1) * spacing / 2.0
        y0 = -(ny - 1) * spacing / 2.0
        points = [
            (x0 + i * spacing, y0 + j * spacing)
            for i in range(nx)
            for j in range(ny)
        ]
        # Chamfered lead-in so the drill bit self-centers in the guide.
        return wp.pushPoints(points).cskHole(hd, hd + 1.6, 90)
