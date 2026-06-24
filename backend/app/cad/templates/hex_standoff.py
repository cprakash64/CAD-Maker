"""Hexagonal standoff / spacer: a true six-sided hex prism with a central
through-bore.

Unlike :class:`SpacerTemplate` (a round body), this builds an actual hexagonal
prism so a "hex standoff" never renders as a round cylinder. The user's
across-flats dimension is preserved EXACTLY; the across-corners diameter is
derived (across_corners = across_flats / cos(30°)) and documented separately.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec

# A regular hexagon: across-corners (circumscribed-circle diameter) is the
# across-flats distance divided by cos(30°). cadquery's polygon(n, diameter)
# inscribes the polygon in a circle of `diameter`, i.e. `diameter` is the
# across-corners distance.
_COS30 = math.cos(math.pi / 6.0)  # 0.8660254…


def across_corners(across_flats: float) -> float:
    return across_flats / _COS30


class HexStandoffTemplate(BaseTemplate):
    object_type = "hex_standoff"
    name = "Hex Standoff / Spacer"
    description = "Six-sided hexagonal prism with a concentric through-bore."
    dimensions = [
        DimensionSpec("across_flats", "Across-flats width", 8.0, 2.0, 300.0),
        DimensionSpec("length", "Length / height", 20.0, 1.0, 1000.0),
        # Default 0 = a solid standoff; an omitted bore means "no bore" (the spec
        # schema rejects a passed 0, so a solid build omits this dimension).
        DimensionSpec("bore_diameter", "Through-bore diameter", 0.0, 0.0, 280.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        af, length, bore = r["across_flats"], r["length"], r["bore_diameter"]
        if bore >= af:
            raise CadGenerationError(
                f"bore_diameter ({bore}mm) must be smaller than the across-flats "
                f"width ({af}mm) of a hex standoff"
            )
        # polygon(6, diameter) -> diameter is the across-corners (circumscribed)
        # distance, so derive it from the requested across-flats.
        part = cq.Workplane("XY").polygon(6, across_corners(af)).extrude(length)
        if bore > 0:
            part = part.faces(">Z").workplane().hole(bore)
        return part
