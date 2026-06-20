"""Cylindrical spacer / standoff: a tube with a central through-bore."""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class SpacerTemplate(BaseTemplate):
    object_type = "spacer"
    name = "Cylindrical Spacer / Standoff"
    description = "Round standoff with a concentric through-hole."
    dimensions = [
        DimensionSpec("outer_diameter", "Outer diameter", 12.0, 2.0, 500.0),
        DimensionSpec("length", "Length / height", 20.0, 1.0, 1000.0),
        DimensionSpec("bore_diameter", "Bore (through-hole) diameter", 6.4, 0.0, 500.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        od, length, bore = r["outer_diameter"], r["length"], r["bore_diameter"]
        if bore >= od:
            raise CadGenerationError(
                f"bore_diameter ({bore}mm) must be smaller than outer_diameter ({od}mm)"
            )
        part = cq.Workplane("XY").circle(od / 2.0).extrude(length)
        if bore > 0:
            part = part.faces(">Z").workplane().hole(bore)
        return part
