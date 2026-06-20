"""Advanced demo template: inline-4 internal-combustion crankshaft.

Built along Z for natural CadQuery extrudes, then rotated to lie horizontally
along X. Layout (front -> rear):

    snout | M1 | web | R1 | web | M2 | web | R2 | web | M3 | web | R3 | web |
    M4 | web | R4 | web | M5 | flange

5 main journals on the central axis; 4 rod journals offset by the throw radius
with inline-4 phasing [0, 180, 180, 0]; 8 crank webs with counterweights on the
side opposite each rod journal; a front keyed snout and a rear flywheel flange
with bolt holes and a center pilot.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, DimensionSpec
from app.schemas.design_spec import DesignSpec

# Inline-4 journal phasing (degrees): outer pair up, inner pair down.
_PHASES = [0, 180, 180, 0]


class CrankshaftTemplate(BaseTemplate):
    object_type = "inline_4_crankshaft"
    name = "Inline-4 Crankshaft"
    description = (
        "Realistic crankshaft for a 4-cylinder inline engine: 5 main journals, "
        "4 throw-offset rod journals, counterweighted webs, keyed front snout "
        "and a bolted rear flywheel flange."
    )
    dimensions = [
        DimensionSpec("total_length_mm", "Total length", 420.0, 120.0, 1500.0),
        DimensionSpec("main_journal_count", "Main journals", 5.0, 5.0, 5.0),
        DimensionSpec("rod_journal_count", "Rod journals", 4.0, 4.0, 4.0),
        DimensionSpec("main_journal_diameter_mm", "Main journal Ø", 55.0, 10.0, 200.0),
        DimensionSpec("main_journal_width_mm", "Main journal width", 28.0, 5.0, 200.0),
        DimensionSpec("rod_journal_diameter_mm", "Rod journal Ø", 45.0, 10.0, 200.0),
        DimensionSpec("rod_journal_width_mm", "Rod journal width", 32.0, 5.0, 200.0),
        DimensionSpec("throw_radius_mm", "Throw radius", 45.0, 5.0, 200.0),
        DimensionSpec("web_thickness_mm", "Web thickness", 18.0, 4.0, 100.0),
        DimensionSpec("front_snout_diameter_mm", "Front snout Ø", 35.0, 8.0, 150.0),
        DimensionSpec("front_snout_length_mm", "Front snout length", 45.0, 10.0, 300.0),
        DimensionSpec("keyway_width_mm", "Keyway width", 8.0, 0.0, 40.0),
        DimensionSpec("keyway_depth_mm", "Keyway depth", 4.0, 0.0, 30.0),
        DimensionSpec("flywheel_flange_diameter_mm", "Flange Ø", 95.0, 30.0, 400.0),
        DimensionSpec("flywheel_flange_thickness_mm", "Flange thickness", 18.0, 5.0, 100.0),
        DimensionSpec("flywheel_bolt_count", "Flange bolt count", 6.0, 0.0, 12.0),
        DimensionSpec("counterweights", "Counterweights (1=on)", 1.0, 0.0, 1.0),
        DimensionSpec("fillets", "Fillets (1=on)", 1.0, 0.0, 1.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        md = r["main_journal_diameter_mm"]
        mw = r["main_journal_width_mm"]
        rd = r["rod_journal_diameter_mm"]
        rw = r["rod_journal_width_mm"]
        throw = r["throw_radius_mm"]
        web_t = r["web_thickness_mm"]
        snout_d, snout_l = r["front_snout_diameter_mm"], r["front_snout_length_mm"]
        flange_d, flange_t = r["flywheel_flange_diameter_mm"], r["flywheel_flange_thickness_mm"]
        bolts = int(round(r["flywheel_bolt_count"]))
        counterweights = r["counterweights"] > 0.5
        fillets = r["fillets"] > 0.5

        web_r = md / 2.0 + 6.0  # main-side web boss radius
        rod_boss_r = rd / 2.0 + 5.0

        z = 0.0
        parts: list[cq.Workplane] = []

        def cyl(radius: float, height: float, x: float, y: float, z0: float) -> cq.Workplane:
            return (
                cq.Workplane("XY")
                .workplane(offset=z0)
                .moveTo(x, y)
                .circle(radius)
                .extrude(height)
            )

        def web(z0: float, sign: int) -> cq.Workplane:
            """Crank cheek connecting the main axis to a rod journal at offset
            ``sign*throw``, with a counterweight lobe on the opposite side."""
            oy = sign * throw
            wp = cq.Workplane("XY").workplane(offset=z0)
            prof = wp.moveTo(0, 0).circle(web_r)
            prof = prof.moveTo(0, oy).circle(rod_boss_r)
            solid = prof.extrude(web_t)
            # Rectangular spine joining the two bosses.
            span = abs(oy)
            spine = (
                cq.Workplane("XY")
                .workplane(offset=z0)
                .moveTo(0, oy / 2.0)
                .rect(2 * min(web_r, rod_boss_r), span + 1)
                .extrude(web_t)
            )
            solid = solid.union(spine)
            if counterweights:
                cw_r = throw * 0.95 + rd / 4.0
                cw = (
                    cq.Workplane("XY")
                    .workplane(offset=z0)
                    .moveTo(0, -sign * (throw * 0.55))
                    .circle(cw_r)
                    .extrude(web_t)
                )
                solid = solid.union(cw)
            return solid

        # --- front snout (keyed) ---
        snout = cyl(snout_d / 2.0, snout_l, 0, 0, z)
        z += snout_l
        parts.append(snout)

        # --- alternating main / web / rod / web ... ---
        n_mains = 5
        for i in range(n_mains):
            parts.append(cyl(md / 2.0, mw, 0, 0, z))
            z += mw
            if i < 4:  # a rod journal section follows every main but the last
                sign = 1 if _PHASES[i] == 0 else -1
                oy = sign * throw
                parts.append(web(z, sign)); z += web_t
                parts.append(cyl(rd / 2.0, rw, 0, oy, z)); z += rw
                parts.append(web(z, sign)); z += web_t

        # --- rear flywheel flange ---
        flange_z = z
        parts.append(cyl(flange_d / 2.0, flange_t, 0, 0, z))
        z += flange_t

        crank = parts[0]
        for p in parts[1:]:
            crank = crank.union(p)

        # Keyway slot in the snout (cut a rectangular notch at the front face).
        kw, kd = r["keyway_width_mm"], r["keyway_depth_mm"]
        if kw > 0 and kd > 0:
            key = (
                cq.Workplane("XY")
                .workplane(offset=-0.5)
                .moveTo(snout_d / 2.0 - kd / 2.0, 0)
                .rect(kd, kw)
                .extrude(min(snout_l * 0.6, snout_l - 1))
            )
            crank = crank.cut(key)

        # Flange bolt holes on a circle + center pilot.
        if bolts > 0:
            bcd = flange_d * 0.7
            hole_d = max(6.0, flange_d * 0.06)
            pts = []
            import math

            for b in range(bolts):
                ang = 2 * math.pi * b / bolts
                pts.append((bcd / 2.0 * math.cos(ang), bcd / 2.0 * math.sin(ang)))
            holes = (
                cq.Workplane("XY")
                .workplane(offset=flange_z - 0.5)
                .pushPoints(pts)
                .circle(hole_d / 2.0)
                .extrude(flange_t + 1)
            )
            crank = crank.cut(holes)
            pilot = cyl(flange_d * 0.12, flange_t + 1, 0, 0, flange_z - 0.5)
            crank = crank.cut(pilot)

        if fillets:
            try:
                crank = crank.edges("|Z").fillet(min(web_t * 0.15, 2.0))
            except Exception:  # noqa: BLE001 - fillets are best-effort
                pass

        # Orient horizontally along X (was built along Z).
        crank = crank.rotate((0, 0, 0), (0, 1, 0), -90)
        return crank


def crankshaft_summary(spec: DesignSpec) -> dict:
    """Structured counts/extents derived from the build parameters (for the UI
    and for geometry-sanity tests)."""
    t = CrankshaftTemplate()
    r = t.resolve(spec)
    return {
        "main_journal_count": int(round(r["main_journal_count"])),
        "rod_journal_count": int(round(r["rod_journal_count"])),
        "web_count": 2 * int(round(r["rod_journal_count"])),
        "flange_bolt_count": int(round(r["flywheel_bolt_count"])),
        "counterweights": r["counterweights"] > 0.5,
        "phases": _PHASES,
    }
