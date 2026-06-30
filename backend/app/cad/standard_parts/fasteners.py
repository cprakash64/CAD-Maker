"""Parametric resolution of fastener standard parts (hex nuts to start).

Pure data → parameters: given a thread size and a standard, produce a fully
dimensioned, geometry-ready parameter set for a catalog fastener. No CAD kernel
and no LLM, so the result is deterministic on every request.

LEGAL / SOURCING NOTE:
McMaster CAD files must not be scraped, cached, redistributed, or used as source
geometry unless LunaiCAD has explicit commercial permission. Parameters here are
computed from public ISO/DIN dimensional tables, never from vendor CAD files.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.cad.standard_parts.standards_data import (
    DEFAULT_HEX_NUT_STANDARD,
    coarse_pitch_mm,
    hex_nut_dimensions,
    thread_minor_diameter_mm,
)


@dataclass(frozen=True)
class HexNutParams:
    """A fully dimensioned regular hex nut, geometry-ready (all mm)."""

    standard: str            # "ISO 4032" | "DIN 934"
    thread: str              # "M12"
    nominal_diameter_mm: float
    pitch_mm: float
    across_flats_mm: float
    across_corners_mm: float
    height_mm: float
    bore_diameter_mm: float   # cosmetic tapped through-bore (minor diameter)
    chamfer_mm: float
    thread_modeled: bool      # False = cosmetic (bore only, no thread form)

    def summary(self) -> str:
        return (f"{self.standard} regular hex nut, {self.thread} × {self.pitch_mm:g}mm "
                f"coarse: {self.across_flats_mm:g}mm across flats "
                f"(≈{self.across_corners_mm:g}mm across corners), "
                f"{self.height_mm:g}mm high.")


def resolve_hex_nut(thread: str, standard: str | None = None,
                    pitch_mm: float | None = None) -> HexNutParams | None:
    """Resolve a regular hex nut from a thread size like ``"M12"``.

    Defaults to ISO 4032 / DIN 934 nominal dimensions. Coarse pitch is used when
    none is given. Returns None for an unsupported size (caller can clarify)."""
    std = (standard or DEFAULT_HEX_NUT_STANDARD).strip()
    key = thread.upper().strip()
    dims = hex_nut_dimensions(key, std)
    if dims is None:
        return None

    try:
        nominal = float(key.lstrip("Mm"))
    except ValueError:
        return None

    pitch = float(pitch_mm) if pitch_mm else (coarse_pitch_mm(nominal) or round(nominal * 0.15, 3))
    bore = thread_minor_diameter_mm(nominal, pitch)
    # Chamfer the bearing faces: bounded so it never eats the flats or the height.
    radial_slack = (dims["across_corners"] - dims["across_flats"]) / 2.0
    chamfer = round(min(radial_slack * 0.85, dims["height"] * 0.15), 2)

    return HexNutParams(
        standard=std,
        thread=key,
        nominal_diameter_mm=nominal,
        pitch_mm=pitch,
        across_flats_mm=dims["across_flats"],
        across_corners_mm=dims["across_corners"],
        height_mm=dims["height"],
        bore_diameter_mm=bore,
        chamfer_mm=chamfer,
        # Intent: standard fasteners default to a MODELED internal thread. The
        # actual representation is reconciled from the built geometry by the
        # internal-thread audit (it may fall back to cosmetic if the kernel can't
        # produce a valid watertight modeled thread for a given size).
        thread_modeled=True,
    )
