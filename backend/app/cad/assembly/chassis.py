"""Deterministic, detailed tubular-chassis / space-frame assembly generator.

Builds a CONCEPT space-frame from a named node/edge graph: tapered nose, lower &
upper longitudinal rails, roll cage, triangulation, plus plate/bracket/gusset
placeholders for mounts. It is NOT a certified or structurally-analyzed design
(no FEA, no load cases) — it is a geometric first pass the user refines.

Geometry note: tubes are exported as CLOSED SOLID cylinders (clean watertight
STEP/STL); the real wall thickness is carried as metadata + cut-list data.

Coordinate frame: x = length (front −x → rear +x), y = width (left +y / right −y,
symmetric about y=0), z = height (ground at z≈0, up +z).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cadquery as cq

from app.cad.base import CadGenerationError

DEFAULT_LENGTH = 4200.0
DEFAULT_WIDTH = 1800.0
DEFAULT_HEIGHT = 1200.0

# Validation taxonomy (imported by report.py).
REQUIRED_ZONES = ["front", "engine_bay", "cabin", "roll_cage", "rear"]
REQUIRED_SYSTEMS = [
    "main_frame", "roll_cage", "transmission_tunnel", "side_impact",
    "suspension_mounts", "engine_mounts", "radiator_mount", "fuel_tank_mount",
    "seat_mounts",
]
# Back-compat alias (older imports referenced REQUIRED_SECTIONS).
REQUIRED_SECTIONS = REQUIRED_ZONES


@dataclass
class ChassisSpec:
    length_mm: float
    width_mm: float
    height_mm: float
    wheelbase_mm: float
    front_overhang_mm: float
    rear_overhang_mm: float
    cabin_start_mm: float
    cabin_end_mm: float
    tube_outer_diameter_mm: float = 40.0
    tube_wall_thickness_mm: float = 2.5
    cage_outer_diameter_mm: float = 45.0
    mount_plate_thickness_mm: float = 6.0
    design_detail_level: str = "detailed"   # simple | detailed | reference
    drive_layout: str = "front_engine_rwd"
    seats: int = 2
    chassis_style: str = "tubular_space_frame"
    # Recommended visual appearance (metadata only — no mass/density implied).
    material_name: str = "Powder-coated steel (black)"
    material_appearance: str = "dark_steel"
    material_color: str = "#23262b"

    def to_meta(self) -> dict:
        return {
            "length_mm": round(self.length_mm, 1),
            "width_mm": round(self.width_mm, 1),
            "height_mm": round(self.height_mm, 1),
            "wheelbase_mm": round(self.wheelbase_mm, 1),
            "front_overhang_mm": round(self.front_overhang_mm, 1),
            "rear_overhang_mm": round(self.rear_overhang_mm, 1),
            "cabin_start_mm": round(self.cabin_start_mm, 1),
            "cabin_end_mm": round(self.cabin_end_mm, 1),
            "tube_outer_diameter_mm": self.tube_outer_diameter_mm,
            "tube_wall_thickness_mm": self.tube_wall_thickness_mm,
            "cage_outer_diameter_mm": self.cage_outer_diameter_mm,
            "mount_plate_thickness_mm": self.mount_plate_thickness_mm,
            "design_detail_level": self.design_detail_level,
            "drive_layout": self.drive_layout,
            "seats": self.seats,
            "chassis_style": self.chassis_style,
            "recommended_material": {
                "name": self.material_name,
                "appearance": self.material_appearance,
                "color": self.material_color,
            },
        }


@dataclass
class Component:
    id: str
    zone: str              # one of REQUIRED_ZONES
    system: str            # main_frame / roll_cage / suspension_mounts / …
    kind: str              # "tube" | "plate"
    type: str              # role label (lower_rail / roll_cage_bar / seat_mount …)
    od: float = 0.0        # tube outer diameter (mm)
    wall: float = 0.0      # tube wall thickness (mm)
    p1: tuple = (0.0, 0.0, 0.0)
    p2: tuple = (0.0, 0.0, 0.0)
    size: tuple = (0.0, 0.0, 0.0)   # plate w,d,h
    center: tuple = (0.0, 0.0, 0.0)
    side: str = "c"        # l | r | c
    mirrored_from: str | None = None
    group: str | None = None        # bent-member group (segments share a group)
    bolt_holes: int = 0             # plates: nominal bolt-hole count (metadata)

    @property
    def length(self) -> float:
        return math.dist(self.p1, self.p2) if self.kind == "tube" else 0.0

    def to_meta(self) -> dict:
        d = {
            "id": self.id, "name": self.id.replace("_", " "),
            "zone": self.zone, "system": self.system, "kind": self.kind,
            "type": self.type, "section": self.zone, "side": self.side,
        }
        if self.group:
            d["group"] = self.group
        if self.mirrored_from:
            d["mirrored_from"] = self.mirrored_from
        if self.kind == "tube":
            d.update(od=round(self.od, 2), wall=round(self.wall, 2),
                     cut_length_mm=round(self.length, 1),
                     p1=[round(v, 1) for v in self.p1], p2=[round(v, 1) for v in self.p2])
            d["anchor"] = [round((a + b) / 2, 1) for a, b in zip(self.p1, self.p2)]
        else:
            d.update(size=[round(v, 1) for v in self.size],
                     center=[round(v, 1) for v in self.center])
            d["anchor"] = [round(v, 1) for v in self.center]
            if self.bolt_holes:
                d["bolt_holes"] = self.bolt_holes
        return d


@dataclass
class ChassisBuild:
    solid: cq.Workplane
    components: list[Component]
    bbox_mm: dict
    envelope_mm: dict
    spec: ChassisSpec

    @property
    def tube_od(self) -> float:
        return self.spec.tube_outer_diameter_mm

    @property
    def tube_wall(self) -> float:
        return self.spec.tube_wall_thickness_mm

    @property
    def tube_count(self) -> int:
        return sum(1 for c in self.components if c.kind == "tube")

    @property
    def zones_present(self) -> list[str]:
        return sorted({c.zone for c in self.components})

    @property
    def systems_present(self) -> list[str]:
        return sorted({c.system for c in self.components})

    # Back-compat alias.
    @property
    def sections_present(self) -> list[str]:
        return self.zones_present


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
    length = min(max(length, 100.0), 12000.0)
    width = min(max(width, 100.0), 6000.0)
    height = min(max(height, 100.0), 6000.0)
    return length, width, height


# Keywords that upgrade a chassis prompt to the reference-grade layout.
_REFERENCE_KEYS = (
    "detailed", "roll cage", "rollcage", "welded", "suspension mount",
    "suspension mounting", "body panel", "space frame", "spaceframe", "buggy",
    "reference",
)


def detect_detail_level(prompt: str) -> str:
    t = (prompt or "").lower()
    return "reference" if any(k in t for k in _REFERENCE_KEYS) else "detailed"


def make_spec(prompt: str) -> ChassisSpec:
    L, W, H = parse_envelope(prompt)
    od = 40.0
    x_nose = -L / 2 + od
    x_fsus = -0.32 * L
    x_dash = -0.12 * L
    x_hoop = 0.14 * L
    x_rsus = 0.30 * L
    x_tail = L / 2 - od
    level = detect_detail_level(prompt)
    return ChassisSpec(
        length_mm=L, width_mm=W, height_mm=H,
        wheelbase_mm=x_rsus - x_fsus,
        front_overhang_mm=x_fsus - x_nose,
        rear_overhang_mm=x_tail - x_rsus,
        cabin_start_mm=x_dash, cabin_end_mm=x_hoop,
        tube_outer_diameter_mm=od, tube_wall_thickness_mm=2.5,
        cage_outer_diameter_mm=45.0, mount_plate_thickness_mm=6.0,
        design_detail_level=level, drive_layout="front_engine_rwd", seats=2,
        chassis_style=("reference_tubular_buggy_chassis" if level == "reference"
                       else "tubular_space_frame"),
    )


def _layout(spec: ChassisSpec) -> list[Component]:
    """Named node/edge space-frame for one envelope, with left/right symmetry."""
    L, W, H = spec.length_mm, spec.width_mm, spec.height_mm
    od, wall, cage = spec.tube_outer_diameter_mm, spec.tube_wall_thickness_mm, spec.cage_outer_diameter_mm
    pt = spec.mount_plate_thickness_mm

    hy = W * 0.45          # main rail half-width
    ty = W * 0.10          # tunnel half-width
    z_low = H * 0.08
    z_sill = H * 0.14
    z_simp = H * 0.28      # side-impact height
    z_up = H * 0.45        # upper rail (waist)
    z_dash = H * 0.58
    z_top = H * 0.95
    z_ws = H * 0.86        # windshield header

    # Longitudinal stations.
    x = {
        "nose": -L / 2 + od, "rad": -0.40 * L, "fsus": -0.32 * L,
        "dash": -0.12 * L, "seat": -0.02 * L, "hoop": 0.14 * L,
        "rsus": 0.30 * L, "tail": L / 2 - od,
    }

    nodes: dict[str, tuple] = {}

    def node(name: str, px: float, py: float, pz: float) -> None:
        nodes[name] = (px, py, pz)

    def node_pair(base: str, px: float, py: float, pz: float) -> None:
        node(f"{base}_l", px, py, pz)
        node(f"{base}_r", px, -py, pz)

    # Lower rail nodes (tapered nose + tail).
    lower = {
        "nose": (hy * 0.36, z_low + H * 0.10), "rad": (hy * 0.72, z_low + H * 0.02),
        "fsus": (hy * 0.92, z_low), "dash": (hy, z_low), "seat": (hy, z_low),
        "hoop": (hy, z_low), "rsus": (hy * 0.85, z_low), "tail": (hy * 0.74, z_low + H * 0.03),
    }
    for s, (py, pz) in lower.items():
        node_pair(f"lower_{s}", x[s], py, pz)

    # Upper rail nodes (waist), fsus → tail.
    upper = {
        "fsus": (hy * 0.9, z_up * 0.82), "dash": (hy, z_up), "seat": (hy, z_up),
        "hoop": (hy, z_up), "rsus": (hy * 0.82, z_up * 0.9), "tail": (hy * 0.72, z_up * 0.8),
    }
    for s, (py, pz) in upper.items():
        node_pair(f"upper_{s}", x[s], py, pz)

    # Roll-cage top nodes.
    node_pair("cage_ws", x["dash"], hy * 0.95, z_ws)
    node_pair("cage_main", x["hoop"], hy, z_top)
    # Tunnel nodes.
    node_pair("tunnel_dash", x["dash"], ty, z_low)
    node_pair("tunnel_hoop", x["hoop"], ty, z_low)
    # Side-impact + sill + dash bar + seat-rail nodes.
    node_pair("simp_dash", x["dash"], hy, z_simp)
    node_pair("simp_hoop", x["hoop"], hy, z_simp)
    node_pair("sill_dash", x["dash"], hy * 0.98, z_sill)
    node_pair("sill_hoop", x["hoop"], hy * 0.98, z_sill)
    node_pair("dashbar", x["dash"], hy * 0.95, z_dash)
    node_pair("seatrail_f", x["seat"] - L * 0.04, hy * 0.55, z_low)
    node_pair("seatrail_r", x["seat"] + L * 0.04, hy * 0.55, z_low)

    comps: list[Component] = []

    def tube(cid, zone, system, role, a, b, o=od, w=wall, side="c", mfrom=None):
        comps.append(Component(id=cid, zone=zone, system=system, kind="tube", type=role,
                               od=o, wall=w, p1=nodes[a], p2=nodes[b], side=side, mirrored_from=mfrom))

    def tube_pair(cid, zone, system, role, abase, bbase, o=od, w=wall):
        tube(f"{cid}_left", zone, system, role, f"{abase}_l", f"{bbase}_l", o, w, "l")
        tube(f"{cid}_right", zone, system, role, f"{abase}_r", f"{bbase}_r", o, w, "r", f"{cid}_left")

    def cross(cid, zone, system, role, base, o=od, w=wall):
        tube(cid, zone, system, role, f"{base}_l", f"{base}_r", o, w, "c")

    def plate(cid, zone, system, role, center, size, side="c", mfrom=None):
        comps.append(Component(id=cid, zone=zone, system=system, kind="plate", type=role,
                               center=tuple(center), size=tuple(size), side=side, mirrored_from=mfrom))

    def plate_pair(cid, zone, system, role, center, size):
        plate(f"{cid}_left", zone, system, role, center, size, "l")
        plate(f"{cid}_right", zone, system, role,
              (center[0], -center[1], center[2]), size, "r", f"{cid}_left")

    # --- lower longitudinal rails (per side, tapered) ---
    lower_segs = [
        ("nose", "rad", "front"), ("rad", "fsus", "front"),
        ("fsus", "dash", "engine_bay"), ("dash", "seat", "cabin"),
        ("seat", "hoop", "cabin"), ("hoop", "rsus", "rear"), ("rsus", "tail", "rear"),
    ]
    for a, b, zone in lower_segs:
        tube_pair(f"lower_rail_{a}_{b}", zone, "main_frame", "lower_rail",
                  f"lower_{a}", f"lower_{b}")

    # --- upper longitudinal rails (fsus → tail) ---
    upper_segs = [("fsus", "dash", "engine_bay"), ("dash", "seat", "cabin"),
                  ("seat", "hoop", "cabin"), ("hoop", "rsus", "rear"), ("rsus", "tail", "rear")]
    for a, b, zone in upper_segs:
        tube_pair(f"upper_rail_{a}_{b}", zone, "main_frame", "upper_rail",
                  f"upper_{a}", f"upper_{b}")

    # --- verticals lower → upper at shared stations ---
    for s, zone in (("fsus", "engine_bay"), ("dash", "cabin"), ("seat", "cabin"),
                    ("hoop", "cabin"), ("rsus", "rear"), ("tail", "rear")):
        tube_pair(f"vertical_{s}", zone, "main_frame", "vertical_strut",
                  f"lower_{s}", f"upper_{s}")

    # --- lower crossmembers (every station) ---
    cm_zone = {"nose": "front", "rad": "front", "fsus": "engine_bay", "dash": "cabin",
               "seat": "cabin", "hoop": "cabin", "rsus": "rear", "tail": "rear"}
    for s, zone in cm_zone.items():
        cross(f"lower_cross_{s}", zone, "main_frame", "cross_member", f"lower_{s}")

    # --- upper crossmembers ---
    for s in ("dash", "seat", "hoop", "tail"):
        zone = "cabin" if s in ("dash", "seat", "hoop") else "rear"
        cross(f"upper_cross_{s}", zone, "main_frame", "cross_member", f"upper_{s}")

    # --- transmission tunnel ---
    tube_pair("tunnel_rail", "cabin", "transmission_tunnel", "transmission_tunnel",
              "tunnel_dash", "tunnel_hoop")
    cross("tunnel_cross_dash", "cabin", "transmission_tunnel", "cross_member", "tunnel_dash")
    cross("tunnel_cross_hoop", "cabin", "transmission_tunnel", "cross_member", "tunnel_hoop")

    # --- roll cage ---
    tube_pair("windshield_post", "roll_cage", "roll_cage", "roll_cage_bar",
              "upper_dash", "cage_ws", cage, wall)
    cross("windshield_header", "roll_cage", "roll_cage", "roll_cage_bar", "cage_ws", cage, wall)
    tube_pair("main_hoop_post", "roll_cage", "roll_cage", "roll_cage_bar",
              "upper_hoop", "cage_main", cage, wall)
    cross("main_hoop_top", "roll_cage", "roll_cage", "roll_cage_bar", "cage_main", cage, wall)
    tube_pair("roof_rail", "roll_cage", "roll_cage", "roll_cage_bar",
              "cage_ws", "cage_main", cage, wall)
    tube_pair("rear_stay", "rear", "roll_cage", "roll_cage_bar",
              "cage_main", "upper_tail", cage, wall)
    cross("dashboard_support", "cabin", "main_frame", "dashboard_support", "dashbar")

    # --- triangulation / bracing ---
    def diag(cid, zone, a, b):
        tube(cid, zone, "main_frame", "diagonal_brace", a, b, side="c")

    # front + engine + cabin + rear floor X-braces
    for cid, zone, a, b in [
        ("front_floor_x_a", "front", "lower_rad_l", "lower_fsus_r"),
        ("front_floor_x_b", "front", "lower_rad_r", "lower_fsus_l"),
        ("engine_floor_x_a", "engine_bay", "lower_fsus_l", "lower_dash_r"),
        ("engine_floor_x_b", "engine_bay", "lower_fsus_r", "lower_dash_l"),
        ("cabin_floor_x_a", "cabin", "lower_seat_l", "lower_hoop_r"),
        ("cabin_floor_x_b", "cabin", "lower_seat_r", "lower_hoop_l"),
        ("rear_floor_x_a", "rear", "lower_rsus_l", "lower_tail_r"),
        ("rear_floor_x_b", "rear", "lower_rsus_r", "lower_tail_l"),
    ]:
        diag(cid, zone, a, b)
    # front side X (per side)
    tube_pair("front_side_x_fwd", "engine_bay", "main_frame", "diagonal_brace",
              "lower_fsus", "upper_dash")
    tube_pair("front_side_x_aft", "engine_bay", "main_frame", "diagonal_brace",
              "upper_fsus", "lower_dash")
    # rear side diagonals (per side)
    tube_pair("rear_side_x_fwd", "rear", "main_frame", "diagonal_brace",
              "lower_hoop", "upper_rsus")
    tube_pair("rear_side_x_aft", "rear", "main_frame", "diagonal_brace",
              "upper_rsus", "lower_tail")

    # --- side-impact structure + sills ---
    tube_pair("side_impact_bar", "cabin", "side_impact", "side_impact_bar",
              "simp_dash", "simp_hoop")
    tube_pair("door_sill", "cabin", "side_impact", "door_sill", "sill_dash", "sill_hoop")
    # --- seat rails ---
    tube_pair("seat_rail", "cabin", "seat_mounts", "seat_rail", "seatrail_f", "seatrail_r")

    # --- mount plates / brackets / gussets ---
    plate_size = (W * 0.07, W * 0.05, pt)
    tab = (W * 0.045, W * 0.035, pt)
    gus = (od * 1.8, od * 1.8, pt)

    plate_pair("engine_mount", "engine_bay", "engine_mounts", "engine_mount",
               (-0.28 * L, hy * 0.55, z_low + H * 0.04), plate_size)
    plate("transmission_mount", "cabin", "engine_mounts", "transmission_mount",
          (x["seat"], 0.0, z_low + H * 0.03), plate_size)
    plate_pair("front_lower_arm_tab", "front", "suspension_mounts", "suspension_tab",
               (x["fsus"], hy * 0.98, z_low + H * 0.02), tab)
    plate_pair("front_upper_arm_tab", "front", "suspension_mounts", "suspension_tab",
               (x["fsus"], hy * 0.9, z_up * 0.82), tab)
    plate_pair("rear_lower_arm_tab", "rear", "suspension_mounts", "suspension_tab",
               (x["rsus"], hy * 0.9, z_low + H * 0.02), tab)
    plate_pair("rear_upper_arm_tab", "rear", "suspension_mounts", "suspension_tab",
               (x["rsus"], hy * 0.82, z_up * 0.9), tab)
    plate_pair("front_shock_tower", "front", "suspension_mounts", "shock_tower",
               (x["fsus"] + L * 0.02, hy * 0.7, z_up), plate_size)
    plate_pair("rear_shock_tower", "rear", "suspension_mounts", "shock_tower",
               (x["rsus"] - L * 0.02, hy * 0.66, z_up * 0.9), plate_size)
    plate_pair("radiator_mount", "front", "radiator_mount", "radiator_mount",
               (x["rad"], hy * 0.45, z_low + H * 0.10), tab)
    plate("fuel_tank_cradle", "rear", "fuel_tank_mount", "fuel_tank_cradle",
          (0.24 * L, 0.0, z_low + H * 0.02), (W * 0.30, W * 0.16, pt))
    plate_pair("fuel_tank_tab", "rear", "fuel_tank_mount", "fuel_tank_tab",
               (0.24 * L, hy * 0.6, z_low + H * 0.04), tab)
    plate("steering_column_bracket", "cabin", "seat_mounts", "steering_column_bracket",
          (x["dash"], W * 0.16, z_dash), tab)
    plate_pair("body_panel_tab_front", "front", "main_frame", "body_panel_tab",
               (x["rad"], hy * 0.8, z_up * 0.7), tab)
    plate_pair("body_panel_tab_rear", "rear", "main_frame", "body_panel_tab",
               (x["rsus"], hy * 0.8, z_up * 0.7), tab)
    plate_pair("seat_mount", "cabin", "seat_mounts", "seat_mount",
               (x["seat"], hy * 0.5, z_low + H * 0.02), plate_size)
    plate_pair("gusset_main_hoop", "roll_cage", "roll_cage", "gusset",
               (x["hoop"], hy, z_up + H * 0.04), gus)
    plate_pair("gusset_windshield", "roll_cage", "roll_cage", "gusset",
               (x["dash"], hy, z_up + H * 0.04), gus)
    plate_pair("gusset_front_susp", "engine_bay", "main_frame", "gusset",
               (x["fsus"], hy, z_low + H * 0.06), gus)

    return comps


def _layout_reference(spec: ChassisSpec) -> list[Component]:
    """Reference-grade buggy/sports-car space frame: tapered nose, curved (bent)
    rails built from grouped segments, full roll cage + roof, dense side
    triangulation, and a rich set of mount plates/tabs/gussets with bolt holes.
    Left/right symmetric."""
    L, W, H = spec.length_mm, spec.width_mm, spec.height_mm
    od, wall, cage = spec.tube_outer_diameter_mm, spec.tube_wall_thickness_mm, spec.cage_outer_diameter_mm
    pt = spec.mount_plate_thickness_mm

    hy = W * 0.46
    ty = W * 0.11
    z_low = H * 0.07
    z_mid = H * 0.30
    z_up = H * 0.46
    z_dash = H * 0.58
    z_ws = H * 0.84
    z_top = H * 0.95

    x = {"nose": -L / 2 + od, "rad": -0.41 * L, "fsus": -0.33 * L, "dash": -0.13 * L,
         "seat": -0.02 * L, "hoop": 0.13 * L, "rsus": 0.29 * L, "tail": L / 2 - od}
    wf = {"nose": 0.34, "rad": 0.60, "fsus": 0.86, "dash": 1.0, "seat": 1.0,
          "hoop": 1.0, "rsus": 0.84, "tail": 0.64}
    lz = {"nose": z_low + H * 0.14, "rad": z_low + H * 0.05, "fsus": z_low, "dash": z_low,
          "seat": z_low, "hoop": z_low, "rsus": z_low, "tail": z_low + H * 0.05}
    uz = {"fsus": z_up * 0.78, "dash": z_up, "seat": z_up, "hoop": z_up,
          "rsus": z_up * 0.9, "tail": z_up * 0.76}
    zone = {"nose": "front", "rad": "front", "fsus": "engine_bay", "dash": "cabin",
            "seat": "cabin", "hoop": "cabin", "rsus": "rear", "tail": "rear"}

    N: dict[str, tuple] = {}

    def node(name, px, py, pz):
        N[name] = (px, py, pz)

    def node_pair(base, px, py, pz):
        node(f"{base}_l", px, py, pz)
        node(f"{base}_r", px, -py, pz)

    for s in x:
        node_pair(f"low_{s}", x[s], hy * wf[s], lz[s])
    for s in ("dash", "seat", "hoop"):
        node_pair(f"mid_{s}", x[s], hy * wf[s] * 0.99, z_mid)
    for s in uz:
        node_pair(f"up_{s}", x[s], hy * wf[s], uz[s])
    node_pair("nosetop", x["nose"] + (x["rad"] - x["nose"]) * 0.45, hy * 0.30, z_low + H * 0.20)
    node_pair("ws", x["dash"], hy * 0.96, z_ws)
    node_pair("main", x["hoop"], hy, z_top)
    node_pair("roofmid", (x["dash"] + x["hoop"]) / 2, hy * 0.98, z_top * 0.99)
    node_pair("tailtop", x["tail"] - (x["tail"] - x["rsus"]) * 0.15, hy * 0.60, z_up * 0.86)
    node_pair("tun_dash", x["dash"], ty, z_low)
    node_pair("tun_seat", x["seat"], ty, z_low)
    node_pair("tun_hoop", x["hoop"], ty, z_low)
    node_pair("harness", x["hoop"], hy, (z_up + z_top) / 2)
    node_pair("dashbar", x["dash"], hy * 0.95, z_dash)
    node_pair("seatrail_f", x["seat"] - L * 0.045, hy * 0.5, z_low)
    node_pair("seatrail_b", x["seat"] + L * 0.045, hy * 0.5, z_low)

    comps: list[Component] = []

    def tube(cid, zn, system, role, a, b, o=od, w=wall, side="c", group=None, mfrom=None):
        comps.append(Component(id=cid, zone=zn, system=system, kind="tube", type=role,
                               od=o, wall=w, p1=N[a], p2=N[b], side=side, group=group,
                               mirrored_from=mfrom))

    def tube_pair(cid, zn, system, role, a, b, o=od, w=wall, group=None):
        tube(f"{cid}_left", zn, system, role, f"{a}_l", f"{b}_l", o, w, "l", group)
        tube(f"{cid}_right", zn, system, role, f"{a}_r", f"{b}_r", o, w, "r", group, f"{cid}_left")

    def cross(cid, zn, system, role, base, o=od, w=wall, group=None):
        tube(cid, zn, system, role, f"{base}_l", f"{base}_r", o, w, "c", group)

    def plate(cid, zn, system, role, center, size, side="c", holes=0, mfrom=None):
        comps.append(Component(id=cid, zone=zn, system=system, kind="plate", type=role,
                               center=tuple(center), size=tuple(size), side=side,
                               bolt_holes=holes, mirrored_from=mfrom))

    def plate_pair(cid, zn, system, role, center, size, holes=0):
        plate(f"{cid}_left", zn, system, role, center, size, "l", holes)
        plate(f"{cid}_right", zn, system, role, (center[0], -center[1], center[2]),
              size, "r", holes, f"{cid}_left")

    # --- bent longitudinal rails (grouped segments) ---
    lower_stations = ["nose", "rad", "fsus", "dash", "seat", "hoop", "rsus", "tail"]
    for a, b in zip(lower_stations, lower_stations[1:]):
        tube_pair(f"side_lower_rail_{a}_{b}", zone[a], "main_frame", "lower_rail",
                  f"low_{a}", f"low_{b}", group="side_lower_rail")
    upper_stations = ["fsus", "dash", "seat", "hoop", "rsus", "tail"]
    for a, b in zip(upper_stations, upper_stations[1:]):
        tube_pair(f"side_upper_rail_{a}_{b}", zone[a], "main_frame", "upper_rail",
                  f"up_{a}", f"up_{b}", group="side_upper_rail")
    for a, b in (("dash", "seat"), ("seat", "hoop")):
        tube_pair(f"side_impact_{a}_{b}", "cabin", "side_impact", "side_impact_bar",
                  f"mid_{a}", f"mid_{b}", group="side_impact_rail")

    # --- tapered nose perimeter (bent) ---
    nose_path = ["low_fsus", "low_rad", "low_nose", "nosetop", "up_fsus"]
    for a, b in zip(nose_path, nose_path[1:]):
        tube_pair(f"nose_perimeter_{a}_{b}", "front", "main_frame", "nose_perimeter",
                  a, b, group="front_nose_perimeter")
    cross("nose_tip_cross", "front", "main_frame", "nose_perimeter", "low_nose")
    cross("nose_top_cross", "front", "main_frame", "nose_perimeter", "nosetop")
    tube_pair("nose_crash_diag", "front", "main_frame", "diagonal_brace",
              "low_nose", "low_rad", group="front_crash")
    tube_pair("nose_to_cockpit_diag", "front", "main_frame", "diagonal_brace",
              "low_rad", "up_fsus", group="front_crash")

    # --- roof / cage ---
    for a, b in (("ws", "roofmid"), ("roofmid", "main")):
        tube_pair(f"roof_rail_{a}_{b}", "roll_cage", "roll_cage", "roof_rail",
                  a, b, cage, wall, group="roof_rail")
    cross("roof_cross_ws", "roll_cage", "roll_cage", "roof_crossbar", "ws", cage, wall)
    cross("roof_cross_mid", "roll_cage", "roll_cage", "roof_crossbar", "roofmid", cage, wall)
    cross("roof_cross_main", "roll_cage", "roll_cage", "roof_crossbar", "main", cage, wall)
    tube("roof_diag_a", "roll_cage", "roll_cage", "roof_diagonal", "ws_l", "main_r", cage, wall)
    tube("roof_diag_b", "roll_cage", "roll_cage", "roof_diagonal", "ws_r", "main_l", cage, wall)
    tube_pair("a_pillar", "roll_cage", "roll_cage", "roll_cage_bar",
              "up_dash", "ws", cage, wall, group="windshield_hoop")
    tube_pair("main_hoop_post", "roll_cage", "roll_cage", "roll_cage_bar",
              "up_hoop", "main", cage, wall, group="main_hoop")
    cross("harness_bar", "roll_cage", "roll_cage", "roll_cage_bar", "harness", cage, wall)
    tube_pair("rear_stay", "rear", "roll_cage", "roll_cage_bar",
              "main", "up_tail", cage, wall, group="rear_stay")

    # --- rear hoop (bent) ---
    for a, b in (("low_tail_l", "up_tail_l"), ("up_tail_l", "tailtop_l"),
                 ("tailtop_l", "tailtop_r"), ("tailtop_r", "up_tail_r"),
                 ("up_tail_r", "low_tail_r")):
        side = "l" if a.endswith("_l") and b.endswith("_l") else ("r" if a.endswith("_r") else "c")
        tube(f"rear_hoop_{a}_{b}", "rear", "roll_cage", "rear_hoop", a, b, cage, wall, side,
             group="rear_hoop")
    tube_pair("rear_bumper_diag", "rear", "main_frame", "diagonal_brace",
              "low_tail", "low_rsus", group="rear_bumper")

    # --- verticals ---
    for s in ("dash", "seat", "hoop"):
        tube_pair(f"vert_low_mid_{s}", zone[s], "main_frame", "vertical_strut",
                  f"low_{s}", f"mid_{s}", group="vertical")
        tube_pair(f"vert_mid_up_{s}", zone[s], "main_frame", "vertical_strut",
                  f"mid_{s}", f"up_{s}", group="vertical")
    for s in ("fsus", "rsus", "tail"):
        tube_pair(f"vert_low_up_{s}", zone[s], "main_frame", "vertical_strut",
                  f"low_{s}", f"up_{s}", group="vertical")

    # --- crossmembers ---
    for s in lower_stations:
        cross(f"lower_cross_{s}", zone[s], "main_frame", "cross_member", f"low_{s}", group="lower_cross")
    for s in ("dash", "seat", "hoop"):
        cross(f"mid_cross_{s}", "cabin", "main_frame", "cross_member", f"mid_{s}", group="mid_cross")
    for s in ("dash", "seat", "hoop", "tail"):
        cross(f"upper_cross_{s}", zone[s], "main_frame", "cross_member", f"up_{s}", group="upper_cross")

    # --- transmission tunnel ---
    for a, b in (("tun_dash", "tun_seat"), ("tun_seat", "tun_hoop")):
        tube(f"tunnel_rail_l_{a}", "cabin", "transmission_tunnel", "transmission_tunnel",
             f"{a}_l", f"{b}_l", side="l", group="tunnel")
        tube(f"tunnel_rail_r_{a}", "cabin", "transmission_tunnel", "transmission_tunnel",
             f"{a}_r", f"{b}_r", side="r", group="tunnel", mfrom=f"tunnel_rail_l_{a}")
    for s in ("tun_dash", "tun_seat", "tun_hoop"):
        cross(f"tunnel_cross_{s}", "cabin", "transmission_tunnel", "cross_member", s, group="tunnel")

    # --- floor X-bracing (center) ---
    bays = [("rad", "fsus"), ("fsus", "dash"), ("dash", "seat"),
            ("seat", "hoop"), ("hoop", "rsus"), ("rsus", "tail")]
    for a, b in bays:
        tube(f"floor_x_{a}_{b}_a", zone[a], "main_frame", "diagonal_brace",
             f"low_{a}_l", f"low_{b}_r", group="floor_xbrace")
        if a in ("dash", "seat"):  # extra crossing brace in the cabin floor
            tube(f"floor_x_{a}_{b}_b", zone[a], "main_frame", "diagonal_brace",
                 f"low_{a}_r", f"low_{b}_l", group="floor_xbrace")

    # --- side triangulation (per side) ---
    side_bays = [("fsus", "dash", True), ("dash", "seat", True), ("seat", "hoop", True),
                 ("hoop", "rsus", False), ("rsus", "tail", False)]
    for a, b, full in side_bays:
        tube_pair(f"side_brace_{a}_{b}_a", zone[a], "main_frame", "diagonal_brace",
                  f"low_{a}", f"up_{b}", group="side_brace")
        if full:
            tube_pair(f"side_brace_{a}_{b}_b", zone[a], "main_frame", "diagonal_brace",
                      f"up_{a}", f"low_{b}", group="side_brace")

    # --- dashboard + steering ---
    cross("dashboard_support", "cabin", "main_frame", "dashboard_support", "dashbar")
    tube("steering_brace", "cabin", "main_frame", "diagonal_brace", "dashbar_l", "up_dash_l", side="l")

    # --- seat rails ---
    tube_pair("seat_rail", "cabin", "seat_mounts", "seat_rail", "seatrail_f", "seatrail_b",
              group="seat_rail")

    # --- mount plates / tabs / gussets (with bolt holes) ---
    big = (W * 0.07, W * 0.05, pt)
    tab = (W * 0.05, W * 0.04, pt)
    gus = (od * 1.9, od * 1.9, pt)
    side_plate = (L * 0.06, W * 0.03, pt)

    plate("front_bulkhead_plate", "front", "main_frame", "front_bulkhead",
          (x["rad"], 0.0, z_low + H * 0.12), (W * 0.30, W * 0.18, pt), holes=6)
    plate_pair("engine_mount", "engine_bay", "engine_mounts", "engine_mount",
               (-0.28 * L, hy * 0.55, z_low + H * 0.05), big, holes=4)
    plate("transmission_mount", "cabin", "engine_mounts", "transmission_mount",
          (x["seat"], 0.0, z_low + H * 0.04), big, holes=4)
    plate_pair("front_lower_arm_tab", "front", "suspension_mounts", "suspension_tab",
               (x["fsus"], hy * 0.97, z_low + H * 0.03), tab, holes=2)
    plate_pair("front_upper_arm_tab", "front", "suspension_mounts", "suspension_tab",
               (x["fsus"], hy * 0.9, z_up * 0.78), tab, holes=2)
    plate_pair("rear_lower_arm_tab", "rear", "suspension_mounts", "suspension_tab",
               (x["rsus"], hy * 0.9, z_low + H * 0.03), tab, holes=2)
    plate_pair("rear_upper_arm_tab", "rear", "suspension_mounts", "suspension_tab",
               (x["rsus"], hy * 0.82, z_up * 0.9), tab, holes=2)
    plate_pair("rear_trailing_arm_tab", "rear", "suspension_mounts", "suspension_tab",
               (x["rsus"] - L * 0.04, hy * 0.7, z_low + H * 0.02), tab, holes=2)
    plate_pair("front_shock_tower", "front", "suspension_mounts", "shock_tower",
               (x["fsus"] + L * 0.02, hy * 0.68, z_up), big, holes=3)
    plate_pair("rear_shock_tower", "rear", "suspension_mounts", "shock_tower",
               (x["rsus"] - L * 0.02, hy * 0.64, z_up * 0.9), big, holes=3)
    plate_pair("radiator_mount", "front", "radiator_mount", "radiator_mount",
               (x["rad"], hy * 0.42, z_low + H * 0.12), tab, holes=2)
    plate("fuel_tank_cradle", "rear", "fuel_tank_mount", "fuel_tank_cradle",
          (0.23 * L, 0.0, z_low + H * 0.03), (W * 0.30, W * 0.18, pt), holes=4)
    plate_pair("fuel_tank_tab", "rear", "fuel_tank_mount", "fuel_tank_tab",
               (0.23 * L, hy * 0.6, z_low + H * 0.05), tab, holes=2)
    plate("steering_column_bracket", "cabin", "seat_mounts", "steering_column_bracket",
          (x["dash"], W * 0.16, z_dash), tab, holes=2)
    plate_pair("seat_base_plate", "cabin", "seat_mounts", "seat_base_plate",
               (x["seat"], hy * 0.45, z_low + H * 0.02), (W * 0.16, W * 0.20, pt), holes=4)
    plate_pair("floor_panel", "cabin", "seat_mounts", "floor_panel",
               (x["seat"], hy * 0.22, z_low - H * 0.005), (L * 0.16, W * 0.18, pt * 0.6), holes=2)
    plate_pair("harness_tab", "cabin", "seat_mounts", "harness_tab",
               (x["hoop"], hy * 0.5, (z_up + z_top) / 2), tab, holes=2)
    plate_pair("body_panel_tab_front", "front", "main_frame", "body_panel_tab",
               (x["rad"], hy * 0.82, z_up * 0.7), tab, holes=2)
    plate_pair("body_panel_tab_rear", "rear", "main_frame", "body_panel_tab",
               (x["rsus"], hy * 0.82, z_up * 0.7), tab, holes=2)
    # Side mounting plates near the sills (front/cabin/rear) — like the reference.
    plate_pair("side_mount_plate_front", "engine_bay", "main_frame", "side_mount_plate",
               (x["fsus"], hy * 0.96, z_low + H * 0.05), side_plate, holes=3)
    plate_pair("side_mount_plate_mid", "cabin", "main_frame", "side_mount_plate",
               (x["seat"], hy * 0.99, z_low + H * 0.05), side_plate, holes=3)
    plate_pair("side_mount_plate_rear", "rear", "main_frame", "side_mount_plate",
               (x["rsus"], hy * 0.85, z_low + H * 0.05), side_plate, holes=3)
    # Gussets at key joints.
    plate_pair("gusset_main_hoop", "roll_cage", "roll_cage", "gusset",
               (x["hoop"], hy, z_up + H * 0.05), gus)
    plate_pair("gusset_windshield", "roll_cage", "roll_cage", "gusset",
               (x["dash"], hy * 0.96, z_up + H * 0.05), gus)
    plate_pair("gusset_front_susp", "engine_bay", "main_frame", "gusset",
               (x["fsus"], hy, z_low + H * 0.07), gus)
    plate_pair("gusset_rear_susp", "rear", "main_frame", "gusset",
               (x["rsus"], hy * 0.85, z_low + H * 0.07), gus)

    return comps


def _unit(v: tuple) -> tuple[tuple, float]:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-9:
        return (0.0, 0.0, 1.0), 0.0
    return (v[0] / n, v[1] / n, v[2] / n), n


def _tube_solid(c: Component):
    """Closed SOLID cylinder along the edge (clean watertight export)."""
    d = (c.p2[0] - c.p1[0], c.p2[1] - c.p1[1], c.p2[2] - c.p1[2])
    u, length = _unit(d)
    if length < 1e-6 or c.od <= 0:
        return None
    plane = cq.Plane(origin=tuple(c.p1), normal=tuple(u))
    return cq.Workplane(plane).circle(c.od / 2).extrude(length).val()


def _plate_solid(c: Component):
    w, d, h = c.size
    if min(w, d) <= 0:
        return None
    thk = max(h, 1.0)
    wp = cq.Workplane("XY").box(w, d, thk)
    n = int(c.bolt_holes)
    if n > 0:
        dia = max(2.0, min(w, d) * 0.16)
        span = w - 2 * (dia + 2)
        if span > 0 and n > 1:
            xs = [-span / 2 + span * i / (n - 1) for i in range(n)]
        else:
            xs = [0.0]
        try:
            wp = wp.faces(">Z").workplane().pushPoints([(hx, 0.0) for hx in xs]).hole(dia)
        except Exception:  # noqa: BLE001 - keep the plate if holes can't be cut
            wp = cq.Workplane("XY").box(w, d, thk)
    return wp.translate(tuple(c.center)).val()


def build_chassis(prompt: str) -> ChassisBuild:
    """Build the chassis assembly as a single compound (multi-body).

    Reference-grade prompts get the dense ``_layout_reference`` frame; others get
    the lighter ``_layout``."""
    spec = make_spec(prompt)
    components = (_layout_reference(spec) if spec.design_detail_level == "reference"
                 else _layout(spec))

    solids, built = [], 0
    for c in components:
        s = _tube_solid(c) if c.kind == "tube" else _plate_solid(c)
        if s is not None:
            solids.append(s)
            built += 1
    if not solids:
        raise CadGenerationError("chassis assembly produced no geometry")
    if built != len(components):
        raise CadGenerationError(
            f"chassis assembly has {len(components) - built} zero-volume components"
        )

    compound = cq.Compound.makeCompound(solids)
    wp = cq.Workplane("XY").add(compound)
    bb = wp.val().BoundingBox()
    bbox = {"x": round(bb.xlen, 1), "y": round(bb.ylen, 1), "z": round(bb.zlen, 1)}
    return ChassisBuild(
        solid=wp, components=components, bbox_mm=bbox,
        envelope_mm={"x": spec.length_mm, "y": spec.width_mm, "z": spec.height_mm},
        spec=spec,
    )
