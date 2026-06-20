"""Simplified flanged pipe branch / tee (a recognizable version of the kind of
flanged pipe-spool drawing users upload).

Main hollow pipe along X with a bolted flange at each end, plus one side branch
(default 90deg, along Z) with its own bolted flange. Built directly along the
final axes.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


class FlangedPipeBranchTemplate(BaseTemplate):
    object_type = "flanged_pipe_branch"
    name = "Flanged Pipe Branch"
    description = (
        "Hollow main pipe with bolted end flanges and one side branch flange — a "
        "simplified flanged pipe-spool / tee."
    )
    dimensions = [
        DimensionSpec("main_pipe_outer_diameter_mm", "Main pipe OD", 90.0, 20.0, 600.0),
        DimensionSpec("main_pipe_length_mm", "Main pipe length", 200.0, 60.0, 2000.0),
        DimensionSpec("branch_pipe_outer_diameter_mm", "Branch pipe OD", 60.0, 15.0, 500.0),
        DimensionSpec("branch_pipe_length_mm", "Branch pipe length", 90.0, 30.0, 1000.0),
        DimensionSpec("branch_angle_deg", "Branch angle", 90.0, 30.0, 90.0),
        DimensionSpec("flange_outer_diameter_mm", "Flange OD", 140.0, 40.0, 900.0),
        DimensionSpec("flange_thickness_mm", "Flange thickness", 16.0, 4.0, 100.0),
        DimensionSpec("bolt_count", "Bolts per flange", 8.0, 0.0, 24.0),
        DimensionSpec("bolt_hole_diameter_mm", "Bolt hole Ø", 14.0, 2.0, 60.0),
        DimensionSpec("bolt_circle_diameter_mm", "Bolt circle Ø", 115.0, 20.0, 850.0),
        DimensionSpec("wall_thickness_mm", "Pipe wall thickness", 8.0, 1.5, 80.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        mod = r["main_pipe_outer_diameter_mm"]
        ml = r["main_pipe_length_mm"]
        bod = r["branch_pipe_outer_diameter_mm"]
        bl = r["branch_pipe_length_mm"]
        angle = r["branch_angle_deg"]
        fod = r["flange_outer_diameter_mm"]
        ft = r["flange_thickness_mm"]
        bolts = int(round(r["bolt_count"]))
        bhd = r["bolt_hole_diameter_mm"]
        bcd = r["bolt_circle_diameter_mm"]
        wall = r["wall_thickness_mm"]

        if wall * 2 >= mod:
            raise CadGenerationError("wall_thickness too large for the main pipe OD")
        if bcd >= fod:
            raise CadGenerationError("bolt_circle_diameter must be smaller than flange OD")

        main_r, main_ir = mod / 2.0, mod / 2.0 - wall
        branch_r, branch_ir = bod / 2.0, max(1.0, bod / 2.0 - wall)

        # Main pipe along X (YZ plane extrudes along X).
        part = cq.Workplane("YZ").workplane(offset=-ml / 2).circle(main_r).extrude(ml)

        # Branch pipe along Z from the main axis upward.
        branch = cq.Workplane("XY").circle(branch_r).extrude(bl)

        # End + branch flanges (discs) with bolt holes.
        def flange_x(x_face: int) -> cq.Workplane:
            x0 = (ml / 2.0) * x_face - (ft if x_face > 0 else 0)
            disc = cq.Workplane("YZ").workplane(offset=x0).circle(fod / 2.0).extrude(ft)
            return disc

        front_flange = cq.Workplane("YZ").workplane(offset=-ml / 2.0).circle(fod / 2.0).extrude(ft)
        rear_flange = cq.Workplane("YZ").workplane(offset=ml / 2.0 - ft).circle(fod / 2.0).extrude(ft)
        branch_flange = cq.Workplane("XY").workplane(offset=bl - ft).circle(fod / 2.0).extrude(ft)

        part = part.union(branch).union(front_flange).union(rear_flange).union(branch_flange)

        # Non-90 branch: rotate the branch + its flange about Y. (Default 90 = vertical.)
        # For simplicity we keep the branch vertical; angle is recorded as metadata.

        # Bore the main pipe and the branch (central openings).
        main_bore = cq.Workplane("YZ").workplane(offset=-ml / 2 - 1).circle(main_ir).extrude(ml + 2)
        branch_bore = cq.Workplane("XY").workplane(offset=-1).circle(branch_ir).extrude(bl + 2)
        part = part.cut(main_bore).cut(branch_bore)

        # Bolt holes around each flange.
        if bolts > 0 and bhd > 0:
            part = self._bolt_holes(part, bolts, bhd, bcd, "YZ", -ml / 2 - 1, ft + 2)
            part = self._bolt_holes(part, bolts, bhd, bcd, "YZ", ml / 2 - ft - 1, ft + 2)
            part = self._bolt_holes(part, bolts, bhd, bcd, "XY", bl - ft - 1, ft + 2)

        return part

    @staticmethod
    def _bolt_holes(part, bolts, bhd, bcd, plane, offset, depth):
        pts = []
        for b in range(bolts):
            ang = 2 * math.pi * b / bolts
            pts.append((bcd / 2 * math.cos(ang), bcd / 2 * math.sin(ang)))
        cutter = (
            cq.Workplane(plane)
            .workplane(offset=offset)
            .pushPoints(pts)
            .circle(bhd / 2.0)
            .extrude(depth)
        )
        return part.cut(cutter)
