"""Best-effort parsing of dimension LABELS from drawing-adjacent text.

Sources: PDF text layers, SVG/DXF text entities, user notes — anywhere the
sheet's printed dimension strings survive as text. (Raster-only uploads carry
no text; they keep the default-scale path.) Recognizes the common callout
grammar of mechanical drawings:

    50   30.5   144°   R1.6   Ø4   ⌀12   12xØ1   4 x Ø3.2   THK 6

Angles and radii never drive the part's envelope; the LARGEST plausible linear
value does (a sheet's biggest printed number is almost always an overall
dimension). Parsing is advisory only — it can upgrade the fallback's scale,
but a parse failure never blocks generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# One dimension token. Groups: hole-count prefix ("12x"), R/Ø marker, value,
# unit/degree suffix.
_TOKEN_RE = re.compile(
    r"(?:(\d{1,3})\s*[x×]\s*)?"          # optional repeat count: "12x"
    r"([RrØø⌀⌀]|[Dd][Ii][Aa]\.?)?\s*"  # optional R / Ø / DIA marker
    r"(\d+(?:[.,]\d+)?)"                  # the number
    r"\s*(°|deg\b|mm\b|in\b|\"|')?",      # optional unit / degree
)

# Plausible printed linear dimension for a machined part (mm).
_LINEAR_MIN, _LINEAR_MAX = 3.0, 3000.0
_THK_RE = re.compile(r"\b(?:thk|thick(?:ness)?|depth)\b[.:\s]*(\d+(?:[.,]\d+)?)", re.I)
# Repeated RADIUS notation ("8xR8", "8 x R6", "4×R13") — a bolt-circle of bosses/
# fillets, the mirror of the "12xØ1" repeated-diameter callout.
_REPEAT_RADIUS_RE = re.compile(r"(\d{1,3})\s*[x×]\s*[Rr]\s*(\d+(?:[.,]\d+)?)")
# Global fillet directive ("All fillet radii 2", "all fillets R5", "fillet all 2mm").
_GLOBAL_FILLET_RE = re.compile(
    r"all\s+fillet(?:\s+rad\w*)?\s*:?\s*[Rr]?\s*(\d+(?:[.,]\d+)?)", re.I)


@dataclass
class ParsedDimensionLabels:
    linear: list[float] = field(default_factory=list)      # plain lengths (mm)
    diameters: list[float] = field(default_factory=list)   # Ø values (mm)
    radii: list[float] = field(default_factory=list)       # R values (mm)
    angles: list[float] = field(default_factory=list)      # degrees
    hole_callouts: list[tuple[int, float]] = field(default_factory=list)  # (n, Ø)
    radius_callouts: list[tuple[int, float]] = field(default_factory=list)  # (n, R)
    global_fillet: float | None = None
    thickness: float | None = None

    @property
    def envelope_mm(self) -> float | None:
        """Largest plausible linear label — the best overall-size estimate."""
        candidates = [v for v in self.linear if _LINEAR_MIN <= v <= _LINEAR_MAX]
        return max(candidates) if candidates else None


def parse_dimension_labels(texts: list[str] | None) -> ParsedDimensionLabels:
    out = ParsedDimensionLabels()
    for text in texts or []:
        if not text:
            continue
        m = _THK_RE.search(text)
        if m and out.thickness is None:
            out.thickness = _num(m.group(1))
        gf = _GLOBAL_FILLET_RE.search(text)
        if gf and out.global_fillet is None:
            out.global_fillet = _num(gf.group(1))
        # Repeated radius bosses/fillets ("8xR8") — captured BEFORE the generic
        # token scan (which would read the R value as a lone radius).
        for cnt, val in _REPEAT_RADIUS_RE.findall(text):
            v = _num(val)
            if v and v > 0:
                out.radius_callouts.append((int(cnt), v))
        for count, marker, num, suffix in _TOKEN_RE.findall(text):
            value = _num(num)
            if value is None or value <= 0:
                continue
            if suffix == "°" or (suffix or "").startswith("deg"):
                out.angles.append(value)
                continue
            if suffix in ('"', "'", "in"):
                value *= 25.4
            mk = (marker or "").lower().rstrip(".")
            is_dia = mk in ("ø", "⌀", "dia")
            if count and is_dia:
                out.hole_callouts.append((int(count), value))
            elif count and mk == "r":
                continue  # "8xR8" already captured as a repeated-radius callout
            elif mk == "r":
                out.radii.append(value)
            elif is_dia:
                out.diameters.append(value)
            else:
                out.linear.append(value)
    return out


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None
