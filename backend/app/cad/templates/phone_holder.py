"""Desktop phone holder / stand fitted to a phone's outline.

A back-leaning slot cradle: the phone drops into a slot sized to its thickness +
clearance, resting on a BOTTOM LIP, supported by an angled BACK SUPPORT, with a
CABLE NOTCH through the front of the lip for a charging cable. Dimensions come from
the phone preset / user, with explicit fit-clearance assumptions.

Spec dimensions: ``phone_width``, ``phone_depth`` (thickness), ``phone_length``
(used for back-support height), ``fit_clearance``, ``lean_deg``, ``wall``.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class PhoneHolderTemplate(BaseTemplate):
    object_type = "phone_holder"
    name = "Phone Holder"
    description = "Back-leaning slot cradle with bottom lip + cable notch."
    dimensions = [
        DimensionSpec("phone_width", "Phone width", 71.6, 30.0, 200.0),
        DimensionSpec("phone_depth", "Phone thickness", 7.8, 3.0, 30.0),
        DimensionSpec("phone_length", "Phone length", 147.6, 60.0, 300.0),
        DimensionSpec("fit_clearance", "Fit clearance", 1.5, 0.3, 5.0),
        DimensionSpec("lean_deg", "Back lean (deg)", 15.0, 0.0, 35.0),
        DimensionSpec("wall", "Wall thickness", 4.0, 2.0, 12.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        pw = r["phone_width"]
        pt = r["phone_depth"]
        clr = r["fit_clearance"]
        lean = r["lean_deg"]
        wall = r["wall"]

        slot_w = pw + clr                 # along X (phone width)
        slot_t = pt + clr                 # along Y (phone thickness)
        base_w = slot_w + 2 * wall        # X
        base_d = max(slot_t + 2 * wall + 18.0, 55.0)   # Y (front lip + back support)
        lip = 6.0                         # bottom lip height the phone rests on
        back_h = 45.0                     # back support height
        base_h = lip + 4.0                # solid base block height
        if base_w <= 0 or base_d <= 0:
            raise CadGenerationError("invalid phone dimensions")

        # Solid base block.
        body = cq.Workplane("XY").box(base_w, base_d, base_h, centered=(True, True, False))
        # Back support wall (rear), leaning back by lean_deg.
        back = (cq.Workplane("XY").box(base_w, wall, back_h, centered=(True, True, False))
                .translate((0, -base_d / 2 + wall / 2, 0)))
        if lean > 0.1:
            back = back.rotate((0, -base_d / 2 + wall / 2, 0), (1, 0, 0), -lean)
        body = body.union(back.translate((0, 0, base_h - 0.01)))
        # Front lip (low wall the phone leans against / rests behind).
        front = (cq.Workplane("XY").box(base_w, wall, lip + 8.0, centered=(True, True, False))
                 .translate((0, base_d / 2 - wall / 2 - 8.0, base_h - 0.01)))
        body = body.union(front)

        # The phone slot: a thin pocket between the back support and the front lip,
        # leaning back, stopping above the base top by the lip height (the rest =
        # bottom lip the phone sits on).
        slot = cq.Workplane("XY").box(slot_w, slot_t, back_h + 20.0, centered=(True, True, False))
        slot = slot.translate((0, -2.0, base_h + lip))
        if lean > 0.1:
            slot = slot.rotate((0, -2.0, base_h + lip), (1, 0, 0), -lean)
        body = body.cut(slot)

        # Cable notch: a channel through the front lip + base for the charger.
        notch = cq.Workplane("XY").box(min(20.0, slot_w * 0.4), base_d, lip + 12.0,
                                       centered=(True, True, False))
        body = body.cut(notch.translate((0, 0, 0)))
        return body
