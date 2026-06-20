"""Electronics enclosure: a shelled box with corner screw bosses and a lid.

Demo-ready details: rounded outer corners, four internal screw bosses with
pilot holes, and a matching lid with countersunk clearance holes at the corners
— a genuine screw-together assembly, exported as both pieces side by side.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.cad.helpers import safe_fillet
from app.schemas.design_spec import DesignSpec


class EnclosureTemplate(BaseTemplate):
    object_type = "enclosure"
    name = "Electronics Enclosure with Lid"
    description = (
        "Hollow box with rounded corners, internal screw bosses and a matching "
        "countersunk lid placed alongside for printing."
    )
    dimensions = [
        DimensionSpec("width", "Inner width (X)", 80.0, 10.0, 1000.0),
        DimensionSpec("depth", "Inner depth (Y)", 60.0, 10.0, 1000.0),
        DimensionSpec("height", "Inner height (Z)", 40.0, 5.0, 1000.0),
        DimensionSpec("wall_thickness", "Wall thickness", 2.5, 0.8, 50.0),
        DimensionSpec("lid_thickness", "Lid thickness", 2.5, 0.8, 50.0),
        DimensionSpec("corner_radius", "Outer corner radius", 3.0, 0.0, 200.0),
        DimensionSpec("boss_diameter", "Screw boss diameter (0 = none)", 7.0, 0.0, 60.0),
        DimensionSpec("vent_count", "Vent slots per side (0 = none)", 0.0, 0.0, 30.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        iw, idp, ih = r["width"], r["depth"], r["height"]
        wt, lt = r["wall_thickness"], r["lid_thickness"]
        corner, boss = r["corner_radius"], r["boss_diameter"]

        if wt * 2 >= iw or wt * 2 >= idp:
            raise CadGenerationError(
                "wall_thickness too large for the requested inner dimensions"
            )

        ow, od = iw + 2 * wt, idp + 2 * wt
        oh = ih + wt  # closed bottom, open top

        # Outer shell: round the outer vertical corners *before* hollowing so
        # only the four outer edges are affected.
        body = cq.Workplane("XY").box(ow, od, oh, centered=(True, True, False))
        if corner > 0:
            body = safe_fillet(body, min(corner, ow / 2 - 0.5, od / 2 - 0.5), "|Z")
        cavity = (
            cq.Workplane("XY")
            .workplane(offset=wt)
            .box(iw, idp, ih + 1, centered=(True, True, False))
        )
        body = body.cut(cavity)

        # Corner screw bosses + pilot holes (skip if they wouldn't fit).
        post_positions: list[tuple[float, float]] = []
        if boss > 0 and iw > 3 * boss and idp > 3 * boss:
            inset = boss / 2 + 0.5
            px, py = iw / 2 - inset, idp / 2 - inset
            post_positions = [(px, py), (-px, py), (px, -py), (-px, -py)]
            pilot = max(2.0, min(boss * 0.4, 4.0))
            for x, y in post_positions:
                post = (
                    cq.Workplane("XY")
                    .workplane(offset=wt)
                    .moveTo(x, y)
                    .circle(boss / 2)
                    .extrude(ih)
                )
                body = body.union(post)
            for x, y in post_positions:
                pilot_cut = (
                    cq.Workplane("XY")
                    .workplane(offset=oh)
                    .moveTo(x, y)
                    .circle(pilot / 2)
                    .extrude(-ih * 0.85)
                )
                body = body.cut(pilot_cut)

        # Optional vent slots through a long (+Y) wall for cooling.
        vent_count = int(round(r["vent_count"]))
        if vent_count > 0:
            slot_w = max(2.0, min(4.0, iw / (vent_count * 3)))
            slot_h = ih * 0.5
            pitch = iw / (vent_count + 1)
            y_wall = od / 2.0  # +Y outer wall
            for k in range(vent_count):
                cx = -iw / 2.0 + pitch * (k + 1)
                slot = (
                    cq.Workplane("XZ")
                    .workplane(offset=-(y_wall + 1))
                    .moveTo(cx, wt + ih / 2.0)
                    .slot2D(slot_h, slot_w, 90)
                    .extrude(wt + 2)
                )
                body = body.cut(slot)

        # Lid: flat plate with rounded corners and countersunk corner holes.
        lid = cq.Workplane("XY").box(ow, od, lt, centered=(True, True, False))
        if corner > 0:
            lid = safe_fillet(lid, min(corner, ow / 2 - 0.5, od / 2 - 0.5), "|Z")
        if post_positions:
            clear = max(2.4, min(boss * 0.5, 5.0))
            csk = clear + 2.0
            lid = (
                lid.faces(">Z")
                .workplane(centerOption="CenterOfBoundBox")
                .pushPoints(post_positions)
                .cskHole(clear, csk, 90)
            )

        # Park the lid next to the body so a single export holds both pieces.
        lid = lid.translate((ow + 12, 0, 0))
        return body.union(lid)
