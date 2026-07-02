"""Wheel rim (no rubber tire).

A rim barrel (bead-seat band the tire mounts on) + a central hub with a centre bore
(round or hex) connected by spokes (N-spoke, solid disc, or multi-spoke) and an
optional lug/bolt-hole pattern. Single watertight solid; no rubber.

Spec dimensions (mm): ``rim_diameter``, ``width``, ``center_bore``, ``hub_diameter``,
``spoke_count`` (>=3), ``solid_disc`` (1=solid face instead of spokes), ``hex_hub``
(1=hex centre bore, e.g. RC 12mm hex), ``lug_count``, ``lug_bore``, ``lug_circle``.
"""
from __future__ import annotations

import math

import cadquery as cq
from cadquery import Vector

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec

_COS30 = math.cos(math.pi / 6.0)


class RimTemplate(BaseTemplate):
    object_type = "rim"
    name = "Wheel Rim"
    description = "Rim barrel + hub + spokes + centre bore (no tire)."
    dimensions = [
        DimensionSpec("rim_diameter", "Rim outer diameter", 100.0, 20.0, 1500.0),
        DimensionSpec("width", "Rim width", 30.0, 5.0, 500.0),
        DimensionSpec("center_bore", "Centre bore Ø", 20.0, 2.0, 400.0),
        DimensionSpec("hub_diameter", "Hub diameter", 34.0, 6.0, 500.0),
        DimensionSpec("spoke_count", "Spoke count", 5.0, 3.0, 24.0),
        DimensionSpec("solid_disc", "Solid disc face (1/0)", 0.0, 0.0, 1.0),
        DimensionSpec("hex_hub", "Hex hub bore (1/0)", 0.0, 0.0, 1.0),
        DimensionSpec("lug_count", "Lug hole count", 0.0, 0.0, 12.0),
        DimensionSpec("lug_bore", "Lug hole Ø", 5.0, 1.0, 30.0),
        DimensionSpec("lug_circle", "Lug bolt circle Ø", 40.0, 5.0, 400.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        rim_r = r["rim_diameter"] / 2.0
        width = r["width"]
        bore = r["center_bore"]
        hub_r = min(r["hub_diameter"] / 2.0, rim_r - 6.0)
        spokes = int(round(r["spoke_count"]))
        solid = r.dims_mm.get("solid_disc", 0.0) >= 0.5
        hexhub = r.dims_mm.get("hex_hub", 0.0) >= 0.5
        lugs = int(round(r.dims_mm.get("lug_count", 0.0)))
        if bore / 2.0 >= hub_r - 1.0:
            hub_r = bore / 2.0 + 4.0
        if hub_r >= rim_r - 4.0:
            raise CadGenerationError(
                f"hub ({2*hub_r}mm) too large for the rim ({2*rim_r}mm)")

        barrel_wall = max(2.5, rim_r * 0.06)
        face_t = max(3.0, width * 0.18)     # spoke / face thickness

        # Rim barrel (bead-seat band the tire mounts on) — a hollow cylinder.
        barrel = (cq.Workplane("XY").circle(rim_r).circle(rim_r - barrel_wall)
                  .extrude(width))
        # Bead-seat lips: small inward ridges at both barrel edges.
        for z in (0.0, width - 2.0):
            lip = (cq.Workplane("XY").workplane(offset=z)
                   .circle(rim_r - barrel_wall * 0.4).circle(rim_r - barrel_wall * 1.4)
                   .extrude(2.0))
            barrel = barrel.union(lip)

        # Central hub (with the centre bore), at mid width.
        hub_z0 = width / 2.0 - face_t / 2.0
        hub = (cq.Workplane("XY").workplane(offset=hub_z0)
               .circle(hub_r).extrude(face_t))
        body = barrel.union(hub)

        # Face: a solid disc, or N spokes from hub to barrel.
        if solid:
            disc = (cq.Workplane("XY").workplane(offset=hub_z0)
                    .circle(rim_r - barrel_wall + 0.5).extrude(face_t))
            body = body.union(disc)
        else:
            spoke_len = (rim_r - barrel_wall + 1.0) - hub_r + 2.0
            spoke_w = max(4.0, (2 * math.pi * hub_r / max(spokes, 1)) * 0.7)
            mid = hub_r + spoke_len / 2.0 - 1.0
            for k in range(spokes):
                ang = 360.0 * k / spokes
                arm = (cq.Workplane("XY").workplane(offset=hub_z0)
                       .center(mid, 0).rect(spoke_len, spoke_w).extrude(face_t)
                       .rotate((0, 0, 0), (0, 0, 1), ang))
                body = body.union(arm)

        # Centre bore (round or hex) through the hub.
        if hexhub:
            cutter = (cq.Workplane("XY").polygon(6, bore / _COS30).extrude(width + 2.0)
                      .translate((0, 0, -1.0)))
            body = body.cut(cutter)
        else:
            body = body.faces(">Z").workplane().hole(bore)

        # Lug / bolt holes on a bolt circle through the face.
        if lugs > 0:
            lug_r = r["lug_bore"] / 2.0
            bc = min(r["lug_circle"] / 2.0, hub_r * 0.9)
            for k in range(lugs):
                a = 2 * math.pi * k / lugs
                cyl = cq.Solid.makeCylinder(
                    lug_r, width + 2.0, Vector(bc * math.cos(a), bc * math.sin(a), -1.0),
                    Vector(0, 0, 1))
                body = body.cut(cq.Workplane(obj=cyl))
        return body
