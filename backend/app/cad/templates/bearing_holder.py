"""Bearing holder / pillow-style seat for a deep-groove ball bearing.

A cylindrical (or rounded-square) block with a press- or slip-fit bore sized to the
bearing's outer diameter, a retention lip (shoulder) so the bearing seats to a
defined depth, and a smaller through-bore for the shaft. Dimensions come from the
curated bearing table (``object_intelligence.standards``), so the bore and width are
exact; the fit CLEARANCE is an explicit, surfaced assumption.

Spec dimensions: ``bearing_outer``, ``bearing_bore``, ``bearing_width``,
``fit_clearance`` (signed; negative = interference/press), ``lip`` (retention lip
radial depth), ``wall`` (material around the bore), ``thickness`` override.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class BearingHolderTemplate(BaseTemplate):
    object_type = "bearing_holder"
    name = "Bearing Holder"
    description = "Seat with a fit-toleranced bore + retention lip for a ball bearing."
    dimensions = [
        DimensionSpec("bearing_outer", "Bearing OD", 22.0, 3.0, 200.0),
        DimensionSpec("bearing_bore", "Bearing bore (ID)", 8.0, 1.0, 150.0),
        DimensionSpec("bearing_width", "Bearing width", 7.0, 1.0, 60.0),
        DimensionSpec("fit_clearance", "Seat clearance (+slip/-press)", 0.0, -0.5, 1.0),
        DimensionSpec("lip", "Retention lip depth", 1.5, 0.5, 8.0),
        DimensionSpec("wall", "Wall around bore", 4.0, 1.5, 40.0),
        DimensionSpec("thickness", "Holder thickness", 11.0, 2.0, 120.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        od = r["bearing_outer"]
        bore = r["bearing_bore"]
        width = r["bearing_width"]
        clr = r.dims_mm.get("fit_clearance", 0.0)
        lip = r["lip"]
        wall = r["wall"]
        if bore >= od:
            raise CadGenerationError(
                f"bearing bore ({bore}mm) must be smaller than its OD ({od}mm)")

        seat_d = od + clr                         # press (-) / slip (+) fit
        body_d = seat_d + 2 * wall                # outer body
        # Thickness: enough to seat the bearing plus a retention lip floor.
        thk = max(r["thickness"], width + lip + 2.0)

        body = cq.Workplane("XY").circle(body_d / 2.0).extrude(thk)
        # Bearing seat pocket from the top, down to the lip shoulder.
        seat_depth = thk - lip
        body = body.faces(">Z").workplane().hole(seat_d, depth=seat_depth)
        # Shaft clearance bore THROUGH the lip floor — the bearing's outer race rests
        # on the retention lip (annular shoulder between seat_d and shaft_clear).
        shaft_clear = min(bore + 1.0, seat_d - 1.0)
        body = body.faces(">Z").workplane().hole(shaft_clear)
        return body
