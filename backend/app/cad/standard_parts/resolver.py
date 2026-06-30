"""Standard Part Resolver: plain-English prompt → recognized catalog part.

This runs BEFORE the generic missing-dimensions clarification gate. A prompt that
names a standard / catalog mechanical part (a fastener, etc.) is resolved here to
a concrete, fully dimensioned part — its dimensions come from a published
standard, not from the user, so we must never ask "what are the dimensions?".

Currently supported families:
  * ``hex_nut`` — ISO 4032 / DIN 934 regular hex nut

LEGAL / SOURCING NOTE:
McMaster CAD files must not be scraped, cached, redistributed, or used as source
geometry unless LunaiCAD has explicit commercial permission. Recognition maps a
prompt to a public standard; geometry is generated parametrically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.cad.standard_parts.fasteners import HexNutParams, resolve_hex_nut

ROUTE_STANDARD_PART = "standard_part"

# A metric thread callout: M12, M 12, M12x1.75, M12-1.75 … The trailing negative
# lookahead (not "another digit") replaces a word boundary so a pitch suffix like
# "M12x1.75" still captures the nominal "12" (a \b would fail before the 'x').
_METRIC_THREAD = re.compile(r"\bM\s?(\d+(?:\.\d+)?)(?!\.?\d)", re.I)
# An explicit pitch in a thread callout (the number after × / x / -).
_THREAD_PITCH = re.compile(r"\bM\s?\d+(?:\.\d+)?\s*[x×\-]\s*(\d+(?:\.\d+)?)\b", re.I)

# A hex nut, with aliases: "hex nut", "hexagonal nut", "hexagon nut", or a bare
# "nut" when a metric thread is present (e.g. "Make a M12 nut").
_HEX_NUT = re.compile(r"\bhex(?:agon(?:al)?)?\s+nut\b", re.I)
_BARE_NUT = re.compile(r"\bnuts?\b", re.I)
# Nut variants we do NOT model as a plain regular hex nut (avoid silently
# mis-building a locknut/wingnut/etc. as a regular nut).
_OTHER_NUT = re.compile(
    r"\b(wing|cap|dome|acorn|square|coupling|flange|t[- ]?slot|knurled|"
    r"rivet|weld)\s+nut\b", re.I)

# Recognized standard labels in the prompt (maps to the canonical name).
_STANDARDS = {
    "iso 4032": "ISO 4032",
    "iso4032": "ISO 4032",
    "din 934": "DIN 934",
    "din934": "DIN 934",
}
_STANDARD_RE = re.compile(r"\b(iso\s?4032|din\s?934)\b", re.I)


@dataclass(frozen=True)
class StandardPartResolution:
    """A recognized standard part, fully dimensioned and ready to build."""

    family: str                 # "hex_nut"
    object_type: str            # the template object_type, e.g. "hex_nut"
    standard: str               # "ISO 4032"
    thread: str                 # "M12"
    pitch_mm: float
    params: HexNutParams        # the concrete geometry parameters
    standard_was_assumed: bool  # True when no standard was named in the prompt
    assumed_message: str        # user-facing assumption notice
    dimensions: dict            # DesignSpec dimensions (mm) for the template

    def badge(self, thread_representation: str | None = None) -> str:
        """The compact UI badge, e.g.
        'Standard part · ISO 4032 · M12 × 1.75 · Modeled thread'."""
        base = f"Standard part · {self.standard} · {self.thread} × {self.pitch_mm:g}"
        if thread_representation == "modeled":
            return base + " · Modeled thread"
        if thread_representation in ("cosmetic", "failed_to_model_fallback_cosmetic"):
            return base + " · Cosmetic thread"
        return base

    def to_metadata(self) -> dict:
        """Serializable block stored on the design's semantic_json.

        ``thread_representation`` / ``internal_thread_modeled`` here are the INTENT
        (standard fasteners default to modeled); the route reconciles them with the
        actual built geometry via the internal-thread audit before persisting."""
        p = self.params
        representation = "modeled" if p.thread_modeled else "cosmetic"
        return {
            "standard_part": True,
            "family": self.family,
            "standard": self.standard,
            "standard_assumed": self.standard_was_assumed,
            "thread": self.thread,
            "pitch_mm": self.pitch_mm,
            "across_flats_mm": p.across_flats_mm,
            "across_corners_mm": p.across_corners_mm,
            "height_mm": p.height_mm,
            "minor_diameter_mm": p.bore_diameter_mm,
            "bore_diameter_mm": p.bore_diameter_mm,
            "internal_thread_modeled": p.thread_modeled,
            "thread_representation": representation,
            "badge": self.badge(representation),
            "assumed_message": self.assumed_message,
        }


def _named_standard(prompt: str) -> str | None:
    m = _STANDARD_RE.search(prompt or "")
    if not m:
        return None
    return _STANDARDS.get(m.group(1).lower().replace("  ", " ").strip())


def _is_hex_nut_prompt(prompt: str) -> bool:
    t = prompt or ""
    if _OTHER_NUT.search(t):
        return False  # wing/cap/square/… nut — not a plain regular hex nut
    if _HEX_NUT.search(t):
        return True
    # A bare "nut" counts as a regular hex nut ONLY when a metric thread is given
    # (so "Make a M12 nut" resolves, but a vague "a nut" does not).
    return bool(_BARE_NUT.search(t) and _METRIC_THREAD.search(t))


def _resolve_hex_nut(prompt: str) -> StandardPartResolution | None:
    m = _METRIC_THREAD.search(prompt or "")
    if not m:
        return None  # a hex nut with no thread size can't be dimensioned here
    thread = f"M{m.group(1)}"
    named = _named_standard(prompt)
    standard = named or "ISO 4032"
    pitch_m = _THREAD_PITCH.search(prompt or "")
    pitch_mm = float(pitch_m.group(1)) if pitch_m else None

    params = resolve_hex_nut(thread, standard=standard, pitch_mm=pitch_mm)
    if params is None:
        return None  # unsupported size — let the normal pipeline handle it

    # Drive a MODELED internal thread: thread_major_diameter + thread_pitch make
    # the template cut real helical geometry and derive the minor bore. DesignSpec
    # rejects a zero dimension, so only carry a positive chamfer.
    dimensions = {
        "across_flats": params.across_flats_mm,
        "height": params.height_mm,
        "thread_major_diameter": params.nominal_diameter_mm,
        "thread_pitch": params.pitch_mm,
    }
    if params.chamfer_mm and params.chamfer_mm > 0:
        dimensions["chamfer"] = params.chamfer_mm

    assumed = named is None
    if assumed:
        message = (f"Assumed {params.standard} regular {thread} hex nut. You can "
                   "change standard, pitch, material, or thread detail.")
    else:
        message = (f"{params.standard} regular {thread} hex nut "
                   f"({thread} × {params.pitch_mm:g}mm).")

    return StandardPartResolution(
        family="hex_nut",
        object_type="hex_nut",
        standard=params.standard,
        thread=thread,
        pitch_mm=params.pitch_mm,
        params=params,
        standard_was_assumed=assumed,
        assumed_message=message,
        dimensions=dimensions,
    )


def resolve_standard_part(prompt: str) -> StandardPartResolution | None:
    """Resolve a recognized standard/catalog part from a prompt, else None.

    Deterministic intent + standards lookup. None means "not a recognized
    standard part" — the caller falls through to the normal pipeline."""
    if _is_hex_nut_prompt(prompt):
        return _resolve_hex_nut(prompt)
    return None
