"""Deterministic, simplified tubular-chassis / space-frame assembly generator.

This produces a CONCEPT assembly model — round-tube structure plus simple plate
placeholders for mounts — laid out from a target envelope. It is NOT a certified
or structurally-analyzed design (no FEA, no load cases); it is a first-pass
geometric concept the user can refine.

Coordinate frame: x = length (front −x → rear +x), y = width (left/right,
symmetric about y=0), z = height (ground at z≈0, up +z).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cadquery as cq

from app.cad.base import CadGenerationError

# Defaults from the brief.
DEFAULT_LENGTH = 4200.0
DEFAULT_WIDTH = 1800.0
DEFAULT_HEIGHT = 1200.0

REQUIRED_SECTIONS = ["main_frame", "front_bay", "cabin", "rear_bay", "roll_cage"]


@dataclass
class Component:
    id: str
    section: str          # one of REQUIRED_SECTIONS
    kind: str             # "tube" | "plate"
    type: str             # role label (lower_rail / roll_cage_bar / seat_mount …)
    od: float = 0.0       # tube outer diameter (mm)
    wall: float = 0.0     # tube wall thickness (mm)
    p1: tuple = (0.0, 0.0, 0.0)
    p2: tuple = (0.0, 0.0, 0.0)
    size: tuple = (0.0, 0.0, 0.0)   # plate w,d,h
    center: tuple = (0.0, 0.0, 0.0)

    def to_meta(self) -> dict:
        d = {"id": self.id, "section": self.section, "kind": self.kind, "type": self.type}
        if self.kind == "tube":
            d.update(od=round(self.od, 2), wall=round(self.wall, 2),
                     p1=[round(v, 1) for v in self.p1], p2=[round(v, 1) for v in self.p2])
            d["anchor"] = [round((a + b) / 2, 1) for a, b in zip(self.p1, self.p2)]
        else:
            d.update(size=[round(v, 1) for v in self.size],
                     center=[round(v, 1) for v in self.center])
            d["anchor"] = [round(v, 1) for v in self.center]
        return d


@dataclass
class ChassisBuild:
    solid: cq.Workplane
    components: list[Component]
    bbox_mm: dict
    envelope_mm: dict
    tube_od: float
    tube_wall: float

    @property
    def tube_count(self) -> int:
        return sum(1 for c in self.components if c.kind == "tube")

    @property
    def sections_present(self) -> list[str]:
        return sorted({c.section for c in self.components})


def parse_envelope(prompt: str) -> tuple[float, float, float]:
    """Pull length/width/height (mm) from the prompt; fall back to defaults."""
    t = (prompt or "").lower()

    def near(*labels) -> float | None:
        for lab in labels:
            m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:[a-z ]{0,12}?)" + lab, t)
            if m:
                return float(m.group(1))
        return None

    length = near("long", "length") or DEFAULT_LENGTH
    width = near("wide", "width") or DEFAULT_WIDTH
    height = near("high", "height", "tall") or DEFAULT_HEIGHT
    # Guard against absurd / impossible values.
    length = min(max(length, 100.0), 12000.0)
    width = min(max(width, 100.0), 6000.0)
    height = min(max(height, 100.0), 6000.0)
    return length, width, height


def _layout(length: float, width: float, height: float,
            od: float, wall: float, cage_od: float) -> list[Component]:
    """All chassis components for one envelope, with left/right symmetry."""
    L, W, H = length, width, height
    hw = W * 0.46                       # rail half-spacing (outer rails)
    ty = W * 0.10                       # transmission tunnel half-spacing
    z_low = H * 0.06
    z_mid = H * 0.50
    z_top = H * 0.96
    x_front = -L / 2 + od              # front extreme
    x_rear = L / 2 - od               # rear extreme
    cab_front = -L * 0.14
    cab_rear = L * 0.14

    comps: list[Component] = []

    def tube(cid, section, role, p1, p2, o=od, w=wall):
        comps.append(Component(id=cid, section=section, kind="tube", type=role,
                               od=o, wall=w, p1=tuple(p1), p2=tuple(p2)))

    def tube_pair(cid, section, role, p1, p2, o=od, w=wall):
        # left = as given (negative y), right = mirrored across y=0.
        tube(f"{cid}_left", section, role, p1, p2, o, w)
        m1 = (p1[0], -p1[1], p1[2])
        m2 = (p2[0], -p2[1], p2[2])
        tube(f"{cid}_right", section, role, m1, m2, o, w)

    def plate(cid, section, role, center, size):
        comps.append(Component(id=cid, section=section, kind="plate", type=role,
                               center=tuple(center), size=tuple(size)))

    def plate_pair(cid, section, role, center, size):
        plate(f"{cid}_left", section, role, center, size)
        plate(f"{cid}_right", section, role, (center[0], -center[1], center[2]), size)

    # --- main frame: lower longitudinal rails (full length) ---
    tube_pair("lower_rail", "main_frame", "lower_rail",
              (x_front, -hw, z_low), (x_rear, -hw, z_low))

    # --- cross-members ---
    tube("front_cross_member", "front_bay", "cross_member",
         (x_front, -hw, z_low), (x_front, hw, z_low))
    tube("rear_cross_member", "rear_bay", "cross_member",
         (x_rear, -hw, z_low), (x_rear, hw, z_low))
    for i, cx in enumerate((cab_front, 0.0, cab_rear)):
        tube(f"cabin_cross_member_{i}", "cabin", "cross_member",
             (cx, -hw, z_low), (cx, hw, z_low))

    # --- transmission tunnel rails (cabin) ---
    tube_pair("transmission_tunnel_rail", "cabin", "transmission_tunnel",
              (cab_front, -ty, z_low), (cab_rear, -ty, z_low))

    # --- front engine bay longitudinal rails ---
    tube_pair("front_bay_rail", "front_bay", "frame_rail",
              (x_front, -hw * 0.7, z_low), (cab_front, -hw * 0.7, z_low))

    # --- rear frame longitudinal rails ---
    tube_pair("rear_bay_rail", "rear_bay", "frame_rail",
              (cab_rear, -hw * 0.7, z_low), (x_rear, -hw * 0.7, z_low))

    # --- diagonal triangulation braces (front + rear floor X-braces) ---
    tube("front_diag_a", "main_frame", "diagonal_brace",
         (x_front, -hw, z_low), (cab_front, hw, z_low))
    tube("front_diag_b", "main_frame", "diagonal_brace",
         (x_front, hw, z_low), (cab_front, -hw, z_low))
    tube("rear_diag_a", "main_frame", "diagonal_brace",
         (cab_rear, -hw, z_low), (x_rear, hw, z_low))
    tube("rear_diag_b", "main_frame", "diagonal_brace",
         (cab_rear, hw, z_low), (x_rear, -hw, z_low))

    # --- roll cage: front + main hoops, roof rails ---
    tube_pair("front_hoop_post", "roll_cage", "roll_cage_bar",
              (cab_front, -hw, z_low), (cab_front, -hw, z_top), cage_od, wall)
    tube("front_hoop_top", "roll_cage", "roll_cage_bar",
         (cab_front, -hw, z_top), (cab_front, hw, z_top), cage_od, wall)
    tube_pair("main_hoop_post", "roll_cage", "roll_cage_bar",
              (cab_rear, -hw, z_low), (cab_rear, -hw, z_top), cage_od, wall)
    tube("main_hoop_top", "roll_cage", "roll_cage_bar",
         (cab_rear, -hw, z_top), (cab_rear, hw, z_top), cage_od, wall)
    tube_pair("roof_rail", "roll_cage", "roll_cage_bar",
              (cab_front, -hw, z_top), (cab_rear, -hw, z_top), cage_od, wall)

    # --- side-impact bars (cabin, mid height) ---
    tube_pair("side_impact_bar", "cabin", "side_impact_bar",
              (cab_front, -hw, z_mid), (cab_rear, -hw, z_mid))

    # --- mount plate placeholders ---
    pw = (W * 0.07, W * 0.07, H * 0.02)  # generic plate size
    plate_pair("seat_mount", "cabin", "seat_mount", (0.0, W * 0.16, z_low), pw)
    plate_pair("engine_mount", "front_bay", "engine_mount", (-L * 0.30, hw * 0.6, z_low), pw)
    plate_pair("front_suspension_mount", "front_bay", "suspension_mount",
               (x_front + L * 0.03, hw, z_low), pw)
    plate_pair("rear_suspension_mount", "rear_bay", "suspension_mount",
               (x_rear - L * 0.03, hw, z_low), pw)
    plate("radiator_mount", "front_bay", "radiator_mount", (x_front, 0.0, z_mid), pw)
    plate("fuel_tank_mount", "rear_bay", "fuel_tank_mount", (L * 0.25, 0.0, z_low), pw)

    return comps


def _unit(v: tuple) -> tuple[tuple, float]:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-9:
        return (0.0, 0.0, 1.0), 0.0
    return (v[0] / n, v[1] / n, v[2] / n), n


def _tube_solid(c: Component):
    d = (c.p2[0] - c.p1[0], c.p2[1] - c.p1[1], c.p2[2] - c.p1[2])
    u, length = _unit(d)
    if length < 1e-6:
        return None
    inner = max(0.0, c.od - 2 * c.wall)
    plane = cq.Plane(origin=tuple(c.p1), normal=tuple(u))
    wp = cq.Workplane(plane).circle(c.od / 2)
    if inner > 0:
        wp = wp.circle(inner / 2)
    return wp.extrude(length).val()


def _plate_solid(c: Component):
    w, d, h = c.size
    return cq.Workplane("XY").box(w, d, max(h, 1.0)).translate(tuple(c.center)).val()


def build_chassis(prompt: str, od: float = 40.0, wall: float = 2.5,
                  cage_od: float = 45.0) -> ChassisBuild:
    """Build the simplified chassis assembly as a single compound (multi-body)."""
    L, W, H = parse_envelope(prompt)
    components = _layout(L, W, H, od, wall, cage_od)

    solids = []
    for c in components:
        s = _tube_solid(c) if c.kind == "tube" else _plate_solid(c)
        if s is not None:
            solids.append(s)
    if not solids:
        raise CadGenerationError("chassis assembly produced no geometry")

    compound = cq.Compound.makeCompound(solids)
    wp = cq.Workplane("XY").add(compound)
    bb = wp.val().BoundingBox()
    bbox = {"x": round(bb.xlen, 1), "y": round(bb.ylen, 1), "z": round(bb.zlen, 1)}
    return ChassisBuild(
        solid=wp, components=components, bbox_mm=bbox,
        envelope_mm={"x": L, "y": W, "z": H}, tube_od=od, tube_wall=wall,
    )
