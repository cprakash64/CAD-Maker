"""Curated local mechanical STANDARDS data (NEMA stepper faces, deep-groove ball
bearings, …). Exact, citable, and used to drive deterministic parametric CAD —
``local_verified`` trust, so the geometry may PASS.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- NEMA stepper motor faces (NEMA ICS 16-2001 frame sizes) ----------------
@dataclass(frozen=True)
class NemaFace:
    nema: int
    body_mm: float            # square body width / face size
    bolt_spacing_mm: float    # square mounting-hole pitch (centre-to-centre)
    bolt_hole_mm: float       # mounting screw clearance (for the bolt size)
    bolt_size: str
    pilot_diameter_mm: float  # raised centre boss / pilot
    shaft_diameter_mm: float


# Source: NEMA ICS 16-2001 / standard stepper datasheets.
NEMA_FACES = {
    17: NemaFace(17, 42.3, 31.0, 3.4, "M3", 22.0, 5.0),
    23: NemaFace(23, 56.4, 47.14, 5.5, "M5", 38.1, 6.35),
    11: NemaFace(11, 28.2, 23.0, 2.5, "M2.5", 22.0, 5.0),
    14: NemaFace(14, 35.2, 26.0, 3.4, "M3", 22.0, 5.0),
}


def nema_face(size: int) -> NemaFace | None:
    return NEMA_FACES.get(int(size))


# --- deep-groove ball bearings (metric, e.g. 608, 6000-series) --------------
@dataclass(frozen=True)
class Bearing:
    name: str
    bore_mm: float        # inner diameter (d)
    outer_mm: float       # outer diameter (D)
    width_mm: float       # width (B)


# Source: ISO 15 / standard bearing dimension tables.
BEARINGS = {
    "608": Bearing("608", 8.0, 22.0, 7.0),
    "623": Bearing("623", 3.0, 10.0, 4.0),
    "625": Bearing("625", 5.0, 16.0, 5.0),
    "626": Bearing("626", 6.0, 19.0, 6.0),
    "688": Bearing("688", 8.0, 16.0, 5.0),
    "6000": Bearing("6000", 10.0, 26.0, 8.0),
    "6001": Bearing("6001", 12.0, 28.0, 8.0),
    "6002": Bearing("6002", 15.0, 32.0, 9.0),
    "6200": Bearing("6200", 10.0, 30.0, 9.0),
    "6201": Bearing("6201", 12.0, 32.0, 10.0),
}


def bearing(name: str) -> Bearing | None:
    return BEARINGS.get(str(name).strip())
