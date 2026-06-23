"""Deterministic spur-gear routing + parsing.

Any prompt that mentions a gear (gear / spur gear / tooth / teeth / module /
pitch diameter / sprocket / cog) is routed to ONE deterministic builder so it can
never reach the LLM/CadPlan, generic feature-graph, pulley, or generic-part paths
that produced a smooth disc in production. The geometry itself is built by the
module-based :class:`GearPulleyTemplate` (a single extruded toothed profile).

This module is pure parsing/intent (no CAD kernel, no LLM) so it is cheap and
deterministic on every request.
"""
from __future__ import annotations

import re

from app.cad.templates.gear_pulley import (
    DEFAULT_TOOTH_COUNT,
    resolve_gear_geometry,
)

ROUTE_DETERMINISTIC_SPUR_GEAR = "deterministic_spur_gear"

# Strong gear signals — these always mean "build a single spur gear".
_STRONG_GEAR = re.compile(
    r"\bspur gears?\b|\bteeth\b|\btooth\b|\bsprockets?\b|\bcogs?\b"
    r"|\bpitch diameter\b|\bmodule\s*\d|\d\s*module\b",
    re.I,
)
# A bare "gear" / "module" only means a gear when it is NOT part of one of these
# phrases (which name a vehicle subsystem or an assembly, not a single spur gear).
_NOT_A_GEAR = re.compile(
    r"landing gear|steering gear|running gear|gear ?up\b|gear ?down\b|gear shift"
    r"|gear ?stick|gear lever|gear selector|gear ?box|gearbox|gear train"
    r"|gear pump|gear motor|gear reduction|gear ?head|gear assembly|gear drive"
    r"|reduction gear|timing gear",
    re.I,
)
_BARE_GEAR = re.compile(r"\bgears?\b", re.I)
# Explicit "don't actually make teeth" qualifier — build a smooth disc so the
# semantic audit can demonstrate the safety net (a smooth disc never PASSES as a
# gear). Honors a deliberately contradictory request rather than hiding it.
_SMOOTH_DISC = re.compile(
    r"\bsmooth\b|\bplain disc\b|\bplain disk\b|\bno teeth\b|\bwithout teeth\b"
    r"|\btoothless\b",
    re.I,
)
_SQUARE_BORE = re.compile(r"\bsquare bore\b|\bkeyed\b|\bkeyway\b|\bkey way\b", re.I)


def is_gear_prompt(prompt: str) -> bool:
    """True when the prompt asks for a single spur gear.

    Strong signals (teeth / tooth / spur gear / sprocket / cog / 'NNt' / pitch
    diameter) always qualify. A bare 'gear' or 'module' qualifies UNLESS it is
    part of a vehicle-subsystem / assembly phrase ('landing gear', 'gearbox',
    'gear pump', …), which are not single spur gears. A bare 'pulley' never
    qualifies."""
    t = prompt or ""
    if _NOT_A_GEAR.search(t):
        # Only block when there is no independent strong gear signal.
        if not _STRONG_GEAR.search(t):
            return False
    if _STRONG_GEAR.search(t):
        return True
    return bool(_BARE_GEAR.search(t))


def _num_before(text: str, *labels: str) -> float | None:
    for label in labels:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:mm|cm)?\s*" + label, text)
        if m:
            return float(m.group(1))
    return None


def parse_gear_params(prompt: str) -> dict:
    """Deterministically resolve the spur-gear parameters from the prompt.

    Inference (standard approximate spur-gear proportions):
      * module given           -> OD = module * (z + 2)
      * else OD given          -> module = OD / (z + 2)
      * else pitch given       -> module = pitch / z
      * else                   -> default module 2.0
    Defaults: 24 teeth, module 2mm, 12mm thick, Ø8mm circular bore.
    """
    t = (prompt or "").lower()

    teeth = _num_before(t, "teeth", "tooth", "-tooth", "t gear", "t spur")
    z = int(teeth) if teeth else DEFAULT_TOOTH_COUNT

    module = _num_before(t, "module", "mod ")
    outer = _num_before(t, "outside diameter", "outer diameter", "od", "tip diameter")
    pitch = _num_before(t, "pitch diameter", "pitch dia", "pitch")
    bore = _num_before(t, "center bore", "centre bore", "bore", "shaft hole", "shaft")
    thickness = _num_before(t, "thick", "thickness", "wide", "width", "face width")

    g = resolve_gear_geometry(
        tooth_count=z, module_mm=module or 0.0,
        outer_diameter_mm=outer or 0.0, pitch_diameter_mm=pitch or 0.0)

    smooth = bool(_SMOOTH_DISC.search(t))
    square_bore = bool(_SQUARE_BORE.search(t))
    return {
        "tooth_count": z,
        "module_mm": g["module_mm"],
        "outside_diameter_mm": g["outer_diameter_mm"],
        "pitch_diameter_mm": g["pitch_diameter_mm"],
        "root_diameter_mm": g["root_diameter_mm"],
        "thickness_mm": thickness or 12.0,
        "bore_diameter_mm": bore if bore is not None else 8.0,
        "smooth_disc": smooth,
        "square_bore": square_bore,
    }


def gear_dimensions(params: dict) -> dict:
    """DesignSpec dimensions for the gear/pulley template from parsed params.

    When ``smooth_disc`` is set we deliberately build a TOOTHLESS disc (tooth_count
    0) so the semantic audit fails it — proving a smooth disc can never PASS as a
    gear."""
    dims = {
        "outer_diameter_mm": params["outside_diameter_mm"],
        "thickness_mm": params["thickness_mm"],
        "bore_diameter_mm": params["bore_diameter_mm"],
        "module_mm": params["module_mm"],
        "pitch_diameter_mm": params["pitch_diameter_mm"],
    }
    if not params.get("smooth_disc"):
        dims["tooth_count"] = float(params["tooth_count"])
    if params.get("square_bore"):
        # A square/keyed bore is modelled as a keyway slot in the round bore.
        dims["keyway_width_mm"] = max(2.0, params["bore_diameter_mm"] * 0.3)
    return dims
