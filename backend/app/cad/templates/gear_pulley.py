"""Gear / pulley template.

Three shapes, chosen by parameters (a *gear* prompt never falls back to a plain
pulley):
  * hex > 0           -> hexagonal outer profile (gear blank / hex part)
  * tooth_count > 0   -> spur gear with that many rim teeth (module-based)
  * otherwise         -> V/flat-belt pulley with a rim groove
Plus a center bore (shaft hole), optional keyway, and optional hub.

Spur gears are sized from the metric MODULE (m), the standard gear pitch unit::

    pitch_diameter = m * z              (z = tooth count)
    outer/tip diameter = m * (z + 2)    (addendum = 1 m)
    root diameter = m * (z - 2.5)       (dedendum = 1.25 m)

Defaults: module 2.0mm, 24 teeth (-> Ø52mm tip), 12mm thick, 8mm bore. Teeth are
an APPROXIMATE trapezoidal profile (a CAD-planning concept, not a certified AGMA/
ISO involute), but the rim ALWAYS has visible alternating tooth/root geometry.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec

# Standard full-depth involute proportions (used as trapezoidal approximations).
_ADDENDUM = 1.0    # * module, tip above pitch circle
_DEDENDUM = 1.25   # * module, root below pitch circle
DEFAULT_MODULE_MM = 2.0
DEFAULT_TOOTH_COUNT = 24


def resolve_gear_geometry(*, tooth_count: int, module_mm: float = 0.0,
                          outer_diameter_mm: float = 0.0,
                          pitch_diameter_mm: float = 0.0) -> dict:
    """Resolve a coherent (module, pitch_d, outer_d, root_d) set from whatever the
    user gave, by metric-gear inference:

      * module given           -> outer = m*(z+2)
      * else outer given       -> m = outer/(z+2)
      * else pitch given       -> m = pitch/z
      * else                   -> default module 2.0

    Pure arithmetic (no geometry); shared by the builder and the prompt parser so
    metadata and the solid always agree.
    """
    z = max(1, int(round(tooth_count)))
    if module_mm and module_mm > 0:
        m = module_mm
    elif outer_diameter_mm and outer_diameter_mm > 0:
        m = outer_diameter_mm / (z + 2)
    elif pitch_diameter_mm and pitch_diameter_mm > 0:
        m = pitch_diameter_mm / z
    else:
        m = DEFAULT_MODULE_MM
    outer_d = outer_diameter_mm if (outer_diameter_mm and outer_diameter_mm > 0) \
        else m * (z + 2)
    pitch_d = m * z
    # Root below pitch by 1.25 m; clamp so a low-tooth gear stays buildable.
    root_d = max(outer_d * 0.4, m * (z - 2.5))
    return {"module_mm": round(m, 4), "pitch_diameter_mm": round(pitch_d, 3),
            "outer_diameter_mm": round(outer_d, 3), "root_diameter_mm": round(root_d, 3)}


class GearPulleyTemplate(BaseTemplate):
    object_type = "simple_gear_or_pulley"
    name = "Gear / Pulley"
    description = "Spur gear (teeth), hexagonal gear blank, or grooved pulley with a bore."
    dimensions = [
        DimensionSpec("outer_diameter_mm", "Outer diameter", 60.0, 8.0, 600.0),
        DimensionSpec("thickness_mm", "Thickness", 12.0, 2.0, 300.0),
        DimensionSpec("bore_diameter_mm", "Bore / shaft hole", 10.0, 0.0, 400.0),
        DimensionSpec("tooth_count", "Teeth (0 = none)", 0.0, 0.0, 200.0),
        DimensionSpec("module_mm", "Gear module (0 = infer)", 0.0, 0.0, 50.0),
        DimensionSpec("pitch_diameter_mm", "Pitch diameter (0 = infer)", 0.0, 0.0, 600.0),
        DimensionSpec("tooth_height_mm", "Tooth height (pulley groove)", 4.0, 0.0, 50.0),
        DimensionSpec("hex", "Hexagonal outer profile (1 = yes)", 0.0, 0.0, 1.0),
        DimensionSpec("hub_diameter_mm", "Hub diameter (0 = none)", 0.0, 0.0, 400.0),
        DimensionSpec("hub_height_mm", "Hub height", 0.0, 0.0, 200.0),
        DimensionSpec("keyway_width_mm", "Keyway width (0 = none)", 0.0, 0.0, 40.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        od = r["outer_diameter_mm"]
        thk = r["thickness_mm"]
        bore = r["bore_diameter_mm"]
        teeth = int(round(r["tooth_count"]))
        th = r["tooth_height_mm"]
        hexagonal = r["hex"] > 0.5

        if bore >= od:
            raise CadGenerationError("bore must be smaller than the outer diameter")

        if hexagonal:
            # Hexagonal outer profile (across-corners diameter = od).
            part = cq.Workplane("XY").polygon(6, od).extrude(thk)
        elif teeth > 0:
            # MODULE-BASED SPUR GEAR. Resolve a coherent gear from whatever sizing
            # the user gave (module / outer / pitch), then extrude a single closed
            # trapezoidal-tooth outline -> one watertight, manifold solid that
            # ALWAYS has visible alternating tooth/root geometry (never a disc).
            g = resolve_gear_geometry(
                tooth_count=teeth, module_mm=r["module_mm"],
                outer_diameter_mm=od, pitch_diameter_mm=r["pitch_diameter_mm"])
            tip_r = g["outer_diameter_mm"] / 2.0
            root_r = g["root_diameter_mm"] / 2.0
            od = g["outer_diameter_mm"]  # keep bbox metadata consistent with teeth
            if root_r <= bore / 2.0:
                raise CadGenerationError("teeth too tall for this diameter/bore")
            part = cq.Workplane("XY").polyline(
                _gear_profile_points(tip_r, root_r, teeth)
            ).close().extrude(thk)
        else:
            # Pulley: disc with a central rim groove.
            part = cq.Workplane("XY").circle(od / 2.0).extrude(thk)
            groove_r = od / 2.0 - max(2.0, th)
            if groove_r > bore / 2.0 + 1:
                groove = (
                    cq.Workplane("XY")
                    .workplane(offset=thk / 3.0)
                    .circle(od / 2.0 + 1)
                    .circle(groove_r)
                    .extrude(thk / 3.0)
                )
                part = part.cut(groove)

        # Optional hub on top.
        hub_d, hub_h = r["hub_diameter_mm"], r["hub_height_mm"]
        if hub_d > bore and hub_h > 0:
            hub = cq.Workplane("XY").workplane(offset=thk).circle(hub_d / 2.0).extrude(hub_h)
            part = part.union(hub)

        # Center bore through everything (+ optional keyway).
        if bore > 0:
            total_h = thk + (hub_h if (hub_d > bore and hub_h > 0) else 0) + 2
            cutter = cq.Workplane("XY").workplane(offset=-1).circle(bore / 2.0).extrude(total_h)
            part = part.cut(cutter)
            kw = r["keyway_width_mm"]
            if kw > 0:
                key = (
                    cq.Workplane("XY")
                    .workplane(offset=-1)
                    .moveTo(bore / 2.0, 0)
                    .rect(kw, kw)
                    .extrude(total_h)
                )
                part = part.cut(key)
        return part


def _gear_profile_points(tip_r: float, root_r: float, teeth: int) -> list[tuple[float, float]]:
    """Vertices of a single closed spur-gear outline, traversed once anticlockwise.

    Each pitch contributes a TRAPEZOIDAL tooth: a root valley, sloped flanks, and
    a narrower tip land (an involute approximation). The polygon is simple
    (non self-intersecting) and extrudes to one watertight, manifold solid with
    ``teeth`` clear tooth peaks and ``teeth`` root valleys — never a smooth disc.
    """
    pts: list[tuple[float, float]] = []
    pitch = 2 * math.pi / teeth
    half_tip = 0.22 * pitch    # half tooth land at the tip (narrow)
    half_root = 0.40 * pitch   # half tooth base at the root (wider -> sloped flank)

    def pt(r: float, a: float) -> tuple[float, float]:
        return (r * math.cos(a), r * math.sin(a))

    for k in range(teeth):
        center = (k + 0.5) * pitch  # tooth centered in its pitch slot
        pts.append(pt(root_r, k * pitch))              # valley start
        pts.append(pt(root_r, center - half_root))     # base of rising flank
        pts.append(pt(tip_r, center - half_tip))       # up the flank to the tip
        pts.append(pt(tip_r, center + half_tip))       # across the tip land
        pts.append(pt(root_r, center + half_root))     # down the falling flank
    return pts
