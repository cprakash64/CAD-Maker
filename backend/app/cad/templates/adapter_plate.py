"""Adapter plate: bridges two bolt patterns with a finished, manufacturable plate.

Demo-ready details: rounded corners, a broken top edge, a chamfered center bore,
and mounting holes that honor clearance / counterbore / countersink types.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.cad.helpers import apply_edge_treatment, apply_holes, safe_fillet
from app.schemas.design_spec import DesignSpec


class AdapterPlateTemplate(BaseTemplate):
    object_type = "adapter_plate"
    name = "Adapter Plate"
    description = "Flat plate bridging two bolt patterns, with an optional center bore."
    dimensions = [
        DimensionSpec("width", "Width (X)", 100.0, 10.0, 1000.0),
        DimensionSpec("depth", "Depth (Y)", 100.0, 10.0, 1000.0),
        DimensionSpec("thickness", "Thickness (Z)", 6.0, 1.0, 200.0),
        DimensionSpec("center_bore", "Center bore diameter (0 = none)", 0.0, 0.0, 900.0),
        DimensionSpec("corner_radius", "Corner radius", 5.0, 0.0, 300.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        width, depth, t = r["width"], r["depth"], r["thickness"]
        corner = r["corner_radius"]

        part = cq.Workplane("XY").box(width, depth, t)
        if corner > 0:
            part = safe_fillet(part, min(corner, width / 2 - 0.5, depth / 2 - 0.5), "|Z")
        part = apply_edge_treatment(part, spec, selector=">Z")

        bore = r["center_bore"]
        if bore > 0:
            if bore >= min(width, depth):
                raise CadGenerationError(
                    f"center_bore ({bore}mm) does not fit within the plate"
                )
            # Chamfer the bore entry for a finished, deburred look.
            csk = min(bore + 2.0, min(width, depth) - 0.5)
            part = (
                part.faces(">Z")
                .workplane(centerOption="CenterOfBoundBox")
                .cskHole(bore, csk, 90)
            )

        part = apply_holes(part, r)
        return part
