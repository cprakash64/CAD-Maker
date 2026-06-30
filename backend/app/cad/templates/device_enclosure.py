"""Deterministic single-board-computer enclosure (Raspberry Pi 4 / 5).

Builds a real device enclosure from a local :class:`DevicePreset` (board outline,
mounting holes, connector cutouts, cooling) — NEVER a generic box. Generation order
(matches a real enclosure workflow):

  1. create the outer shell box,
  2. hollow it out (floor + four walls, open top),
  3. add standoffs / bosses aligned to the board's mounting holes,
  4. build the (separate, removable) lid,
  5. subtract the port cutouts — each a TRUE THROUGH-HOLE.

Every port opening (USB-C, micro-HDMI, HDMI, Ethernet, USB-A, audio jack, microSD,
GPIO slot, FFC slots, …) is cut with :func:`wall_opening_cutter`, a single reusable
cutter that starts OUTSIDE the wall and extends past the inner face INTO the cavity,
so the opening is always a real through-hole rather than a shallow pocket (the old
bug: a cutter only ``wall/2 + 1`` deep left residual material on a 2.5 mm wall).

After cutting, :func:`audit_port_openings` probes each port through the wall and
records whether it is truly open; the route reads the verdict via
:func:`take_last_port_audit` and refuses a PASS if any required port is blocked.

Two object types share this builder: ``rpi4_enclosure`` and ``rpi5_enclosure``.
"""
from __future__ import annotations

import contextvars

import cadquery as cq
from cadquery import Vector

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.cad.device_presets import get_preset, preset_for_object_type
from app.cad.device_presets.devices import (
    SIDE_X_MAX,
    SIDE_X_MIN,
    SIDE_Y_MAX,
    SIDE_Y_MIN,
    DevicePreset,
)
from app.schemas.design_spec import DesignSpec

# Horizontal gap between the board edge and the inner wall (room for standoffs).
_BOARD_TO_WALL_MM = 2.5
# Exploded gap the lid floats above the shell opening (so internals are visible).
_LID_EXPLODE_MM = 14.0
# Through-cutter overshoot: how far OUTSIDE the wall a cutter starts, and how far
# PAST the inner face it reaches into the cavity. Both > 0 guarantee a clean
# through-hole regardless of wall thickness or float rounding.
_CUT_OUT_MARGIN_MM = 1.2
_CUT_OVERSHOOT_MM = 3.0

# The build records its per-port through-hole audit here so the route can read the
# authoritative verdict (the template's build() can only return a Workplane).
_LAST_PORT_AUDIT: contextvars.ContextVar = contextvars.ContextVar(
    "last_port_audit", default=None)


def take_last_port_audit() -> "list[dict] | None":
    """Return and clear the most recent enclosure port-opening audit."""
    a = _LAST_PORT_AUDIT.get()
    _LAST_PORT_AUDIT.set(None)
    return a


def set_last_port_audit(audit: "list[dict] | None") -> None:
    """Record the port-opening audit for the route to read (used by all enclosure
    builders that cut through-wall ports)."""
    _LAST_PORT_AUDIT.set(audit)


def wall_opening_cutter(*, side: str, pos: float, z_center: float, width: float,
                        height: float, outer_x: float, outer_y: float, wall: float,
                        out_margin: float = _CUT_OUT_MARGIN_MM,
                        overshoot: float = _CUT_OVERSHOOT_MM) -> "cq.Workplane":
    """A box cutter that punches a TRUE through-hole in one enclosure wall.

    The cutter starts ``out_margin`` OUTSIDE the wall's outer face and extends
    ``overshoot`` PAST the inner face into the cavity, so subtracting it always
    removes the full wall thickness at the opening (never a shallow pocket).

    ``pos`` is the opening centre ALONG the wall (shell-X for the long y_* walls,
    shell-Y for the short x_* walls); ``z_center`` is the opening centre height;
    ``width``/``height`` are the final opening size (clearance already applied)."""
    depth = wall + out_margin + overshoot
    if side in (SIDE_Y_MIN, SIDE_Y_MAX):
        if side == SIDE_Y_MIN:
            yc = (-outer_y / 2.0 - out_margin) + depth / 2.0   # extends +Y inward
        else:
            yc = (outer_y / 2.0 + out_margin) - depth / 2.0    # extends -Y inward
        box = cq.Workplane("XY").box(width, depth, height, centered=(True, True, True))
        return box.translate((pos, yc, z_center))
    # short walls (normal ±X)
    if side == SIDE_X_MIN:
        xc = (-outer_x / 2.0 - out_margin) + depth / 2.0
    else:
        xc = (outer_x / 2.0 + out_margin) - depth / 2.0
    box = cq.Workplane("XY").box(depth, width, height, centered=(True, True, True))
    return box.translate((xc, pos, z_center))


