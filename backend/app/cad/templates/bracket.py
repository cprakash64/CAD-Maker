"""Rectangular mounting bracket: a finished flat plate with mounting holes.

Demo-ready details: rounded plan-view corners, a broken (filleted/chamfered)
top edge, optional clearance / counterbore / countersink holes, and an optional
strengthening gusset rib for wall mounting.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, DimensionSpec
from app.cad.helpers import apply_edge_treatment, apply_holes, safe_fillet
from app.schemas.design_spec import DesignSpec


class RectangularBracketTemplate(BaseTemplate):
    object_type = "rectangular_bracket"
    name = "Rectangular Mounting Bracket"
    description = "Flat mounting plate with rounded corners, finished edges and holes."
    dimensions = [
        DimensionSpec("width", "Width (X)", 80.0, 5.0, 1000.0),
        DimensionSpec("depth", "Depth (Y)", 40.0, 5.0, 1000.0),
        DimensionSpec("thickness", "Thickness (Z)", 5.0, 0.5, 200.0),
        DimensionSpec("corner_radius", "Corner radius", 4.0, 0.0, 200.0),
        DimensionSpec("gusset_height", "Gusset rib height (0 = none)", 0.0, 0.0, 1000.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        width, depth, t = r["width"], r["depth"], r["thickness"]
        corner = r["corner_radius"]

        part = cq.Workplane("XY").box(width, depth, t)

        # Rounded plan-view corners (vertical edges).
        if corner > 0:
            part = safe_fillet(part, min(corner, width / 2 - 0.5, depth / 2 - 0.5), "|Z")

        # Break the top perimeter edge for a finished look (fillet or chamfer),
        # then drill the holes while the top face is still a single clean plane.
        part = apply_edge_treatment(part, spec, selector=">Z")
        part = apply_holes(part, r)

        # Optional gusset rib along the back (-Y) edge for a wall bracket. Added
        # LAST: unioning it earlier makes the top face non-coplanar and breaks
        # the ">Z" hole-drilling selector.
        gusset = r["gusset_height"]
        if gusset > 0:
            rib_t = min(t, 6.0)
            rib = (
                cq.Workplane("YZ")
                .workplane(offset=-width / 2)
                .moveTo(-depth / 2, t / 2)
                .lineTo(-depth / 2, t / 2 + gusset)
                .lineTo(-depth / 2 + gusset, t / 2)
                .close()
                .extrude(rib_t)
            )
            part = part.union(rib)
        return part
