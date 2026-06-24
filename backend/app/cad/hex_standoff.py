"""Deterministic hex-standoff / hex-spacer routing + parsing.

A prompt that asks for a HEX standoff / spacer / pillar is routed to one
deterministic builder (:class:`HexStandoffTemplate`) BEFORE the LLM/CadPlan,
generic feature-graph, round-spacer, or generic-part paths — those produced a
ROUND cylinder for a hex part in production. The across-flats dimension is
preserved exactly and a geometry-measured hex audit (see
``app.cad.semantic_audits.audit_hex_standoff``) confirms six flat sides so a
round body can never PASS as a hex standoff.

This module is pure parsing/intent (no CAD kernel, no LLM) so it is cheap and
deterministic on every request.
"""
from __future__ import annotations

import re

from app.cad.templates.hex_standoff import across_corners

ROUTE_DETERMINISTIC_HEX_STANDOFF = "deterministic_hex_standoff"

# "hex" / "hexagonal" / "hexagon" anywhere in the prompt.
_HEX = re.compile(r"\bhex(?:agon(?:al)?)?\b", re.I)
# The part must be a standoff / spacer / pillar (NOT a hex nut / bolt / key /
# head, which are different parts the round-spacer family must not steal).
_STANDOFF = re.compile(
    r"\bstand[- ]?offs?\b|\bspacers?\b|\bpillars?\b|\bstandoffs?\b", re.I)
# Parts that merely contain "hex" but are not standoffs — never route here.
_HEX_OTHER = re.compile(
    r"\bhex (?:nut|bolt|head|key|screw|socket|wrench|driver|cap)\b", re.I)

# Standard metric screw clearance (close-fit) holes, mm. "M4 through hole" on a
# standoff means a clearance bore for an M4 screw.
_M_CLEARANCE = {
    1.6: 1.8, 2.0: 2.4, 2.5: 2.9, 3.0: 3.4, 4.0: 4.5,
    5.0: 5.5, 6.0: 6.6, 8.0: 9.0, 10.0: 11.0, 12.0: 13.5,
}
# A sensible default across-flats for a given screw size (typical metric
# standoff), used only when the prompt names a screw but no across-flats.
_M_ACROSS_FLATS = {
    2.0: 4.0, 2.5: 5.0, 3.0: 5.5, 4.0: 7.0, 5.0: 8.0, 6.0: 10.0, 8.0: 13.0,
}


def is_hex_standoff_prompt(prompt: str) -> bool:
    """True when the prompt asks for a hexagonal standoff / spacer / pillar."""
    t = prompt or ""
    if _HEX_OTHER.search(t):
        return False
    return bool(_HEX.search(t) and _STANDOFF.search(t))


def _num_before(text: str, *labels: str) -> float | None:
    for label in labels:
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:mm|cm)?\s*" + label, text)
        if m:
            return float(m.group(1))
    return None


def _metric_screw(text: str) -> float | None:
    m = re.search(r"\bm\s?(\d+(?:\.\d+)?)\b", text)
    return float(m.group(1)) if m else None


def parse_hex_params(prompt: str) -> dict:
    """Deterministically resolve hex-standoff parameters from the prompt.

    Defaults: 8mm across flats, 20mm long, Ø4mm through bore. An ``M<n>`` screw
    callout sets a clearance bore (and a typical across-flats when none is
    given). An explicit ``solid`` / ``no bore`` request builds a blind body.
    """
    t = (prompt or "").lower()

    screw = _metric_screw(t)
    # Strip the metric callout (e.g. "M4") before number parsing so its digit is
    # never mistaken for an across-flats or bore dimension ("M4 through hole").
    t_dims = re.sub(r"\bm\s?\d+(?:\.\d+)?\b", " ", t)

    across_flats = _num_before(
        t_dims, "across flats", "across-flats", "across the flats", "a/f", "af",
        "wide across flats", "flats", "hex")
    length = _num_before(t_dims, "long", "length", "tall", "height", "high")
    bore = _num_before(
        t_dims, "through bore", "through hole", "through-hole", "thru bore",
        "bore", "inner diameter", "id", "shaft hole")

    solid = bool(re.search(r"\bsolid\b|\bno bore\b|\bno hole\b|\bblind\b", t))

    if bore is None and screw is not None:
        bore = _M_CLEARANCE.get(screw, round(screw * 1.1 + 0.1, 1))
    if solid:
        bore = 0.0
    if bore is None:
        bore = 4.0

    if across_flats is None and screw is not None:
        across_flats = _M_ACROSS_FLATS.get(screw)
    if across_flats is None:
        across_flats = 8.0

    # The bore must fit inside the hex body; keep the user's across-flats and
    # only widen the body if a contradictory bore would exceed it.
    if bore and bore >= across_flats:
        across_flats = round(bore + 2.0, 2)

    if length is None:
        length = 20.0

    return {
        "across_flats_mm": float(across_flats),
        "across_corners_mm": round(across_corners(float(across_flats)), 3),
        "length_mm": float(length),
        "bore_diameter_mm": float(bore),
        "metric_screw": screw,
        "solid": solid,
    }


def hex_dimensions(params: dict) -> dict:
    """DesignSpec dimensions for :class:`HexStandoffTemplate`.

    A zero bore (a solid standoff) is OMITTED rather than passed as 0 — the spec
    schema rejects a zero dimension, and the template defaults an omitted bore to
    'no bore'."""
    dims = {
        "across_flats": params["across_flats_mm"],
        "length": params["length_mm"],
    }
    if params["bore_diameter_mm"] > 0:
        dims["bore_diameter"] = params["bore_diameter_mm"]
    return dims
