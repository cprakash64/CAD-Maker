"""Pipe clamp / saddle: a base block with a pipe channel and mounting ears."""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class PipeClampTemplate(BaseTemplate):
    object_type = "pipe_clamp"
    name = "Pipe Clamp / Saddle"
    description = (
        "Wall saddle with a semicircular channel sized to a pipe, plus two "
        "mounting holes on flanking ears."
    )
    dimensions = [
        DimensionSpec("pipe_diameter", "Pipe outer diameter", 25.0, 3.0, 500.0),
        DimensionSpec("width", "Saddle width (along pipe axis)", 25.0, 5.0, 500.0),
        DimensionSpec("thickness", "Wall thickness behind pipe", 6.0, 1.5, 100.0),
        DimensionSpec("ear_width", "Mounting ear width", 18.0, 5.0, 300.0),
        DimensionSpec("hole_diameter", "Mounting hole diameter", 6.0, 0.0, 100.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        pd, width = r["pipe_diameter"], r["width"]
        t, ear, hd = r["thickness"], r["ear_width"], r["hole_diameter"]

        radius = pd / 2.0
        body_height = radius + t  # channel sits at the top of the block
        total_width = pd + 2 * ear

        # Base block (origin centered in X/Y, sitting on Z=0).
        part = cq.Workplane("XY").box(
            total_width, width, body_height, centered=(True, True, False)
        )

        # Cut the semicircular pipe channel from the top, centered.
        channel = (
            cq.Workplane("XZ")
            .workplane(offset=width / 2.0)
            .moveTo(0, body_height)
            .circle(radius)
            .extrude(-width)
        )
        part = part.cut(channel)

        # Mounting holes through the ears (axis ‖ Z).
        if hd > 0:
            if hd >= ear:
                raise CadGenerationError(
                    f"hole_diameter ({hd}mm) does not fit in ear width ({ear}mm)"
                )
            x_off = radius + ear / 2.0
            for sx in (-x_off, x_off):
                cut = (
                    cq.Workplane("XY")
                    .moveTo(sx, 0)
                    .circle(hd / 2.0)
                    .extrude(body_height + 2)
                ).translate((0, 0, -1))
                part = part.cut(cut)
        return part