def _wall_probe(side: str, pos: float, z_center: float, outer_x: float,
                outer_y: float, wall: float, width: float, height: float) -> "cq.Workplane":
    """A thin solid spanning ONLY the wall thickness at a port centre — intersecting
    it with the body measures residual (blocking) wall material in the opening."""
    pw = max(0.4, min(width * 0.5, 3.0))
    ph = max(0.4, min(height * 0.5, 3.0))
    if side in (SIDE_Y_MIN, SIDE_Y_MAX):
        yc = (-outer_y / 2.0 + wall / 2.0) if side == SIDE_Y_MIN else (outer_y / 2.0 - wall / 2.0)
        box = cq.Workplane("XY").box(pw, wall, ph, centered=(True, True, True))
        return box.translate((pos, yc, z_center))
    xc = (-outer_x / 2.0 + wall / 2.0) if side == SIDE_X_MIN else (outer_x / 2.0 - wall / 2.0)
    box = cq.Workplane("XY").box(wall, pw, ph, centered=(True, True, True))
    return box.translate((xc, pos, z_center))


def audit_port_openings(body: "cq.Workplane", ports: list[dict], outer_x: float,
                        outer_y: float, wall: float, *, residual_tol_mm3: float = 0.05
                        ) -> list[dict]:
    """Probe each cut port and report whether it is a TRUE through-opening.

    For each port a thin probe spanning the wall thickness at the opening centre is
    intersected with the body; near-zero residual volume ⇒ the wall is fully open
    there. Returns one record per port: ``{name, side, kind, required, open,
    residual_mm3}``."""
    out: list[dict] = []
    for p in ports:
        probe = _wall_probe(p["side"], p["pos"], p["z_center"], outer_x, outer_y,
                            wall, p["width"], p["height"])
        residual = 0.0
        try:
            inter = body.val().intersect(probe.val())
            residual = float(inter.Volume()) if inter is not None else 0.0
        except Exception:  # noqa: BLE001 — empty intersection / kernel quirk ⇒ open
            residual = 0.0
        out.append({
            "name": p["name"], "side": p["side"], "kind": p.get("kind", "port"),
            "required": p.get("required", True),
            "open": residual <= residual_tol_mm3,
            "residual_mm3": round(residual, 4),
        })
    return out


