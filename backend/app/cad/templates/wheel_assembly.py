"""Wheel assembly: a rubber tire mounted on a rim.

Composes the finished hollow tire (``tire.py``) with a rim (``rim.py``) seated in
its bore — the rim's outer (bead-seat) diameter equals the tire's inner diameter, so
the rim sits inside the tire without filling the sidewalls. Shipped as the tire +
rim (two coaxial bodies); status is REVIEW unless all critical dimensions are given.

Spec dimensions (mm): ``outer_diameter`` (tire OD), ``inner_diameter`` (tire ID =
rim seat), ``width``, ``rim_diameter`` (rim OD; defaults to the tire ID),
``center_bore``, ``spoke_count``, ``tread_style_code``, ``hex_hub``.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.cad.templates.rim import RimTemplate
from app.cad.templates.tire import TireTemplate
from app.schemas.design_spec import DesignSpec


class WheelAssemblyTemplate(BaseTemplate):
    object_type = "wheel_assembly"
    name = "Wheel Assembly"
    description = "Tire mounted on a rim (tire + rim seated together)."
    dimensions = [
        DimensionSpec("outer_diameter", "Tire outer diameter", 100.0, 20.0, 2000.0),
        DimensionSpec("inner_diameter", "Tire inner diameter (rim seat)", 60.0, 5.0, 1900.0),
        DimensionSpec("width", "Width", 30.0, 5.0, 600.0),
        DimensionSpec("rim_diameter", "Rim outer diameter", 60.0, 10.0, 1900.0),
        DimensionSpec("center_bore", "Rim centre bore", 20.0, 2.0, 400.0),
        DimensionSpec("spoke_count", "Spoke count", 5.0, 3.0, 24.0),
        DimensionSpec("tread_style_code", "Tread style (1-5)", 3.0, 1.0, 5.0),
        DimensionSpec("hex_hub", "Hex hub bore (1/0)", 0.0, 0.0, 1.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        od = r["outer_diameter"]
        idia = r["inner_diameter"]
        width = r["width"]
        rim_od = r.dims_mm.get("rim_diameter") or idia
        # The rim's bead-seat diameter matches the tire's inner diameter, but the tire
        # now has a CONCAVE inner cavity that only touches the rim at the bead tips —
        # so seat the rim ~1mm PROUD of the tire ID to interpenetrate the beads and
        # fuse into one clean solid (rather than a razor-thin two-body contact).
        rim_od = min(rim_od, idia) + 1.2
        if rim_od <= r["center_bore"] + 6.0:
            raise CadGenerationError("rim diameter too small for the centre bore")

        tire_dims = {"outer_diameter": od, "inner_diameter": idia, "width": width,
                     "tread_style_code": r.dims_mm.get("tread_style_code", 3.0)}
        tire = TireTemplate().build(DesignSpec(
            object_type="tire", dimensions=tire_dims,
            manufacturing_method=spec.manufacturing_method, material="rubber"))

        rim_dims = {"rim_diameter": rim_od, "width": width,
                    "center_bore": r["center_bore"], "spoke_count": r["spoke_count"]}
        if r.dims_mm.get("hex_hub", 0.0) >= 0.5:
            rim_dims["hex_hub"] = 1.0
        rim = RimTemplate().build(DesignSpec(
            object_type="rim", dimensions=rim_dims,
            manufacturing_method=spec.manufacturing_method, material="aluminum"))

        # Seat the rim in the tire bore, coaxial. The rim OD slightly overlaps the
        # tire ID so they fuse into one solid; a compound is fine either way.
        return tire.union(rim)
