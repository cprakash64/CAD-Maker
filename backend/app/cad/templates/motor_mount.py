"""NEMA stepper-motor mount plate.

A flat mounting plate carrying the NEMA face pattern: a central pilot/shaft
clearance bore and four mounting holes on the square bolt-spacing pattern, with
chamfered/rounded corners. Dimensions come from the curated NEMA standard
(``object_intelligence.standards``) so the four-hole pattern and pilot are exact.

Spec dimensions: ``nema_size``, ``bolt_spacing``, ``bolt_hole``, ``pilot_diameter``,
``plate_size`` (square), ``thickness``.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class MotorMountTemplate(BaseTemplate):
    object_type = "motor_mount"
    name = "NEMA Motor Mount"
    description = "Flat plate with the NEMA face bolt pattern + centre pilot bore."
    dimensions = [
        DimensionSpec("nema_size", "NEMA size", 17.0, 8.0, 42.0),
        DimensionSpec("bolt_spacing", "Bolt spacing (square)", 31.0, 10.0, 120.0),
        DimensionSpec("bolt_hole", "Mounting hole Ø", 3.4, 1.5, 12.0),
        DimensionSpec("pilot_diameter", "Centre pilot Ø", 23.0, 5.0, 120.0),
        DimensionSpec("plate_size", "Plate size (square)", 50.0, 20.0, 200.0),
        DimensionSpec("thickness", "Plate thickness", 6.0, 2.0, 30.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        spacing = r["bolt_spacing"]
        bolt = r["bolt_hole"]
        pilot = r["pilot_diameter"]
        thk = r["thickness"]
        plate = max(r["plate_size"], spacing + 2 * bolt + 6.0)
        if pilot >= plate:
            raise CadGenerationError(
                f"pilot bore ({pilot}mm) must be smaller than the plate ({plate}mm)")

        body = (cq.Workplane("XY").rect(plate, plate).extrude(thk)
                .edges("|Z").fillet(min(5.0, plate * 0.12)))
        # Centre pilot/shaft clearance bore (through).
        body = body.faces(">Z").workplane().hole(pilot)
        # Four mounting holes on the square NEMA pattern.
        half = spacing / 2.0
        body = (body.faces(">Z").workplane()
                .pushPoints([(half, half), (-half, half), (half, -half), (-half, -half)])
                .hole(bolt))
        return body
