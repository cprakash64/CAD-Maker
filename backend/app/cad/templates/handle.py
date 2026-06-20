"""Simple handle / knob: a round knob with a domed top and a shaft bore."""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.cad.helpers import safe_fillet
from app.schemas.design_spec import DesignSpec


class HandleTemplate(BaseTemplate):
    object_type = "handle"
    name = "Handle / Knob"
    description = "Cylindrical knob with a rounded top edge and a bottom shaft bore."
    dimensions = [
        DimensionSpec("diameter", "Knob diameter", 30.0, 5.0, 500.0),
        DimensionSpec("height", "Knob height", 25.0, 3.0, 500.0),
        DimensionSpec("bore_diameter", "Shaft bore diameter", 8.0, 0.0, 200.0),
        DimensionSpec("bore_depth", "Shaft bore depth", 12.0, 0.0, 500.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        dia, height = r["diameter"], r["height"]
        bore, bore_depth = r["bore_diameter"], r["bore_depth"]

        part = cq.Workplane("XY").circle(dia / 2.0).extrude(height)
        # Round the top edge for a hand-friendly knob.
        fr = spec.to_mm(spec.fillet_radius) if spec.fillet_radius else min(dia * 0.15, height * 0.4)
        part = safe_fillet(part, fr, selector=">Z")

        if bore > 0 and bore_depth > 0:
            if bore >= dia:
                raise CadGenerationError(
                    f"bore_diameter ({bore}mm) must be smaller than knob diameter ({dia}mm)"
                )
            depth = min(bore_depth, height - 1.0)
            cut = (
                cq.Workplane("XY")
                .circle(bore / 2.0)
                .extrude(depth)
            )
            part = part.cut(cut)
        return part
