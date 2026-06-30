"""Generic fitted enclosure for a user-described board / PCB.

A parametric enclosure sized to a user-given board (length × width × height) with
corner standoffs carrying the requested mounting-hole pattern, a removable lid, and
any user-requested port cutouts (USB-C, HDMI, Ethernet, …) cut as TRUE through-wall
openings (reuses the device-enclosure through-cutter + audit). Used when the user
supplies their OWN dimensions (``user_provided`` trust → may PASS once geometry +
requested features validate).

Spec dimensions: ``board_length``, ``board_width``, ``board_height`` (component
clearance), ``wall_thickness``, ``mount_hole`` (Ø), ``mount_inset``, ``mount_count``
(0/4), ``logo`` (0/1), and ``cut_<port>`` (0/1) flags for each requested port.
"""
from __future__ import annotations

import cadquery as cq
from cadquery import Vector

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.cad.object_intelligence.features import PORT_CUTOUT_SIZES
from app.cad.templates.device_enclosure import (
    _LID_EXPLODE_MM,
    audit_port_openings,
    set_last_port_audit,
    wall_opening_cutter,
)
from app.schemas.design_spec import DesignSpec

_BOARD_TO_WALL_MM = 2.5


class GenericFittedBoxTemplate(BaseTemplate):
    object_type = "generic_fitted_box"
    name = "Fitted Enclosure"
    description = "Enclosure fitted to a user-described board with posts, ports + a lid."
    dimensions = [
        DimensionSpec("board_length", "Board length", 80.0, 5.0, 400.0),
        DimensionSpec("board_width", "Board width", 50.0, 5.0, 400.0),
        DimensionSpec("board_height", "Component clearance", 15.0, 3.0, 200.0),
        DimensionSpec("wall_thickness", "Wall thickness", 2.5, 1.0, 8.0),
        DimensionSpec("mount_hole", "Mounting hole Ø", 3.0, 0.0, 12.0),
        DimensionSpec("mount_inset", "Corner inset", 4.0, 2.0, 30.0),
        DimensionSpec("mount_count", "Mounting holes (0/4)", 4.0, 0.0, 4.0),
        DimensionSpec("logo", "Logo emboss (0/1)", 0.0, 0.0, 1.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        bl = r["board_length"]
        bw = r["board_width"]
        bh = r["board_height"]
        wall = r["wall_thickness"]
        mh = r.dims_mm.get("mount_hole", 3.0)
        inset = r["mount_inset"]
        count = int(round(r.dims_mm.get("mount_count", 4.0)))

        inner_x = bl + 2 * _BOARD_TO_WALL_MM
        inner_y = bw + 2 * _BOARD_TO_WALL_MM
        below = 3.0
        cavity_h = below + bh
        outer_x = inner_x + 2 * wall
        outer_y = inner_y + 2 * wall
        outer_h = wall + cavity_h
        board_low_z = wall + below
        if min(inner_x, inner_y) <= 2 * wall:
            raise CadGenerationError("walls thicker than the board cavity")

        base = cq.Workplane("XY").box(outer_x, outer_y, outer_h, centered=(True, True, False))
        cavity = (cq.Workplane("XY").workplane(offset=wall)
                  .box(inner_x, inner_y, cavity_h + 1.0, centered=(True, True, False)))
        base = base.cut(cavity)

        # Corner standoffs carrying the mounting holes (board centred in cavity).
        if count > 0 and mh > 0.1:
            so_r = max(mh / 2.0 + 1.6, 2.5)
            sx = bl / 2.0 - inset
            sy = bw / 2.0 - inset
            for px, py in [(sx, sy), (-sx, sy), (sx, -sy), (-sx, -sy)][:count]:
                post = (cq.Workplane("XY").workplane(offset=wall)
                        .center(px, py).circle(so_r).extrude(below))
                base = base.union(post)
                hole = cq.Solid.makeCylinder(
                    mh / 2.0, below + wall * 0.6, Vector(px, py, board_low_z + 0.2),
                    Vector(0, 0, -1))
                base = base.cut(cq.Workplane(obj=hole))

        # Requested port cutouts: true through-wall openings on the front long wall
        # (y_min), spread along its length. Each is audited so the route can confirm
        # the requested feature is actually present (and through).
        # Port-cutout flags (cut_usb_c, …) are NOT declared DimensionSpecs, so read
        # them from the raw spec dimensions (resolve() keeps only declared keys).
        ports = self._requested_ports(spec.dimensions, outer_x, board_low_z)
        for sp in ports:
            base = base.cut(wall_opening_cutter(
                side=sp["side"], pos=sp["pos"], z_center=sp["z_center"],
                width=sp["width"], height=sp["height"],
                outer_x=outer_x, outer_y=outer_y, wall=wall))
        set_last_port_audit(audit_port_openings(base, ports, outer_x, outer_y, wall))

        # Removable lid (exploded above), optional logo pad.
        lid_t = max(wall, 2.0)
        lid_z = outer_h + _LID_EXPLODE_MM
        lid = (cq.Workplane("XY").workplane(offset=lid_z)
               .box(outer_x, outer_y, lid_t, centered=(True, True, False)))
        if r.dims_mm.get("logo", 0.0) >= 0.5:
            pad = (cq.Workplane("XY").workplane(offset=lid_z + lid_t)
                   .center(0, 0).box(min(40.0, outer_x * 0.5), min(16.0, outer_y * 0.4),
                                     1.0, centered=(True, True, False)))
            lid = lid.union(pad)
        return base.union(lid)

    def _requested_ports(self, dims: dict, outer_x: float, board_low_z: float
                         ) -> list[dict]:
        keys = [k for k in PORT_CUTOUT_SIZES if dims.get(f"cut_{k}", 0.0) >= 0.5]
        if not keys:
            return []
        # Spread the requested ports evenly along the front wall.
        span = outer_x * 0.7
        step = span / max(len(keys), 1)
        x0 = -span / 2.0 + step / 2.0
        ports: list[dict] = []
        for i, k in enumerate(keys):
            w, h = PORT_CUTOUT_SIZES[k]
            ports.append({
                "name": k, "side": "y_min", "pos": x0 + i * step,
                "z_center": board_low_z + max(h, 4.0) / 2.0 + 0.5,
                "width": w + 1.0, "height": h + 1.0, "kind": "port", "required": True,
            })
        return ports