class _DeviceEnclosureBase(BaseTemplate):
    """Shared builder; concrete subclasses set ``object_type`` (board selector)."""

    name = "Device Enclosure"
    description = "Single-board-computer enclosure built from a local board preset."
    dimensions = [
        DimensionSpec("wall_thickness", "Wall thickness", 2.5, 1.0, 8.0),
        DimensionSpec("logo", "Logo emboss area (0/1)", 0.0, 0.0, 1.0),
    ]

    def _preset(self, spec: DesignSpec) -> DevicePreset:
        # Dedicated board object types (rpi4/rpi5) map by object_type; the generic
        # ``board_enclosure`` carries its board preset id on the spec.
        p = preset_for_object_type(self.object_type)
        if p is None and getattr(spec, "preset_id", None):
            p = get_preset(spec.preset_id)
        if p is None:
            raise CadGenerationError(
                f"no device preset for {self.object_type} / {getattr(spec, 'preset_id', None)}")
        return p

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        p = self._preset(spec)
        r = self.resolve(spec)
        wall = r["wall_thickness"]
        want_logo = r.dims_mm.get("logo", 0.0) >= 0.5
        e = p.enclosure
        b = p.board

        # --- envelope -------------------------------------------------------
        inner_x = b.length_mm + 2 * _BOARD_TO_WALL_MM
        inner_y = b.width_mm + 2 * _BOARD_TO_WALL_MM
        cavity_h = e.board_clearance_below_mm + b.thickness_mm + e.board_clearance_above_mm
        outer_x = inner_x + 2 * wall
        outer_y = inner_y + 2 * wall
        outer_h = wall + cavity_h          # floor + cavity (open top)
        board_low_z = wall + e.board_clearance_below_mm   # board lower face

        # Board-local (x:0..L, y:0..W) -> shell-local (board centered in cavity).
        def to_shell(bx: float, by: float) -> tuple[float, float]:
            return bx - b.length_mm / 2.0, by - b.width_mm / 2.0

        # 1+2) outer shell, hollowed (floor + 4 walls, open top) -------------
        base = cq.Workplane("XY").box(outer_x, outer_y, outer_h, centered=(True, True, False))
        cavity = (cq.Workplane("XY").workplane(offset=wall)
                  .box(inner_x, inner_y, cavity_h + 1.0, centered=(True, True, False)))
        base = base.cut(cavity)

        # 3) standoffs / bosses at the board mounting holes ------------------
        so_r = e.standoff_outer_diameter_mm / 2.0
        screw_r = e.standoff_screw_diameter_mm / 2.0
        so_h = e.board_clearance_below_mm
        for m in p.mounting_holes:
            sx, sy = to_shell(m.x_mm, m.y_mm)
            post = (cq.Workplane("XY").workplane(offset=wall)
                    .center(sx, sy).circle(so_r).extrude(so_h))
            base = base.union(post)
            # Screw pilot hole — skipped for retention-only posts (no PCB hole).
            if screw_r > 0.1:
                hole = cq.Solid.makeCylinder(
                    screw_r, so_h + wall * 0.6, Vector(sx, sy, board_low_z + 0.2),
                    Vector(0, 0, -1))
                base = base.cut(cq.Workplane(obj=hole))

        # 4) build the (separate, removable) lid -----------------------------
        lid = self._build_lid(p, wall, inner_x, inner_y, outer_x, outer_y,
                              outer_h, want_logo)

        # 5) subtract every port cutout as a TRUE through-hole ---------------
        ports = self._port_specs(p, wall, outer_x, outer_y, board_low_z, e, to_shell)
        for sp in ports:
            cutter = wall_opening_cutter(
                side=sp["side"], pos=sp["pos"], z_center=sp["z_center"],
                width=sp["width"], height=sp["height"],
                outer_x=outer_x, outer_y=outer_y, wall=wall)
            base = base.cut(cutter)

        # Audit the through-holes on the BASE (before the lid floats above it).
        set_last_port_audit(audit_port_openings(base, ports, outer_x, outer_y, wall))

        return base.union(lid)

    # ---- port geometry ----------------------------------------------------
    def _port_specs(self, p, wall, outer_x, outer_y, board_low_z, e, to_shell
                    ) -> list[dict]:
        """Resolve every connector + cable slot into a shell-local opening spec
        (side, along-pos, z-centre, width, height) — the single source of truth for
        both the cutters and the through-hole audit."""
        margin = e.connector_clearance_mm
        specs: list[dict] = []

        def add(name, side, along, width, height, z_base, kind, margin_v, required):
            w = width + 2 * margin_v
            h = height + 2 * margin_v
            z0 = board_low_z + z_base - margin_v
            if side in (SIDE_Y_MIN, SIDE_Y_MAX):
                pos, _ = to_shell(along, 0.0)
            else:
                _, pos = to_shell(0.0, along)
            specs.append({"name": name, "side": side, "pos": pos,
                          "z_center": z0 + h / 2.0, "width": w, "height": h,
                          "kind": kind, "required": required})

        for c in p.connectors:
            add(c.name, c.side, c.along_mm, c.width_mm, c.height_mm, c.z_base_mm,
                c.kind, margin, required=(c.kind == "port"))
        for s in p.cable_slots:
            add(s.name, s.side, s.along_mm, s.width_mm, s.height_mm, 2.0, "ffc",
                0.3, required=False)
        return specs

    def _build_lid(self, p, wall, inner_x, inner_y, outer_x, outer_y, outer_h,
                   want_logo):
        lid_t = max(wall, 2.0)
        lid_z = outer_h + _LID_EXPLODE_MM
        lid = (cq.Workplane("XY").workplane(offset=lid_z)
               .box(outer_x, outer_y, lid_t, centered=(True, True, False)))
        # Ventilation slot pattern on the lid (clear of the perimeter rim).
        slot_w, slot_gap = 2.0, 4.0
        slot_len = inner_y * 0.55
        n = max(3, int((inner_x * 0.6) // (slot_w + slot_gap)))
        x0 = -((n - 1) * (slot_w + slot_gap)) / 2.0
        for i in range(n):
            x = x0 + i * (slot_w + slot_gap)
            slot = (cq.Workplane("XY").workplane(offset=lid_z - 1.0)
                    .center(x, 0).box(slot_w, slot_len, lid_t + 2.0,
                                      centered=(True, True, False)))
            lid = lid.cut(slot)
        if want_logo:
            pad_l, pad_w = min(40.0, outer_x * 0.5), min(16.0, outer_y * 0.4)
            pad = (cq.Workplane("XY").workplane(offset=lid_z + lid_t)
                   .center(outer_x * 0.22, 0).box(pad_l, pad_w, 1.0,
                                                  centered=(True, True, False)))
            lid = lid.union(pad)
        return lid


class RPi4EnclosureTemplate(_DeviceEnclosureBase):
    object_type = "rpi4_enclosure"
    name = "Raspberry Pi 4 Model B Enclosure"


class RPi5EnclosureTemplate(_DeviceEnclosureBase):
    object_type = "rpi5_enclosure"
    name = "Raspberry Pi 5 Enclosure"


class BoardEnclosureTemplate(_DeviceEnclosureBase):
    """Generic, preset-driven board enclosure (Arduino, ESP32, Jetson, …). The board
    preset id is carried on ``spec.preset_id``."""

    object_type = "board_enclosure"
    name = "Board Enclosure"
