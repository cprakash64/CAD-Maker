"""Render-and-repair self-check: compare the prompt against the generated spec's
metadata and fix obvious mismatches once (gear vs pulley, hexagonal, missing
bore, missing holes). Deterministic and provider-agnostic — it repairs specs the
LLM under-specified (e.g. a gear with no teeth).
"""
from __future__ import annotations

import re

from app.schemas.design_spec import DesignSpec, Hole


def repair_spec(prompt: str, spec: DesignSpec) -> tuple[DesignSpec, list[str]]:
    """Return a (possibly repaired) spec and a list of repair notes."""
    t = prompt.lower()
    notes: list[str] = []
    dims = dict(spec.dimensions)
    holes = list(spec.holes)
    fillet = spec.fillet_radius

    if spec.object_type == "simple_gear_or_pulley":
        is_hex = "hex" in t or "hexagon" in t
        is_gear = ("gear" in t) or ("sprocket" in t) or ("teeth" in t) or ("tooth" in t)
        is_pulley = "pulley" in t
        if is_hex and not dims.get("hex"):
            dims["hex"] = 1.0
            dims.pop("tooth_count", None)
            notes.append("Interpreted as a hexagonal outer profile")
        elif is_gear and not is_pulley and not dims.get("tooth_count") and not dims.get("hex"):
            dims["tooth_count"] = 24.0
            notes.append("Added gear teeth (assumed 24)")
        # Shaft → bore.
        if not dims.get("bore_diameter_mm") and "protruding" not in t:
            bore = _shaft_bore(t)
            dims["bore_diameter_mm"] = bore
            notes.append(f"Added a {bore:g}mm shaft bore")
        dims.setdefault("outer_diameter_mm", 60.0)
        dims.setdefault("thickness_mm", 12.0)
        if not fillet:
            fillet = 0.5

    # Plate-like templates: "N holes" requested but none placed → add a row.
    if spec.object_type in ("rectangular_bracket", "drill_jig", "adapter_plate") and not holes:
        n = _hole_count(t)
        if n:
            holes = _row_of_holes(n, dims, spec.object_type)
            notes.append(f"Added {n} holes (positions assumed, centered)")

    if not notes:
        return spec, []
    repaired = spec.model_copy(update={"dimensions": dims, "holes": holes, "fillet_radius": fillet})
    return repaired, notes


def _shaft_bore(text: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:shaft|bore|hole)", text) or re.search(
        r"(?:shaft|bore|hole)\D{0,6}(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else 10.0


def _hole_count(text: str) -> int | None:
    words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8}
    m = re.search(r"(\d+)\s*(?:x\s*)?(?:m\d+\s*)?(?:holes?|bolts?)", text)
    if m:
        return min(int(m.group(1)), 32)
    for w, n in words.items():
        if re.search(rf"\b{w}\b\s+(?:m\d+\s*)?(?:holes?|bolts?)", text):
            return n
    return None


def _row_of_holes(n: int, dims: dict, object_type: str) -> list[Hole]:
    width = dims.get("width") or dims.get("length") or 80.0
    span = width / 2 - min(width * 0.2, 12.0)
    if n == 1:
        xs = [0.0]
    else:
        xs = [(-span + 2 * span * i / (n - 1)) for i in range(n)]
    return [Hole(diameter=5.5, x=round(x, 2), y=0.0) for x in xs]
