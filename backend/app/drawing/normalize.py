"""Drawing dimension normalization + decimal-sanity correction.

Vision OCR on mechanical drawings loses decimal points: 14.8 becomes 148,
0.5 becomes 5, Ø1 becomes Ø10 — inflating the whole part 10×. The inverse also
happens (drawing-scale cm values that need ×10). This module makes ONE explicit
decision about the scale of a dimension set:

* every candidate factor (×1, ÷10, ÷100, ×10, ×25.4 for inches) is applied to
  the WHOLE set and scored for self-consistency: per-key plausibility bands
  (a flange OD is 40–800mm, a wall 1.5–40mm, a bolt hole 2.5–40mm, …) plus
  family proportion checks (bolt Ø vs flange OD, wall vs pipe OD, flange OD vs
  pipe OD);
* a prior favors ×1 — we never rescale without evidence — strengthened when the
  raw values carry decimal points (decimals surviving OCR mean the numbers are
  probably right as written);
* after the set-level factor, individual keys that are still 10× outside their
  plausibility band are repaired ÷10 with an explicit "suspected lost decimal
  point" assumption (mixed decimal loss hits some values and not others).

Every correction is recorded as an assumption; suspicion that couldn't be
resolved becomes a warning. Explicit units are always obeyed (inch → ×25.4,
high-confidence mm → ×1 + warning when implausible).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Candidate whole-set factors, most-trusted first (order breaks ties).
CANDIDATE_FACTORS: tuple[float, ...] = (1.0, 10.0, 0.1, 0.01)
# Advantage the winning factor must have over ×1 before we rescale.
_PRIOR_RAW = 0.75
# Extra ×1 prior when the raw values carry decimal points (OCR kept them).
_PRIOR_DECIMALS = 0.75

# Plausibility bands (mm) by semantic key fragment, first match wins.
_KEY_BANDS: tuple[tuple[tuple[str, ...], float, float], ...] = (
    (("wall",), 1.5, 40.0),
    (("bolt", "screw"), 2.5, 40.0),                      # bolt/screw hole Ø
    (("flange_thickness", "flange thk"), 4.0, 80.0),
    (("flange",), 40.0, 800.0),                          # flange OD / PCD
    (("thickness", "thick"), 1.5, 120.0),
    (("bore", "inner"), 6.0, 800.0),
    (("diameter", "od", "outer"), 15.0, 1000.0),         # pipe/part ODs
    (("length", "height", "width", "depth", "run", "projection"), 25.0, 3000.0),
)
_GENERIC_BAND = (2.0, 3000.0)

# Family proportion checks: (key-a fragments, key-b fragments, lo, hi) → a/b
# band. "+"-separated fragments must ALL appear in the key.
_RATIO_BANDS: tuple[tuple[str, str, float, float], ...] = (
    ("wall", "main+diameter", 0.02, 0.30),            # wall / main pipe OD
    ("flange+outer", "main+diameter", 1.1, 3.5),      # flange OD / main pipe OD
    ("branch+diameter", "main+diameter", 0.25, 1.0),  # branch OD / main OD
    ("flange+thickness", "flange+outer", 0.03, 0.35),
)

_COUNT_HINTS = ("count", "number", "qty", "quantity")


def _is_count_key(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _COUNT_HINTS)


def _band(key: str) -> tuple[float, float]:
    k = key.lower().replace("_", " ")
    for fragments, lo, hi in _KEY_BANDS:
        if any(fr.replace("_", " ") in k for fr in fragments):
            return lo, hi
    return _GENERIC_BAND


def _band_score(value: float, lo: float, hi: float) -> float:
    """1.0 inside the band, decaying with log-distance outside. Values ABOVE
    the band decay twice as fast: OCR decimal loss inflates numbers, so an
    oversize value is stronger evidence of a wrong scale than an undersize one."""
    if value <= 0:
        return 0.0
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        return max(0.0, 1.0 - abs(math.log10(value / lo)))
    return max(0.0, 1.0 - 2.0 * abs(math.log10(value / hi)))


def _find(dims: dict[str, float], fragments: str) -> float | None:
    """First value whose key contains EVERY '+'-separated fragment."""
    frags = [f.replace("_", " ") for f in fragments.split("+")]
    for k, v in dims.items():
        key = k.lower().replace("_", " ")
        if v > 0 and all(f in key for f in frags):
            return v
    return None


def _has_decimals(values: list[float]) -> bool:
    """True when a meaningful share of the raw values carry decimal fractions —
    evidence the OCR pass preserved decimal points."""
    nonint = sum(1 for v in values if abs(v - round(v)) > 1e-9)
    return nonint >= max(1, len(values) // 3)


@dataclass
class NormalizedDimensions:
    dimensions: dict[str, float]        # mm, factor applied + outliers repaired
    hole_diameters: dict[int, float]    # index → mm (holes list, same order)
    factor: float
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score_detail: dict[str, float] = field(default_factory=dict)


def score_candidate(dims: dict[str, float], hole_dias: list[float],
                    factor: float) -> float:
    """Self-consistency score of the whole set at one factor."""
    score = 0.0
    scaled = {k: v * factor for k, v in dims.items() if not _is_count_key(k)}
    for k, v in scaled.items():
        lo, hi = _band(k)
        score += _band_score(v, lo, hi)
    for d in hole_dias:
        score += _band_score(d * factor, 2.5, 40.0)
    # Proportion checks are factor-invariant for a WHOLE-set factor, but they
    # matter for per-key repairs; include them so mixed sets prefer factors
    # that at least keep absolute bands right.
    for a_frag, b_frag, lo, hi in _RATIO_BANDS:
        a, b = _find(scaled, a_frag), _find(scaled, b_frag)
        if a and b and b > 0:
            r = a / b
            score += 0.5 if lo <= r <= hi else 0.0
    return score


def normalize_dimensions(
    dims: dict[str, float],
    hole_diameters: list[float],
    units: str = "mm",
    units_confidence: float = 0.5,
    explicit_units_confidence: float = 0.75,
) -> NormalizedDimensions:
    """Choose ONE explicit factor for the dimension set and repair per-key
    decimal-loss outliers. Counts are never scaled."""
    dims = {k: float(v) for k, v in dims.items() if v is not None and float(v) > 0}
    lengths = [v for k, v in dims.items() if not _is_count_key(k)]
    out_assumptions: list[str] = []
    out_warnings: list[str] = []
    u = (units or "mm").lower()

    # --- explicit units are obeyed -----------------------------------------
    if "inch" in u or u in ("in", '"'):
        factor = 25.4
        out_assumptions.append("Converted inch dimensions to millimetres (×25.4)")
        return _apply(dims, hole_diameters, factor, out_assumptions, out_warnings, {})

    explicit_mm = ("mm" in u or "millim" in u) and units_confidence >= explicit_units_confidence

    # --- candidate scoring ---------------------------------------------------
    detail: dict[str, float] = {}
    if lengths or hole_diameters:
        for f in CANDIDATE_FACTORS:
            detail[f"x{f:g}"] = round(score_candidate(dims, hole_diameters, f), 3)
        prior = _PRIOR_RAW + (_PRIOR_DECIMALS if _has_decimals(lengths) else 0.0)
        best = max(CANDIDATE_FACTORS,
                   key=lambda f: detail[f"x{f:g}"] + (prior if f == 1.0 else 0.0))
    else:
        best = 1.0

    if explicit_mm and best != 1.0:
        # Explicit mm wins, but say WHY we are suspicious instead of silently
        # building an implausible model.
        out_warnings.append(
            "Dimensions are marked mm but look "
            + ("inflated (possible lost decimal points — e.g. 14.8 read as 148)"
               if best < 1.0 else "drawing-scale (implausibly small)")
            + " — double-check the drawing scale")
        best = 1.0
    elif best == 10.0:
        env = max(lengths, default=0)
        out_assumptions.append(
            f"Dimensions (largest {env:g}) look like centimetres / drawing-scale "
            "units — interpreted ×10 as millimetres")
    elif best in (0.1, 0.01):
        out_assumptions.append(
            f"Dimensions look inflated by a lost decimal point (OCR) — corrected "
            f"×{best:g} to a self-consistent millimetre set")

    return _apply(dims, hole_diameters, best, out_assumptions, out_warnings, detail)


def _apply(dims: dict[str, float], hole_diameters: list[float], factor: float,
           assumptions: list[str], warnings: list[str],
           detail: dict[str, float]) -> NormalizedDimensions:
    scaled = {k: (round(v * factor, 3) if not _is_count_key(k) else v)
              for k, v in dims.items()}

    # --- per-key decimal-loss repair (mixed inflation) ----------------------
    # A single value 10× ABOVE its plausibility band, that lands inside the
    # band when ÷10, is a lost decimal point on that one number.
    for k, v in list(scaled.items()):
        if _is_count_key(k) or v <= 0:
            continue
        lo, hi = _band(k)
        if v > hi and lo <= v / 10.0 <= hi:
            scaled[k] = round(v / 10.0, 3)
            assumptions.append(
                f"{k.replace('_', ' ')} {v:g} is implausible for this part — "
                f"read as {v / 10.0:g}mm (suspected lost decimal point)")

    holes = {i: round(d * factor, 3) for i, d in enumerate(hole_diameters) if d > 0}
    return NormalizedDimensions(
        dimensions=scaled, hole_diameters=holes, factor=factor,
        assumptions=assumptions, warnings=warnings, score_detail=detail,
    )
