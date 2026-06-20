"""L-bracket: two perpendicular flanges sharing a corner."""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, DimensionSpec
from app.cad.helpers import safe_fillet
from app.schemas.design_spec import DesignSpec


class LBracketTemplate(BaseTemplate):
    object_type = "l_bracket"
    name = "L-Bracket"
    description = "Right-angle bracket with two flanges and a mounting hole on each."
    dimensions = [
        DimensionSpec("length", "Horizontal leg length (X)", 60.0, 10.0, 1000.0),
        DimensionSpec("width", "Width (Y)", 40.0, 5.0, 1000.0),
        DimensionSpec("height", "Vertical leg height (Z)", 60.0, 10.0, 1000.0),
        DimensionSpec("thickness", "Material thickness", 5.0, 1.0, 100.0),
        DimensionSpec("hole_diameter", "Mounting hole diameter", 6.0, 0.0, 100.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        length, width = r["length"], r["width"]
        height, t = r["height"], r["thickness"]

        # L profile in the XZ plane (corner at origin), extruded along Y.
        part = (
            cq.Workplane("XZ")
            .moveTo(0, 0)
            .lineTo(length, 0)
            .lineTo(length, t)
            .lineTo(t, t)
            .lineTo(t, height)
            .lineTo(0, height)
            .close()
            .extrude(width)
        )

        hd = r["hole_diameter"]
        if hd > 0 and hd < min(length, height) - 2 * t:
            y_mid = width / 2.0
            # Hole through the horizontal flange (axis ‖ Z), near its free end.
            x_h = (length + t) / 2.0
            h_cut = (
                cq.Workplane("XY")
                .moveTo(x_h, y_mid)
                .circle(hd / 2.0)
                .extrude(t + 2)
            ).translate((0, 0, -1))
            # Hole through the vertical flange (axis ‖ X), near its free end.
            z_v = (height + t) / 2.0
            v_cut = (
                cq.Workplane("YZ")
                .moveTo(y_mid, z_v)
                .circle(hd / 2.0)
                .extrude(t + 2)
            ).translate((-1, 0, 0))
            part = part.cut(h_cut).cut(v_cut)

        # Optional inner-corner fillet for strength (best-effort).
        fr = spec.to_mm(spec.fillet_radius or 0)
        part = safe_fillet(part, min(fr, t * 0.9), selector="|Y")
        return part
