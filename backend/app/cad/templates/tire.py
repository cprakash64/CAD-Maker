"""Finished single-part rubber tire (no rim/hub/spokes).

The tire is a REVOLVED radial cross-section. The section is designed to read as a
*real tire* — a broad, gently-domed tread crown, defined rounded shoulders, convex
sidewalls that bulge to the exact section width, beads tucked inboard at the rim
seat, and an open centre bore — NOT a torus/swim-ring and NOT a flat washer.

OUTER meridian (crown apex → bead heel, mirrored about the centre):
    crown arc      — a broad, almost-flat dome across the tread band (large radius)
    shoulder arc   — rolls the crown edge down into the sidewall; the arc is built
                     so its topmost point is the sidewall bulge, guaranteeing the
                     axial extreme is *exactly* ±width/2 (no overshoot, no flat)
    sidewall arc   — a second convex arc, tangent at the bulge, tapering down and in
    bead face      — a short radial heel to the bore at the inner radius
INNER meridian (the cavity liner) is a domed ceiling joining the two beads, leaving
the section an OPEN "C" toward the axis — exactly like a real tyre that seats on a
rim. This keeps the carcass hollow, the centre bore fully open through the width, and
the whole surface ONE connected watertight shell (no enclosed void).

The whole meridian is a DENSE POLYLINE sampled from analytic arcs (no fragile
single spline that overshoots the OD/width), so OD and section width come out exact.

Five styles reshape the crown proportions AND drive the tread:
  1 smooth  — street proportions, no tread (slick)
  2 racing  — flat wide crown, shallow circumferential grooves
  3 street  — balanced crown, circumferential grooves + light lateral sipes
  4 offroad — broad flat crown, chunky staggered centre + shoulder lugs (aggressive)
  5 block   — all-terrain chunky staggered centre lugs (default when tread requested)

Tread is real CAD geometry on the CROWN band only — never on the sidewalls, never a
helix. Lug styles recess the crown to a base radius and union raised blocks back up
to the exact OD, so the tread is chunky and deep while the OD is preserved.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec

# Tread style codes (positive — the spec validator rejects 0.0 dims).
TREAD_SMOOTH = 1
TREAD_RACING = 2
TREAD_STREET = 3
TREAD_OFFROAD = 4
TREAD_BLOCK = 5

# --- SHAPE presets (fractions of h = ro-ri for radii, of hw = width/2 for axial) --
#   tread_half : tread-band half width  (crown spans ±tread_half·hw)
#   crown_drop : radial dome — tread edge sits this far below the crown apex
#   bulge      : max-width radius = ri + bulge·h  (reached at y = ±hw)
#   bead_y     : axial position of the bead heel (< 1.0 — beads sit inboard)
#   seat       : bead-seat-top radius = ri + seat·h
#   crown_th   : tread-rubber thickness (cavity ceiling depth below the crown)
#   side_wall  : sidewall rubber thickness (cavity inset from the outer wall)
#   bead_wall  : bead rubber thickness above the bore
_SHAPE = {
    "offroad": dict(tread_half=0.70, crown_drop=0.03, bulge=0.60, bead_y=0.72,
                    seat=0.06, crown_th=0.30, side_wall=0.14, bead_wall=0.22),
    "street": dict(tread_half=0.66, crown_drop=0.05, bulge=0.58, bead_y=0.74,
                   seat=0.06, crown_th=0.30, side_wall=0.14, bead_wall=0.22),
    "race": dict(tread_half=0.78, crown_drop=0.02, bulge=0.54, bead_y=0.78,
                 seat=0.06, crown_th=0.30, side_wall=0.16, bead_wall=0.22),
}

# --- STYLE presets (shape + tread) -----------------------------------------
_STYLE = {
    TREAD_SMOOTH: dict(shape="street", tread=dict(kind="none"),
                       label="smooth / slick"),
    TREAD_RACING: dict(shape="race",
                       tread=dict(kind="groove", circ=3, depth=1.8, sipes=False),
                       label="racing (circumferential grooves)"),
    TREAD_STREET: dict(shape="street",
                       tread=dict(kind="groove", circ=4, depth=2.4, sipes=False),
                       label="city / street grooves"),
    TREAD_OFFROAD: dict(shape="offroad",
                        tread=dict(kind="lug", depth=4.0, rows=3, pitch=34.0,
                                   fill=0.66, shoulder=True),
                        label="off-road block + shoulder"),
    TREAD_BLOCK: dict(shape="offroad",
                      tread=dict(kind="lug", depth=3.4, rows=3, pitch=26.0,
                                 fill=0.60, shoulder=True),
                      label="all-terrain block"),
}


def style_label(code: int) -> str:
    return _STYLE.get(int(code), _STYLE[TREAD_STREET])["label"]


# --- analytic meridian helpers ---------------------------------------------
def _arc(cx: float, cy: float, R: float, a0: float, a1: float, n: int):
    return [(cx + R * math.cos(a0 + (a1 - a0) * i / n),
             cy + R * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def _outer_half(ri: float, ro: float, hw: float, s: dict):
    """Front half of the OUTER meridian: crown apex (ro, 0) → front bead heel
    (ri, +y_bead), as (r, y) points with y ≥ 0. Dense enough to revolve smooth."""
    h = ro - ri
    tb_h = s["tread_half"] * hw
    crown_drop = max(1e-3, s["crown_drop"] * h)
    r_bulge = ri + s["bulge"] * h
    y_bead = s["bead_y"] * hw
    r_seat = ri + s["seat"] * h

    # 1) crown: a broad gentle dome. apex (ro,0) → tread edge E=(ro-crown_drop, tb_h)
    Rc = (crown_drop ** 2 + tb_h ** 2) / (2 * crown_drop)
    Oc = (ro - Rc, 0.0)
    phi = math.asin(min(1.0, tb_h / Rc))
    crown = _arc(Oc[0], Oc[1], Rc, 0.0, phi, 20)
    E = crown[-1]

    # 2) shoulder/upper-sidewall arc: E → bulge B=(r_bulge, hw). Centre sits directly
    #    below B (cx = r_bulge) so B is the arc's TOPMOST point ⇒ max |y| == hw
    #    exactly (this is what stops the widest section being sliced flat).
    cy_u = (hw ** 2 - E[1] ** 2 - (r_bulge - E[0]) ** 2) / (2 * (hw - E[1]))
    aE = math.atan2(E[1] - cy_u, E[0] - r_bulge)
    upper = _arc(r_bulge, cy_u, hw - cy_u, aE, math.pi / 2, 22)

    # 3) lower-sidewall arc: B → bead seat S=(r_seat, y_bead). Also topmost at B, so it
    #    shares B's horizontal tangent with the upper arc → one smooth convex bulge.
    cy_l = (hw ** 2 - y_bead ** 2 - (r_bulge - r_seat) ** 2) / (2 * (hw - y_bead))
    aS = math.atan2(y_bead - cy_l, r_seat - r_bulge)
    lower = _arc(r_bulge, cy_l, hw - cy_l, math.pi / 2, aS, 22)

    # 4) bead heel: seat top S → bore (ri, y_bead)
    return crown + upper[1:] + lower[1:] + [(ri, y_bead)]


def _cavity_liner_half(ri: float, ro: float, hw: float, s: dict):
    """Half of the cavity liner (the inner ceiling of the C), as (r, y) with y ≥ 0:
    ceiling apex (ceiling, 0) → front cavity bead (r_cav, y_lip). A quarter-ellipse so
    the interior domes smoothly from the crown down to each bead."""
    h = ro - ri
    ceiling = ro - s["crown_th"] * h
    r_cav = ri + s["bead_wall"] * h
    y_lip = s["bead_y"] * hw * 0.9
    a_r = ceiling - r_cav
    n = 16
    return [(r_cav + a_r * math.cos(t * math.pi / 2),
             y_lip * math.sin(t * math.pi / 2)) for t in [i / n for i in range(n + 1)]]


def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - out[-1][0]) > 1e-7 or abs(p[1] - out[-1][1]) > 1e-7:
            out.append(p)
    return out


def tire_section_paths(ri: float, ro: float, width: float, shape: dict):
    """The single closed point path of the open C-section (kept for callers/tests):
    back bead → crown apex → front bead (the OUTER wall, a polyline) then front →
    ceiling apex → back (the cavity liner, a spline). Returns ``(loop, n_outer)``
    where ``loop[:n_outer]`` is the outer wall, in a (r, axial) frame centred on
    the width."""
    hw = width / 2.0
    outer = _outer_half(ri, ro, hw, shape)
    cav = _cavity_liner_half(ri, ro, hw, shape)

    def mir(pts):
        return [(r, -y) for (r, y) in pts]

    outer_full = _dedupe(list(reversed(mir(outer))) + outer[1:])
    cav_front_to_back = list(reversed(cav)) + [(p[0], -p[1]) for p in cav[1:]]
    loop = outer_full + cav_front_to_back
    return loop, len(outer_full)


class TireTemplate(BaseTemplate):
    object_type = "tire"
    name = "Tire"
    description = "Finished hollow rubber tire with a real carcass + tread — no rim."
    dimensions = [
        DimensionSpec("outer_diameter", "Outer diameter", 100.0, 20.0, 2000.0),
        DimensionSpec("inner_diameter", "Inner diameter", 60.0, 5.0, 1900.0),
        DimensionSpec("width", "Section width", 30.0, 3.0, 600.0),
        DimensionSpec("tread_style_code", "Tread style (1-5)", 3.0, 1.0, 5.0),
        DimensionSpec("tread_depth", "Tread groove depth override", 3.0, 0.0, 20.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        od = r["outer_diameter"]
        idia = r["inner_diameter"]
        width = r["width"]
        code = int(round(r.dims_mm.get("tread_style_code", TREAD_STREET)))
        style = _STYLE.get(code, _STYLE[TREAD_STREET])
        shape = _SHAPE[style["shape"]]

        ro = od / 2.0
        ri = idia / 2.0
        hw = width / 2.0
        if ri >= ro - 4.0:
            raise CadGenerationError(
                f"tire inner diameter ({idia}mm) must be well under the OD ({od}mm)")

        # Revolve the closed C-section (dense polyline outer + smooth cavity ceiling)
        # about Y, reorient so the tire axis is Z, and shift so the tire spans
        # z ∈ [0, width] (matches the rim, for assembly). The section is open toward
        # the axis, so the carcass is hollow yet one connected watertight shell.
        loop, n_outer = tire_section_paths(ri, ro, width, shape)
        w = cq.Workplane("XY").moveTo(*loop[0]).polyline(loop[1:n_outer])
        w = w.spline(loop[n_outer:], includeCurrent=True).close()
        body = (w.revolve(360, (0, 0, 0), (0, 1, 0))
                .rotate((0, 0, 0), (1, 0, 0), 90).translate((0, 0, hw)))
        # Safety clamp: kill any radial/axial overshoot so OD and width are exact.
        body = body.cut(cq.Workplane("XY").circle(ro + 60).circle(ro)
                        .extrude(width * 3).translate((0, 0, -width)))
        body = self._clamp_width(body, ro, width)

        tread = style["tread"]
        if tread["kind"] != "none":
            override = r.dims_mm.get("tread_depth")
            depth = tread["depth"]
            if override and abs(override - 3.0) > 1e-6:
                depth = override
            depth = min(depth, (ro - ri) * 0.30, shape["crown_th"] * (ro - ri) * 0.9)
            if tread["kind"] == "lug":
                body = self._lug_tread(body, ro, ri, width, depth, tread, shape)
            else:
                body = self._groove_tread(body, ro, ri, width, depth, tread, shape)
            body = self._clamp_width(body, ro, width)
        return body

    @staticmethod
    def _clamp_width(body, ro, width):
        slab = cq.Workplane("XY").rect(2 * (ro + 30), 2 * (ro + 30)).extrude(width * 2)
        return (body.cut(slab.translate((0, 0, width)))
                .cut(slab.translate((0, 0, -width * 2))))

    def _tread_band(self, ro, width, shape):
        tb = 2.0 * shape["tread_half"] * (width / 2.0)
        return width / 2 - tb / 2, width / 2 + tb / 2

    def _lug_tread(self, body, ro, ri, width, depth, t, shape):
        """Chunky staggered-brick lugs covering the WHOLE crown: recess the tread band
        to a base radius, then union raised blocks back up to the exact OD in
        row-to-row offset rows (so lugs cross the contact-patch centre, not just the
        shoulders) plus bold shoulder lugs that wrap each tread edge. Deep grooves,
        aggressive, even coverage across the width, OD preserved."""
        lo, hi = self._tread_band(ro, width, shape)
        band = hi - lo
        base = ro - depth
        rows = t["rows"]
        # blocks per row scale with circumference; larger pitch ⇒ fewer, chunkier lugs.
        N = max(9, min(16, int(round(2 * math.pi * ro / t["pitch"]))))
        pitch = 360.0 / N
        arc_w = (2 * math.pi * base / N) * t["fill"]
        row_h = band / rows
        # recess the whole tread band (deep grooves everywhere), then add lugs back.
        recess = (cq.Workplane("XY").workplane(offset=lo).circle(ro + 5).circle(base)
                  .extrude(band))
        body = body.cut(recess)

        def _block(ang, zc, ax, cw):
            return (cq.Workplane("XY").box(depth + 3, cw, ax, centered=(True, True, True))
                    .translate((base + (depth + 3) / 2 - 0.5, 0, zc))
                    .rotate((0, 0, 0), (0, 0, 1), ang)).val()

        lugs: list = []
        for ridx in range(rows):                      # centre + intermediate rows
            zc = lo + row_h * (ridx + 0.5)
            off = (ridx % 2) * 0.5                     # brick stagger row-to-row
            for k in range(N):
                lugs.append(_block(pitch * (k + off), zc, row_h * 0.92, arc_w))
        if t.get("shoulder"):                          # bold lugs wrapping each edge
            for edge in (lo, hi):
                for k in range(N):
                    lugs.append(_block(360.0 * (k + 0.25) / N, edge,
                                       row_h * 0.85, arc_w * 1.1))
        body = body.union(cq.Workplane(obj=cq.Compound.makeCompound(lugs)))
        # trim lug tops back to exactly ro so the OD is preserved
        body = body.cut(cq.Workplane("XY").circle(ro + 60).circle(ro)
                        .extrude(width * 3).translate((0, 0, -width)))
        return body

    def _groove_tread(self, body, ro, ri, width, depth, t, shape):
        """Street/racing: shallow circumferential grooves across the crown, plus (for
        street) light staggered lateral sipes. Shallow + printable, stays watertight."""
        lo, hi = self._tread_band(ro, width, shape)
        crown_w = hi - lo
        inner = ro - depth
        # 2–4 shallow grooves depending on width (a narrow tyre gets fewer): keeps the
        # rolling surface clean/printable rather than overly busy.
        n = max(2, min(t["circ"], int(crown_w / 5.5)))
        for i in range(n):
            z = lo + crown_w * (i + 1) / (n + 1)
            gw = min(crown_w / (2 * n + 2), 3.0)
            ring = (cq.Workplane("XY").workplane(offset=z - gw / 2.0)
                    .circle(ro + 2.0).circle(inner).extrude(gw))
            body = body.cut(ring)
        if t.get("sipes"):
            # shallow lateral sipes, one compound cut (cutters don't overlap).
            blocks = max(16, int(2 * math.pi * ro / 20.0))
            sipe_w = max(1.2, (2 * math.pi * ro / blocks) * 0.18)
            sd = depth * 0.55
            cutters: list = []
            for k in range(blocks):
                ang = 360.0 * k / blocks
                cutters.append((cq.Workplane("XY")
                                .box(sd + 2.0, sipe_w, crown_w * 0.92,
                                     centered=(True, True, True))
                                .translate((ro + 1.0 - (sd + 2.0) / 2.0, 0, width / 2))
                                .rotate((0, 0, 0), (0, 0, 1), ang)).val())
            body = body.cut(cq.Workplane(obj=cq.Compound.makeCompound(cutters)))
        return body
