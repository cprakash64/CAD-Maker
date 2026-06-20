"""Consistent millimetre scaling for drawing dimensions.

2D drawings often carry drawing-scale values rather than real millimetres: a
flanged fitting dimensioned 14.8 / 15 / Ø12 with a "12xØ1" bolt callout is
almost certainly centimetres (or scale units), not a 15mm-tall fitting with
twelve 1mm bolt holes. This module infers ONE consistent factor for the whole
drawing, converts everything to millimetres, and records the inference as an
assumption — never a blocking question.

Rules:
* explicit inch units convert ×25.4;
* unclear units + implausibly small envelope (< 30mm) ⇒ treat as cm (×10);
* explicit mm units are obeyed, with a WARNING when the result is physically
  tiny rather than a silent wrong model;
* a bolt/hole callout that stays implausibly small relative to the part after
  scaling (e.g. Ø1 on a Ø120 flange) is treated as a drawing-scale value and
  rescaled, with an assumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.drawing_spec import DrawingInterpretationSpec

# A real machined fitting/bracket envelope is rarely under 30mm in every axis.
PLAUSIBLE_ENVELOPE_MIN = 30.0
# Bolt clearance holes are rarely under 2.5mm in real parts.
MIN_PLAUSIBLE_HOLE = 2.5
# Units marked at/above this confidence are treated as explicit.
EXPLICIT_UNITS_CONFIDENCE = 0.75

# Dimension keys that are COUNTS, never lengths — exempt from scaling.
_COUNT_KEY_HINTS = ("count", "number", "qty", "quantity")


@dataclass
class ScaledHole:
    diameter: float  # mm
    count: int
    callout: str | None = None


@dataclass
class ScaledDrawing:
    dimensions: dict[str, float] = field(default_factory=dict)  # mm
    holes: list[ScaledHole] = field(default_factory=list)
    scale: float = 1.0
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_count_key(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _COUNT_KEY_HINTS)


def infer_scale(interp: DrawingInterpretationSpec) -> ScaledDrawing:
    """One consistent drawing→mm scale for dimensions AND hole callouts."""
    out = ScaledDrawing()
    dims = {k: float(v) for k, v in (interp.overall_dimensions or {}).items()
            if v is not None and float(v) > 0}
    lengths = {k: v for k, v in dims.items() if not _is_count_key(k)}
    envelope = max(lengths.values(), default=0.0)

    units = str(interp.units or "mm").lower()
    explicit_mm = ("mm" in units or "millim" in units) and \
        interp.drawing_units_confidence >= EXPLICIT_UNITS_CONFIDENCE

    factor = 1.0
    if "inch" in units or units in ("in", '"'):
        factor = 25.4
        out.assumptions.append("Converted inch dimensions to millimetres (×25.4)")
    elif 0 < envelope < PLAUSIBLE_ENVELOPE_MIN:
        if explicit_mm:
            out.warnings.append(
                f"Dimensions are marked mm but the whole part is only "
                f"{envelope:g}mm — double-check the drawing scale")
        else:
            factor = 10.0
            out.assumptions.append(
                f"Dimensions (largest {envelope:g}) look like centimetres / "
                f"drawing-scale units — interpreted ×10 as millimetres")

    out.scale = factor
    out.dimensions = {
        k: round(v * factor, 3) if not _is_count_key(k) else v
        for k, v in dims.items()
    }
    envelope_mm = max((v for k, v in out.dimensions.items() if not _is_count_key(k)),
                      default=0.0)

    for h in interp.holes or []:
        if not h.diameter or h.diameter <= 0:
            continue
        dia = h.diameter * factor
        # A "12xØ1"-style callout that is still tiny relative to a real-size
        # part is a drawing-scale number — rescale it instead of cutting twelve
        # 1mm holes in a Ø120 flange. Same when the envelope is UNKNOWN (the
        # vision pass read the callout but no overall dimensions): no real
        # fitting has Ø1 bolt holes.
        if dia < MIN_PLAUSIBLE_HOLE and (
                envelope_mm >= PLAUSIBLE_ENVELOPE_MIN or envelope_mm == 0):
            rescaled = dia * 10.0
            out.assumptions.append(
                f"Hole callout Ø{h.diameter:g} looks like a drawing-scale value — "
                f"interpreted as Ø{rescaled:g}mm")
            dia = rescaled
            if dia < MIN_PLAUSIBLE_HOLE:
                out.warnings.append(
                    f"Hole Ø{dia:g}mm is still unusually small for this part")
        out.holes.append(ScaledHole(diameter=round(dia, 2),
                                    count=max(1, int(h.count or 1)),
                                    callout=h.callout))
    return out
