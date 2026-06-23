"""Deterministic structural-frame & concept-assembly generators.

A reusable builder for square/round tube frames and a handful of concrete
families (machine frame, engine test stand, drone frame, motorcycle rear
subframe, electric-skateboard motor mount). All are CONCEPT CAD:

* tubes export as solid cylinders, square tubing as solid rectangular beams;
  real wall thickness is carried as cut-list metadata, not modelled as a hollow
  section;
* node/joints are idealized — no weld prep, load-driven sizing, or FEA;
* nothing here executes LLM code and nothing makes structural claims.

Coordinate frame: x = length, y = width (symmetric about y=0), z = height
(ground at z≈0, up +z). Geometry is built with CadQuery and fused into a single
compound (assembly) or a single solid (a one-piece component).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cadquery as cq

from app.cad.base import CadGenerationError


# --- envelope parsing (shared with the chassis generator's conventions) -----
def parse_lwh(prompt: str, defaults: tuple[float, float, float]) -> tuple[float, float, float]:
    t = (prompt or "").lower()

    def near(*labels) -> float | None:
        for lab in labels:
            m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:[a-z ]{0,12}?)" + lab, t)
            if m:
                return float(m.group(1))
        return None

    length = near("long", "length") or defaults[0]
    width = near("wide", "width") or defaults[1]
    height = near("high", "height", "tall") or defaults[2]
    length = min(max(length, 50.0), 12000.0)
    width = min(max(width, 50.0), 6000.0)
    height = min(max(height, 50.0), 6000.0)
    return length, width, height


def parse_tube_size(prompt: str, default: float) -> float:
    """Square tube side / round tube OD in mm."""
    t = (prompt or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:square|round)?\s*(?:steel\s*)?(?:tub|pipe)", t)
    if m:
        return min(max(float(m.group(1)), 8.0), 200.0)
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:square|od)", t)
    if m:
        return min(max(float(m.group(1)), 8.0), 200.0)
    return default


# --- members ----------------------------------------------------------------
@dataclass
class Member:
    id: str
    role: str                 # leg / top_rail / brace / foot_plate / motor_plate / arm / ...
    system: str               # frame / legs / braces / mounts / panels / arms / rails ...
    kind: str                 # "beam" | "tube" | "plate"
    p1: tuple = (0.0, 0.0, 0.0)
    p2: tuple = (0.0, 0.0, 0.0)
    section: tuple = (0.0, 0.0)        # beam cross-section (w, h) mm
    od: float = 0.0                    # tube outer diameter mm
    wall: float = 0.0                  # metadata only
    center: tuple = (0.0, 0.0, 0.0)
    size: tuple = (0.0, 0.0, 0.0)      # plate (w, d, h)
    bolt_holes: int = 0
    hole_dia: float = 0.0
    bore_dia: float = 0.0
    side: str = "c"
    mirrored_from: str | None = None

    @property
    def length(self) -> float:
        if self.kind in ("beam", "tube"):
            return math.dist(self.p1, self.p2)
        return 0.0

    def to_meta(self) -> dict:
        d = {"id": self.id, "name": self.id.replace("_", " "), "role": self.role,
             "system": self.system, "kind": self.kind, "side": self.side}
        if self.mirrored_from:
            d["mirrored_from"] = self.mirrored_from
        if self.kind in ("beam", "tube"):
            d.update(cut_length_mm=round(self.length, 1),
                     p1=[round(v, 1) for v in self.p1], p2=[round(v, 1) for v in self.p2])
            d["anchor"] = [round((a + b) / 2, 1) for a, b in zip(self.p1, self.p2)]
            if self.kind == "beam":
                d["section_mm"] = [round(v, 1) for v in self.section]
            else:
                d.update(od=round(self.od, 2), wall=round(self.wall, 2))
        else:
            d.update(size=[round(v, 1) for v in self.size],
                     center=[round(v, 1) for v in self.center])
            d["anchor"] = [round(v, 1) for v in self.center]
            if self.bolt_holes:
                d["bolt_holes"] = self.bolt_holes
            if self.bore_dia:
                d["bore_dia_mm"] = round(self.bore_dia, 1)
        return d


@dataclass
class AssemblyBuild:
    solid: cq.Workplane
    members: list[Member]
    bbox_mm: dict
    envelope_mm: dict
    family_id: str
    display_name: str
    design_mode: str          # "assembly" | "single_part"
    profile: str              # validation profile id
    meta: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    fused: bool = False
    decomposition_note: str | None = None

    @property
    def member_count(self) -> int:
        return len(self.members)

    def roles_present(self) -> set[str]:
        return {m.role for m in self.members}

    def total_holes(self) -> int:
        return sum(m.bolt_holes for m in self.members if m.kind == "plate")


# --- geometry builders ------------------------------------------------------
def _unit(d: tuple) -> tuple[tuple, float]:
    n = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
    if n < 1e-9:
        return (0.0, 0.0, 1.0), 0.0
    return (d[0] / n, d[1] / n, d[2] / n), n


def _beam_solid(m: Member):
    d = (m.p2[0] - m.p1[0], m.p2[1] - m.p1[1], m.p2[2] - m.p1[2])
    u, length = _unit(d)
    w, h = m.section
    if length < 1e-6 or min(w, h) <= 0:
        return None
    plane = cq.Plane(origin=tuple(m.p1), normal=tuple(u))
    return cq.Workplane(plane).rect(w, h).extrude(length).val()


def _tube_solid(m: Member):
    d = (m.p2[0] - m.p1[0], m.p2[1] - m.p1[1], m.p2[2] - m.p1[2])
    u, length = _unit(d)
    if length < 1e-6 or m.od <= 0:
        return None
    plane = cq.Plane(origin=tuple(m.p1), normal=tuple(u))
    return cq.Workplane(plane).circle(m.od / 2).extrude(length).val()


def _plate_solid(m: Member):
    w, d, h = m.size
    if min(w, d) <= 0:
        return None
    thk = max(h, 1.0)
    wp = cq.Workplane("XY").box(w, d, thk)
    if m.bore_dia and m.bore_dia > 0:
        try:
            wp = wp.faces(">Z").workplane().hole(min(m.bore_dia, min(w, d) * 0.9))
        except Exception:  # noqa: BLE001 - keep the plate if the bore can't be cut
            pass
    n = int(m.bolt_holes)
    if n > 0:
        dia = m.hole_dia if m.hole_dia > 0 else max(3.0, min(w, d) * 0.12)
        pts = _hole_points(w, d, n)
        try:
            wp = wp.faces(">Z").workplane().pushPoints(pts).hole(dia)
        except Exception:  # noqa: BLE001 - keep the plate if holes can't be cut
            pass
    return wp.translate(tuple(m.center)).val()


def _hole_points(w: float, d: float, n: int) -> list[tuple]:
    """Bolt-hole layout on a plate: 4 -> corners, else an even row on the long axis."""
    ix, iy = w / 2 - max(6.0, w * 0.1), d / 2 - max(6.0, d * 0.1)
    if n == 4:
        return [(-ix, -iy), (ix, -iy), (-ix, iy), (ix, iy)]
    if n == 2:
        return [(-ix, 0.0), (ix, 0.0)]
    if n == 1:
        return [(0.0, 0.0)]
    span = 2 * ix
    return [(-ix + span * i / (n - 1), 0.0) for i in range(n)]


def assemble(members: list[Member], *, family_id: str, display_name: str,
             design_mode: str, profile: str, envelope_mm: dict, meta: dict,
             requirements: dict, notes: list[str], fused: bool = False,
             decomposition_note: str | None = None) -> AssemblyBuild:
    """Build every member into a solid, fuse into a compound (or one solid), and
    measure the real bounding box."""
    solids, built = [], 0
    for m in members:
        if m.kind == "beam":
            s = _beam_solid(m)
        elif m.kind == "tube":
            s = _tube_solid(m)
        else:
            s = _plate_solid(m)
        if s is not None:
            solids.append(s)
            built += 1
    if not solids:
        raise CadGenerationError(f"{family_id} produced no geometry")
    if built != len(members):
        raise CadGenerationError(
            f"{family_id} has {len(members) - built} zero-volume members")

    if fused:
        wp = cq.Workplane("XY").add(solids[0])
        for s in solids[1:]:
            wp = wp.union(cq.Workplane("XY").add(s))
    else:
        compound = cq.Compound.makeCompound(solids)
        wp = cq.Workplane("XY").add(compound)
    bb = wp.val().BoundingBox()
    bbox = {"x": round(bb.xlen, 1), "y": round(bb.ylen, 1), "z": round(bb.zlen, 1)}
    return AssemblyBuild(
        solid=wp, members=members, bbox_mm=bbox, envelope_mm=envelope_mm,
        family_id=family_id, display_name=display_name, design_mode=design_mode,
        profile=profile, meta=meta, requirements=requirements, notes=notes,
        fused=fused, decomposition_note=decomposition_note,
    )


_CONCEPT_NOTES = [
    "Concept CAD only. Not FEA analyzed. Not structurally certified. Requires "
    "engineering review before fabrication.",
]

# Stated on every frame so the small, expected bbox vs requested difference is
# never an unexplained surprise.
_ENVELOPE_NOTE = (
    "Requested dimensions are treated as the outside envelope; the measured "
    "bounding box may exceed them by up to one tube/extrusion width (~{s:g}mm "
    "per axis) because members are solid."
)


# --- helpers to lay out a rectangular tube frame ----------------------------
def _rect_frame_beams(prefix: str, system: str, z: float, L: float, W: float,
                      s: float, role: str) -> list[Member]:
    """Four beams forming a rectangle at height z (perimeter of L×W)."""
    hx, hy = L / 2, W / 2
    sec = (s, s)
    return [
        Member(f"{prefix}_front", role, system, "beam", (-hx, -hy, z), (-hx, hy, z), section=sec),
        Member(f"{prefix}_rear", role, system, "beam", (hx, -hy, z), (hx, hy, z), section=sec),
        Member(f"{prefix}_left", role, system, "beam", (-hx, hy, z), (hx, hy, z), section=sec),
        Member(f"{prefix}_right", role, system, "beam", (-hx, -hy, z), (hx, -hy, z), section=sec),
    ]


def _legs(L: float, W: float, H: float, s: float) -> list[Member]:
    hx, hy = L / 2, W / 2
    sec = (s, s)
    corners = [("fl", -hx, hy), ("fr", -hx, -hy), ("rl", hx, hy), ("rr", hx, -hy)]
    return [Member(f"leg_{name}", "leg", "legs", "beam", (x, y, 0), (x, y, H), section=sec)
            for name, x, y in corners]


def _foot_plates(L: float, W: float, s: float, bolt_d: float) -> list[Member]:
    hx, hy = L / 2, W / 2
    fp = max(2.5 * s, 70.0)
    # Inset each foot pad so its OUTER edge aligns with the leg's outer face
    # (leg outer = hx + s/2). This keeps the leveling feet under the legs instead
    # of overhanging by ~fp/2, so the measured outside envelope ≈ requested + one
    # tube width (not requested + a whole foot-plate width).
    off = (fp - s) / 2
    cx, cy = hx - off, hy - off
    corners = [("fl", -cx, cy), ("fr", -cx, -cy), ("rl", cx, cy), ("rr", cx, -cy)]
    return [Member(f"foot_plate_{name}", "foot_plate", "feet", "plate",
                   center=(x, y, 4.0), size=(fp, fp, 8.0), bolt_holes=4, hole_dia=bolt_d)
            for name, x, y in corners]


# --- families ---------------------------------------------------------------
def build_machine_frame(prompt: str) -> AssemblyBuild:
    L, W, H = parse_lwh(prompt, (1200.0, 800.0, 900.0))
    s = parse_tube_size(prompt, 40.0)
    members: list[Member] = []
    members += _legs(L, W, H, s)
    members += _rect_frame_beams("top_frame", "frame", H - s / 2, L, W, s, "top_rail")
    members += _rect_frame_beams("bottom_frame", "frame", s / 2, L, W, s, "bottom_rail")
    # Diagonal braces on the two long sides.
    hx, hy = L / 2, W / 2
    sec = (s, s)
    members.append(Member("brace_left", "brace", "braces", "beam",
                          (-hx, hy, s), (hx, hy, H - s), section=sec))
    members.append(Member("brace_right", "brace", "braces", "beam",
                          (-hx, -hy, s), (hx, -hy, H - s), section=sec))
    members.append(Member("brace_front", "brace", "braces", "beam",
                          (-hx, -hy, s), (-hx, hy, H - s), section=sec))
    # Crossmembers across the top to carry the motor plate.
    members.append(Member("crossmember_top", "crossmember", "frame", "beam",
                          (0, -hy, H - s / 2), (0, hy, H - s / 2), section=sec))
    members += _foot_plates(L, W, s, 11.0)
    # Motor mounting plate on top, electronics panel on a side.
    members.append(Member("motor_mount_plate", "motor_plate", "mounts", "plate",
                          center=(0, 0, H + 4.0), size=(min(L * 0.35, 300), min(W * 0.45, 300), 10.0),
                          bolt_holes=4, hole_dia=9.0))
    members.append(Member("electronics_panel", "electronics_panel", "panels", "plate",
                          center=(hx - s, 0, H * 0.5), size=(6.0, W * 0.7, H * 0.5),
                          bolt_holes=4, hole_dia=5.5))
    requirements = {
        "min_components": 18,
        "required_roles": ["leg", "top_rail", "bottom_rail", "brace",
                           "foot_plate", "motor_plate", "electronics_panel"],
        "min_legs": 4, "min_foot_plates": 4, "min_holes": 16,
        "envelope": {"x": L, "y": W, "z": H},
    }
    meta = {"tube_profile": "square", "tube_size_mm": s, "tube_wall_thickness_mm": 3.0,
            "dimension_basis": "outside_envelope", "material": "Welded steel (concept)"}
    notes = [f"Square steel tubing {s:g}×{s:g}mm; wall thickness carried as cut-list "
             "metadata (members exported as solid beams).",
             _ENVELOPE_NOTE.format(s=s), *_CONCEPT_NOTES]
    return assemble(members, family_id="machine_frame",
                    display_name="Welded machine frame", design_mode="assembly",
                    profile="structural_frame_assembly",
                    envelope_mm={"x": L, "y": W, "z": H}, meta=meta,
                    requirements=requirements, notes=notes)


def build_engine_test_stand(prompt: str) -> AssemblyBuild:
    L, W, H = parse_lwh(prompt, (1000.0, 700.0, 800.0))
    s = parse_tube_size(prompt, 40.0)
    members: list[Member] = []
    members += _legs(L, W, H, s)
    members += _rect_frame_beams("top_frame", "frame", H - s / 2, L, W, s, "top_rail")
    members += _rect_frame_beams("bottom_frame", "frame", s / 2, L, W, s, "bottom_rail")
    hx, hy = L / 2, W / 2
    sec = (s, s)
    members.append(Member("brace_left", "brace", "braces", "beam",
                          (-hx, hy, s), (hx, hy, H - s), section=sec))
    members.append(Member("brace_right", "brace", "braces", "beam",
                          (-hx, -hy, s), (hx, -hy, H - s), section=sec))
    # Adjustable crossbar spanning the top (height-adjustable in concept).
    members.append(Member("adjustable_crossbar", "adjustable_crossbar", "frame", "beam",
                          (-L * 0.1, -hy, H * 0.7), (-L * 0.1, hy, H * 0.7), section=sec))
    # Engine mounting plates (two, on the crossbar level).
    for i, x in enumerate((-L * 0.18, L * 0.02)):
        members.append(Member(f"engine_mount_plate_{i}", "engine_mount_plate", "mounts", "plate",
                              center=(x, 0, H * 0.7 + s / 2 + 5), size=(140, 120, 10),
                              bolt_holes=4, hole_dia=11.0))
    # Radiator mount at the front.
    members.append(Member("radiator_mount", "radiator_mount", "mounts", "plate",
                          center=(-hx + s, 0, H * 0.55), size=(6.0, W * 0.6, H * 0.4),
                          bolt_holes=4, hole_dia=6.6))
    # Fuel tank tray near the bottom rear.
    members.append(Member("fuel_tank_tray", "fuel_tank_tray", "trays", "plate",
                          center=(hx * 0.5, 0, s + 6), size=(L * 0.3, W * 0.6, 6.0),
                          bolt_holes=4, hole_dia=6.6))
    # Caster wheel plates at the four bottom corners.
    corners = [("fl", -hx, hy), ("fr", -hx, -hy), ("rl", hx, hy), ("rr", hx, -hy)]
    for name, x, y in corners:
        members.append(Member(f"caster_plate_{name}", "caster_plate", "casters", "plate",
                              center=(x, y, 3.0), size=(90, 90, 6.0), bolt_holes=4, hole_dia=9.0))
    requirements = {
        "min_components": 20,
        "required_roles": ["leg", "top_rail", "bottom_rail", "brace",
                           "adjustable_crossbar", "engine_mount_plate",
                           "radiator_mount", "fuel_tank_tray", "caster_plate"],
        "min_legs": 4, "min_caster_plates": 4, "min_holes": 24,
        "envelope": {"x": L, "y": W, "z": H},
    }
    meta = {"tube_profile": "square", "tube_size_mm": s, "tube_wall_thickness_mm": 3.0,
            "material": "Welded steel (concept)"}
    notes = [f"Square steel tubing {s:g}×{s:g}mm; wall thickness carried as cut-list "
             "metadata.", "Adjustable crossbar position is a concept placeholder.",
             _ENVELOPE_NOTE.format(s=s), *_CONCEPT_NOTES]
    return assemble(members, family_id="engine_test_stand",
                    display_name="Engine test stand", design_mode="assembly",
                    profile="structural_frame_assembly",
                    envelope_mm={"x": L, "y": W, "z": H}, meta=meta,
                    requirements=requirements, notes=notes)


def build_drone_frame(prompt: str) -> AssemblyBuild:
    t = (prompt or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:motor[- ]to[- ]motor|diagonal|wheelbase)", t)
    diagonal = float(m.group(1)) if m else 450.0
    diagonal = min(max(diagonal, 120.0), 2000.0)
    r = diagonal / 2.0                      # center -> motor distance
    arm_w = max(12.0, diagonal * 0.04)      # square arm cross-section
    plate_t = 4.0
    central = max(80.0, diagonal * 0.28)    # central plate side
    members: list[Member] = []
    # Four arms in an X layout at ±45°.
    diag = [("fr", 1, 1), ("fl", 1, -1), ("rl", -1, 1), ("rr", -1, -1)]
    motor_pts = []
    for name, sx, sy in diag:
        ex, ey = sx * r * math.cos(math.radians(45)), sy * r * math.cos(math.radians(45))
        motor_pts.append((name, ex, ey))
        members.append(Member(f"arm_{name}", "arm", "arms", "beam",
                              (0, 0, 0), (ex, ey, 0), section=(arm_w, arm_w)))
        # Motor mount plate with a 4-hole pattern at each arm end.
        members.append(Member(f"motor_mount_{name}", "motor_mount", "motors", "plate",
                              center=(ex, ey, arm_w / 2 + 2), size=(40, 40, plate_t),
                              bolt_holes=4, hole_dia=3.4, bore_dia=12.0))
        # Landing foot pointing down at each arm end.
        members.append(Member(f"landing_foot_{name}", "landing_foot", "landing", "tube",
                              (ex, ey, 0), (ex, ey, -max(40.0, diagonal * 0.12)),
                              od=max(8.0, arm_w * 0.7), wall=1.5))
    # Central electronics plate (top) + battery plate (below).
    members.append(Member("electronics_plate", "electronics_plate", "core", "plate",
                          center=(0, 0, arm_w / 2 + 6), size=(central, central, plate_t),
                          bolt_holes=4, hole_dia=3.4))
    members.append(Member("battery_plate", "battery_plate", "core", "plate",
                          center=(0, 0, -arm_w / 2 - 6), size=(central * 0.8, central * 0.6, plate_t),
                          bolt_holes=2, hole_dia=4.5))
    requirements = {
        "min_components": 12,
        "required_roles": ["arm", "motor_mount", "electronics_plate",
                           "battery_plate", "landing_foot"],
        "min_arms": 4, "min_motor_mounts": 4, "min_holes": 16,
        "motor_to_motor_diagonal_mm": diagonal, "diagonal_tolerance_frac": 0.20,
        "motor_points": motor_pts,
        # Envelope is the arm span (approx), not a user L×W×H.
        "envelope": {},
    }
    meta = {"tube_profile": "carbon_fiber_arm", "arm_section_mm": arm_w,
            "motor_to_motor_diagonal_mm": diagonal, "material": "Carbon fiber (concept)"}
    notes = [f"Quadcopter X-frame, ~{diagonal:g}mm motor-to-motor diagonal; carbon-fiber "
             "arms represented as solid beams.", *_CONCEPT_NOTES]
    return assemble(members, family_id="drone_frame",
                    display_name="Quadcopter drone frame", design_mode="assembly",
                    profile="drone_frame", envelope_mm={}, meta=meta,
                    requirements=requirements, notes=notes)


def build_motorcycle_subframe(prompt: str) -> AssemblyBuild:
    L, W, H = parse_lwh(prompt, (850.0, 350.0, 450.0))
    od = parse_tube_size(prompt, 25.0)
    wall = 2.0
    hx, hy = L / 2, W / 2
    # Z layout actually USES the requested height: low front mount, rising main
    # rails, and seat/grab rails reaching near the top so the measured envelope
    # height is close to H (not ~0.6H).
    z_low = H * 0.10            # front mount (bolts to the main frame)
    z_rear = H * 0.55          # main-rail rear height
    z_top = H * 0.95           # seat / grab rail top
    y_front, y_rear = hy, hy * 0.6
    members: list[Member] = []

    def tube(cid, role, system, a, b, side="c", o=od):
        members.append(Member(cid, role, system, "tube", a, b, od=o, wall=wall, side=side))

    # Main rails (left/right), rising and tapering inward toward the rear.
    tube("main_rail_left", "main_rail", "rails", (-hx, y_front, z_low), (hx, y_rear, z_rear), "l")
    tube("main_rail_right", "main_rail", "rails", (-hx, -y_front, z_low), (hx, -y_rear, z_rear), "r")
    # Seat rails climb to the top of the subframe.
    tube("seat_rail_left", "seat_rail", "rails", (-hx * 0.5, y_front * 0.9, z_rear),
         (hx, y_rear * 0.9, z_top), "l")
    tube("seat_rail_right", "seat_rail", "rails", (-hx * 0.5, -y_front * 0.9, z_rear),
         (hx, -y_rear * 0.9, z_top), "r")
    # Rear uprights (grab-rail loop) connect main rails to the seat-rail top.
    tube("rear_upright_left", "brace", "bracing", (hx, y_rear, z_rear), (hx, y_rear * 0.9, z_top), "l")
    tube("rear_upright_right", "brace", "bracing", (hx, -y_rear, z_rear), (hx, -y_rear * 0.9, z_top), "r")
    # Cross members (front, mid, top-rear) tie the rails together.
    tube("cross_front", "crossmember", "rails", (-hx, y_front, z_low), (-hx, -y_front, z_low))
    tube("cross_mid", "crossmember", "rails", (0, hy * 0.8, (z_low + z_rear) / 2),
         (0, -hy * 0.8, (z_low + z_rear) / 2))
    tube("cross_top", "crossmember", "rails", (hx, y_rear * 0.9, z_top), (hx, -y_rear * 0.9, z_top))
    # Triangulated bracing (diagonals each side).
    tube("brace_left", "brace", "bracing", (-hx, y_front, z_low), (0, y_front * 0.7, z_rear), "l")
    tube("brace_right", "brace", "bracing", (-hx, -y_front, z_low), (0, -y_front * 0.7, z_rear), "r")
    # Plates / tabs (shock tabs at the main-rail rear height).
    members.append(Member("shock_mount_tab_left", "shock_mount_tab", "mounts", "plate",
                          center=(hx * 0.4, y_rear, z_rear), size=(50, 6, 60),
                          bolt_holes=2, hole_dia=10.0, side="l"))
    members.append(Member("shock_mount_tab_right", "shock_mount_tab", "mounts", "plate",
                          center=(hx * 0.4, -y_rear, z_rear), size=(50, 6, 60),
                          bolt_holes=2, hole_dia=10.0, side="r"))
    members.append(Member("tail_light_bracket", "tail_light_bracket", "mounts", "plate",
                          center=(hx, 0, z_top), size=(6, W * 0.5, 60),
                          bolt_holes=2, hole_dia=5.0))
    members.append(Member("battery_tray", "battery_tray", "trays", "plate",
                          center=(0, 0, z_low), size=(L * 0.3, W * 0.6, 6.0),
                          bolt_holes=4, hole_dia=5.0))
    members.append(Member("side_panel_tab_left", "side_panel_tab", "mounts", "plate",
                          center=(hx * 0.2, y_rear, z_rear), size=(40, 6, 30),
                          bolt_holes=1, hole_dia=5.0, side="l"))
    members.append(Member("side_panel_tab_right", "side_panel_tab", "mounts", "plate",
                          center=(hx * 0.2, -y_rear, z_rear), size=(40, 6, 30),
                          bolt_holes=1, hole_dia=5.0, side="r"))
    requirements = {
        "min_components": 14,
        "required_roles": ["main_rail", "seat_rail", "crossmember", "brace",
                           "shock_mount_tab", "tail_light_bracket", "battery_tray",
                           "side_panel_tab"],
        "min_holes": 8,
        # Height is now genuinely used, so validate all three axes honestly.
        "envelope": {"x": L, "y": W, "z": H},
    }
    meta = {"tube_profile": "round", "tube_od_mm": od, "tube_wall_thickness_mm": wall,
            "material": "Steel tube (concept)"}
    notes = [f"Round steel tube Ø{od:g}mm × {wall:g}mm wall (exported as solid "
             "cylinders; wall carried as cut-list metadata).",
             "Requested dimensions are treated as the outside envelope; seat/grab "
             "rails rise to ~95% of the requested height.",
             "Rear taper + triangulated bracing are concept geometry.", *_CONCEPT_NOTES]
    return assemble(members, family_id="motorcycle_subframe",
                    display_name="Motorcycle rear subframe", design_mode="assembly",
                    profile="motorcycle_subframe",
                    envelope_mm={"x": L, "y": W, "z": H}, meta=meta,
                    requirements=requirements, notes=notes)


def build_skateboard_motor_mount(prompt: str) -> AssemblyBuild:
    """Primary component of an e-skateboard drive: a motor mount bracket that
    clamps to the truck hanger and carries the motor on a bolt pattern. The full
    deck assembly is intentionally decomposed to this one buildable part."""
    hanger_d = _find(prompt, r"(\d+(?:\.\d+)?)\s*mm\s*hanger", 12.0)
    motor_bolt = 3.4
    base_w, base_d, base_t = 90.0, 60.0, 8.0
    plate_w, plate_h, plate_t = 80.0, 80.0, 8.0
    members: list[Member] = [
        # Clamp base that wraps the truck hanger.
        Member("clamp_base", "clamp_base", "mount", "plate",
               center=(0, 0, base_t / 2), size=(base_w, base_d, base_t),
               bolt_holes=2, hole_dia=6.6, bore_dia=hanger_d),
        # Vertical motor plate with a 4-hole motor pattern + center bore.
        Member("motor_plate", "motor_plate", "mount", "plate",
               center=(base_w / 2 - plate_t / 2, 0, base_t + plate_h / 2),
               size=(plate_t, plate_w, plate_h), bolt_holes=4, hole_dia=motor_bolt,
               bore_dia=12.0),
        # Gusset bracing the plate to the base.
        Member("gusset", "gusset", "mount", "beam",
               (base_w / 2 - plate_t, 0, base_t),
               (base_w / 2 - plate_t - 40, 0, base_t + 40), section=(6.0, base_d * 0.6)),
    ]
    requirements = {
        "min_components": 3,
        "required_roles": ["clamp_base", "motor_plate"],
        "min_holes": 6,
        "envelope": {},
    }
    meta = {"component": "motor_mount_bracket", "hanger_diameter_mm": hanger_d,
            "material": "Aluminium (concept)"}
    notes = [
        "Generated the PRIMARY component (motor mount bracket) of a larger "
        "electric-skateboard assembly request.",
        *_CONCEPT_NOTES,
    ]
    decomposition = (
        "The full e-skateboard request (deck plate, wheel axle supports, belt "
        "guard, battery enclosure, controller enclosure) is a multi-part "
        "assembly. As requested, the main motor mount bracket was generated "
        "first; build the remaining components separately and assemble them."
    )
    return assemble(members, family_id="skateboard_motor_mount",
                    display_name="E-skateboard motor mount bracket",
                    design_mode="single_part", profile="motor_mount_component",
                    envelope_mm={}, meta=meta, requirements=requirements,
                    notes=notes, fused=True, decomposition_note=decomposition)


def _find(prompt: str, pattern: str, default: float) -> float:
    m = re.search(pattern, (prompt or "").lower())
    return float(m.group(1)) if m else default


def build_square_tube_frame(prompt: str, *, family_id: str = "square_tube_frame",
                            display_name: str = "Square-tube frame",
                            decomposition_note: str | None = None) -> AssemblyBuild:
    """Generic reusable square-tube structural frame: four legs, top & bottom
    rectangular frames, diagonal braces and leveling foot plates. Also used as
    the buildable fallback 'base frame' for unsupported assembly requests."""
    L, W, H = parse_lwh(prompt, (1000.0, 700.0, 800.0))
    s = parse_tube_size(prompt, 40.0)
    members: list[Member] = []
    members += _legs(L, W, H, s)
    members += _rect_frame_beams("top_frame", "frame", H - s / 2, L, W, s, "top_rail")
    members += _rect_frame_beams("bottom_frame", "frame", s / 2, L, W, s, "bottom_rail")
    hx, hy = L / 2, W / 2
    sec = (s, s)
    members.append(Member("brace_left", "brace", "braces", "beam",
                          (-hx, hy, s), (hx, hy, H - s), section=sec))
    members.append(Member("brace_right", "brace", "braces", "beam",
                          (-hx, -hy, s), (hx, -hy, H - s), section=sec))
    members += _foot_plates(L, W, s, 11.0)
    requirements = {
        "min_components": 14,
        "required_roles": ["leg", "top_rail", "bottom_rail", "brace", "foot_plate"],
        "min_legs": 4, "min_foot_plates": 4, "min_holes": 16,
        "envelope": {"x": L, "y": W, "z": H},
    }
    meta = {"tube_profile": "square", "tube_size_mm": s, "tube_wall_thickness_mm": 3.0,
            "dimension_basis": "outside_envelope", "material": "Welded steel (concept)"}
    notes = [f"Square steel tubing {s:g}×{s:g}mm; wall thickness carried as cut-list "
             "metadata.", _ENVELOPE_NOTE.format(s=s), *_CONCEPT_NOTES]
    return assemble(members, family_id=family_id, display_name=display_name,
                    design_mode="assembly", profile="structural_frame_assembly",
                    envelope_mm={"x": L, "y": W, "z": H}, meta=meta,
                    requirements=requirements, notes=notes,
                    decomposition_note=decomposition_note)


def build_round_tube_frame(prompt: str, *, family_id: str = "round_tube_frame",
                           display_name: str = "Round-tube frame") -> AssemblyBuild:
    """Generic reusable round-tube structural frame (cylindrical members)."""
    L, W, H = parse_lwh(prompt, (1000.0, 700.0, 800.0))
    od = parse_tube_size(prompt, 40.0)
    wall = 2.5
    hx, hy = L / 2, W / 2
    members: list[Member] = []

    def tube(cid, role, system, a, b):
        members.append(Member(cid, role, system, "tube", a, b, od=od, wall=wall))

    corners = [("fl", -hx, hy), ("fr", -hx, -hy), ("rl", hx, hy), ("rr", hx, -hy)]
    for name, x, y in corners:
        tube(f"leg_{name}", "leg", "legs", (x, y, 0), (x, y, H))
    for z, role in ((H - od / 2, "top_rail"), (od / 2, "bottom_rail")):
        tube(f"{role}_front", role, "frame", (-hx, -hy, z), (-hx, hy, z))
        tube(f"{role}_rear", role, "frame", (hx, -hy, z), (hx, hy, z))
        tube(f"{role}_left", role, "frame", (-hx, hy, z), (hx, hy, z))
        tube(f"{role}_right", role, "frame", (-hx, -hy, z), (hx, -hy, z))
    tube("brace_left", "brace", "braces", (-hx, hy, od), (hx, hy, H - od))
    tube("brace_right", "brace", "braces", (-hx, -hy, od), (hx, -hy, H - od))
    members += _foot_plates(L, W, od, 11.0)
    requirements = {
        "min_components": 12,
        "required_roles": ["leg", "top_rail", "bottom_rail", "brace", "foot_plate"],
        "min_legs": 4, "min_holes": 16,
        "envelope": {"x": L, "y": W, "z": H},
    }
    meta = {"tube_profile": "round", "tube_od_mm": od, "tube_wall_thickness_mm": wall,
            "dimension_basis": "outside_envelope", "material": "Steel tube (concept)"}
    notes = [f"Round steel tube Ø{od:g}mm × {wall:g}mm wall (exported as solid "
             "cylinders; wall carried as cut-list metadata).",
             _ENVELOPE_NOTE.format(s=od), *_CONCEPT_NOTES]
    return assemble(members, family_id=family_id, display_name=display_name,
                    design_mode="assembly", profile="structural_frame_assembly",
                    envelope_mm={"x": L, "y": W, "z": H}, meta=meta,
                    requirements=requirements, notes=notes)


def build_cnc_router_frame(prompt: str) -> AssemblyBuild:
    """Desktop CNC router frame concept with clearly separated BASE, BED and
    GANTRY groups: an aluminium-extrusion-style base rectangle, bed
    cross-members, linear-rail mounting strips, two gantry side plates, a gantry
    bridge beam and motor mount plates."""
    L, W, H = parse_lwh(prompt, (900.0, 700.0, 350.0))
    ext = parse_tube_size(prompt, 40.0)   # aluminium extrusion section
    hx, hy = L / 2, W / 2
    sec = (ext, ext)
    z_base = ext / 2
    members: list[Member] = []

    # --- BASE: extrusion-style perimeter rails -------------------------------
    members.append(Member("base_rail_front", "base_rail", "base", "beam",
                          (-hx, -hy, z_base), (-hx, hy, z_base), section=sec))
    members.append(Member("base_rail_rear", "base_rail", "base", "beam",
                          (hx, -hy, z_base), (hx, hy, z_base), section=sec))
    members.append(Member("base_rail_left", "base_rail", "base", "beam",
                          (-hx, hy, z_base), (hx, hy, z_base), section=sec))
    members.append(Member("base_rail_right", "base_rail", "base", "beam",
                          (-hx, -hy, z_base), (hx, -hy, z_base), section=sec))

    # --- BED: cross-members spanning the width + linear-rail mounting strips --
    n_cross = 4
    xs = [-hx + ext + (L - 2 * ext) * i / (n_cross - 1) for i in range(n_cross)]
    for i, x in enumerate(xs):
        members.append(Member(f"bed_crossmember_{i}", "bed_crossmember", "bed", "beam",
                              (x, -hy + ext, z_base), (x, hy - ext, z_base), section=sec))
    # Linear-rail mounting strips on the two long sides, with rail bolt holes.
    rail_holes = max(4, int((L - 2 * ext) / 80))
    for name, y in (("left", hy - ext / 2), ("right", -hy + ext / 2)):
        members.append(Member(f"linear_rail_mount_{name}", "linear_rail_mount", "bed", "plate",
                              center=(0, y, ext + 3.0), size=(L - 2 * ext, ext * 0.8, 6.0),
                              bolt_holes=rail_holes, hole_dia=5.5))

    # --- GANTRY: side plates + bridge beam + motor mount plates ---------------
    gx = 0.0                       # gantry sits mid-table
    plate_x = min(180.0, L * 0.25)
    gantry_h = H - ext
    for name, y in (("left", hy - ext / 2), ("right", -hy + ext / 2)):
        members.append(Member(f"gantry_side_plate_{name}", "gantry_side_plate", "gantry", "plate",
                              center=(gx, y, ext + gantry_h / 2),
                              size=(plate_x, 12.0, gantry_h), bolt_holes=4, hole_dia=6.6))
    members.append(Member("gantry_bridge", "gantry_bridge", "gantry", "beam",
                          (gx, hy - ext, H - ext / 2), (gx, -hy + ext, H - ext / 2),
                          section=(ext, ext)))
    # Motor mount plates: X/Y on the gantry, plus a Z motor plate on the bridge.
    members.append(Member("motor_plate_x", "motor_plate", "gantry", "plate",
                          center=(gx + plate_x / 2, hy - ext, ext + gantry_h * 0.8),
                          size=(70, 70, 8.0), bolt_holes=4, hole_dia=4.5, bore_dia=22.0))
    members.append(Member("motor_plate_z", "motor_plate", "gantry", "plate",
                          center=(gx, 0, H - ext), size=(70, 70, 8.0),
                          bolt_holes=4, hole_dia=4.5, bore_dia=22.0))

    requirements = {
        "min_components": 14,
        "required_roles": ["base_rail", "bed_crossmember", "linear_rail_mount",
                           "gantry_side_plate", "gantry_bridge", "motor_plate"],
        "min_holes": 12,
        "envelope": {"x": L, "y": W, "z": H},
        "required_groups": ["base", "bed", "gantry"],
    }
    meta = {"tube_profile": "aluminium_extrusion", "extrusion_size_mm": ext,
            "dimension_basis": "outside_envelope",
            "groups": ["base", "bed", "gantry"], "material": "Aluminium extrusion (concept)"}
    notes = [f"Aluminium-extrusion-style members {ext:g}×{ext:g}mm exported as solid "
             "beams; clearly separated base / bed / gantry metadata groups.",
             "Linear-rail mounting holes are plain bores (no rail profile modelled).",
             _ENVELOPE_NOTE.format(s=ext), *_CONCEPT_NOTES]
    return assemble(members, family_id="cnc_router_frame",
                    display_name="Desktop CNC router frame", design_mode="assembly",
                    profile="structural_frame_assembly",
                    envelope_mm={"x": L, "y": W, "z": H}, meta=meta,
                    requirements=requirements, notes=notes)


# --- detection + dispatch ---------------------------------------------------
_BUILDERS = {
    "cnc_router_frame": build_cnc_router_frame,
    "machine_frame": build_machine_frame,
    "engine_test_stand": build_engine_test_stand,
    "drone_frame": build_drone_frame,
    "motorcycle_subframe": build_motorcycle_subframe,
    "skateboard_motor_mount": build_skateboard_motor_mount,
    "square_tube_frame": build_square_tube_frame,
    "round_tube_frame": build_round_tube_frame,
}


def detect_frame_family(prompt: str) -> str | None:
    """Return a supported deterministic frame/assembly family, or None.

    Returns None for the tubular-chassis / roll-cage family (handled by the
    dedicated chassis generator) and for anything not explicitly supported, so
    unsupported huge prompts still fall through to decomposition guidance."""
    t = (prompt or "").lower()
    # Leave vehicle chassis / roll cage to the chassis generator.
    if re.search(r"\bchassis\b|\broll ?cage\b|\bspace ?frame\b", t):
        return None
    if re.search(r"\bcnc\b", t) and re.search(r"router|mill|gantry|machine", t):
        return "cnc_router_frame"
    if "cnc router" in t or ("router frame" in t and "gantry" in t):
        return "cnc_router_frame"
    if re.search(r"\bdrone\b|\bquad ?copter\b|\bquadrotor\b", t):
        return "drone_frame"
    if "motorcycle" in t and re.search(r"sub[- ]?frame|rear frame", t):
        return "motorcycle_subframe"
    if "skateboard" in t or "longboard" in t:
        return "skateboard_motor_mount"
    if re.search(r"engine test stand|test stand|engine stand", t):
        return "engine_test_stand"
    if "machine frame" in t or "equipment frame" in t or "workbench frame" in t:
        return "machine_frame"
    # Generic structural frame: a welded/tubing frame with legs.
    if re.search(r"\bframe\b", t) and re.search(r"\btub(?:e|ing)\b", t) \
            and re.search(r"\bleg(?:s)?\b", t):
        return "machine_frame"
    return None


def build_frame_family(prompt: str, family_id: str) -> AssemblyBuild:
    builder = _BUILDERS.get(family_id)
    if builder is None:
        raise CadGenerationError(f"no frame builder for '{family_id}'")
    return builder(prompt)


# Phrases that grant permission to build only a primary component when the full
# assembly is too complex, e.g. "if this is too complex, generate the base frame
# first" or "generate the main motor mount bracket first".
_FALLBACK_RE = re.compile(
    r"(?:if[^.]{0,60}?too complex[^.]{0,80}?)?generate (?:the |a |an )?"
    r"(?P<what>[a-z][a-z \-]{2,40}?) first", re.I)


def detect_fallback_directive(prompt: str) -> str | None:
    """If the prompt explicitly permits building a single primary component when
    the whole thing is too complex, return the buildable family for that
    component (else None). Used so an otherwise-unsupported huge prompt produces
    a useful part instead of generic decomposition."""
    t = (prompt or "").lower()
    m = _FALLBACK_RE.search(t)
    if not m:
        return None
    what = m.group("what")
    if "motor mount" in what:
        return "skateboard_motor_mount" if "skateboard" in t else "skateboard_motor_mount"
    if "frame" in what:  # base frame / main frame / base rectangular frame
        return "square_tube_frame"
    return None
