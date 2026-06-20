"""Deterministic feature-graph builders for common non-template parts.

These produce a validated ``CADFeatureGraph`` (data only — the trusted
interpreter compiles it). Used by the offline mock provider and as a safe
reference; a real LLM can emit equivalent graphs via structured output.
"""
from __future__ import annotations

import re


def _num(text: str, *labels: str, default: float | None = None) -> float | None:
    # Prefer "<number> <unit> <label>" (e.g. "8mm bore", "12mm across").
    for label in labels:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm|cm)?\s*" + re.escape(label), text)
        if m:
            return float(m.group(1))
    # Then a tight "<label> [of|=|:] <number>" (e.g. "bore of 8mm").
    for label in labels:
        m = re.search(re.escape(label) + r"\s*(?:of|=|:|is|diameter)?\s*(\d+(?:\.\d+)?)", text)
        if m:
            return float(m.group(1))
    return default


def bearing_housing(shaft_d: float = 20.0) -> dict:
    d = shaft_d
    return {
        "units": "mm",
        "result_id": "m2",
        "operations": [
            {"op": "cylinder", "id": "pillar", "params": {"radius": d, "height": d * 1.6}},
            {"op": "box", "id": "base",
             "params": {"width": d * 3.4, "depth": d * 1.4, "height": d * 0.5},
             "at": (0, 0, d * 0.25)},
            {"op": "boolean_union", "id": "u", "target": "pillar", "tool": "base"},
            {"op": "cut_hole", "id": "bore",
             "target": "u", "params": {"radius": d / 2, "depth": d * 1.9}, "at": (0, 0, -0.1)},
            {"op": "cut_hole", "id": "m1",
             "target": "bore", "params": {"radius": max(2.5, d * 0.15), "depth": d},
             "at": (d * 1.25, 0, -0.1)},
            {"op": "cut_hole", "id": "m2",
             "target": "m1", "params": {"radius": max(2.5, d * 0.15), "depth": d},
             "at": (-d * 1.25, 0, -0.1)},
        ],
    }


def hex_spacer(across_corners: float = 16.0, bore: float = 6.0, height: float = 20.0) -> dict:
    # Keep the hex comfortably larger than the bore (across-flats > bore + 4mm).
    across_corners = max(across_corners, (bore + 4.0) / 0.866)
    return {
        "units": "mm",
        "result_id": "bore",
        "operations": [
            {"op": "hex_prism", "id": "hex", "params": {"diameter": across_corners, "height": height}},
            {"op": "cut_hole", "id": "bore",
             "target": "hex", "params": {"radius": bore / 2, "depth": height + 2}, "at": (0, 0, -1)},
        ],
    }


def pipe_elbow(pipe_d: float = 40.0, wall: float = 5.0, leg: float = 70.0,
               flange_d: float | None = None, flange_t: float = 12.0) -> dict:
    r = pipe_d / 2
    ir = max(1.0, r - wall)
    fd = flange_d or pipe_d * 1.8
    return {
        "units": "mm",
        "result_id": "bore2",
        "operations": [
            # Leg 1 up the Z axis, leg 2 rotated to run along +Y, joined at origin.
            {"op": "cylinder", "id": "leg1", "params": {"radius": r, "height": leg}},
            {"op": "cylinder", "id": "leg2z", "params": {"radius": r, "height": leg}},
            {"op": "rotate", "id": "leg2", "source": "leg2z", "axis": "x", "params": {"angle": -90}},
            {"op": "boolean_union", "id": "elbow", "target": "leg1", "tool": "leg2"},
            # Flange discs at the two open ends.
            {"op": "cylinder", "id": "f1", "params": {"radius": fd / 2, "height": flange_t},
             "at": (0, 0, leg - flange_t)},
            {"op": "cylinder", "id": "f2z", "params": {"radius": fd / 2, "height": flange_t}},
            {"op": "rotate", "id": "f2r", "source": "f2z", "axis": "x", "params": {"angle": -90}},
            {"op": "translate", "id": "f2", "source": "f2r", "params": {"dy": leg - flange_t}},
            {"op": "boolean_union", "id": "wf1", "target": "elbow", "tool": "f1"},
            {"op": "boolean_union", "id": "body", "target": "wf1", "tool": "f2"},
            # Bores through both legs.
            {"op": "cut_hole", "id": "bore1", "target": "body",
             "params": {"radius": ir, "depth": leg + 2}, "at": (0, 0, -1)},
            {"op": "cylinder", "id": "b2z", "params": {"radius": ir, "height": leg + 2}},
            {"op": "rotate", "id": "b2r", "source": "b2z", "axis": "x", "params": {"angle": -90}},
            {"op": "translate", "id": "b2", "source": "b2r", "params": {"dy": -1}},
            {"op": "boolean_cut", "id": "bore2", "target": "bore1", "tool": "b2"},
        ],
    }


def stepped_slot_block(width: float = 80.0, depth: float = 50.0, height: float = 25.0) -> dict:
    return {
        "units": "mm",
        "result_id": "h2",
        "operations": [
            {"op": "box", "id": "blk", "params": {"width": width, "depth": depth, "height": height},
             "at": (0, 0, height / 2)},
            # Stepped slot: a wide shallow recess over a narrow deep slot.
            {"op": "rectangular_cutout", "id": "step", "target": "blk",
             "params": {"width": width * 0.5, "depth": depth + 2, "height": height * 0.4},
             "at": (0, 0, height - height * 0.2)},
            {"op": "rectangular_cutout", "id": "slot", "target": "step",
             "params": {"width": width * 0.25, "depth": depth + 2, "height": height},
             "at": (0, 0, height * 0.5)},
            # Two counterbored holes.
            {"op": "cut_hole", "id": "h1a", "target": "slot",
             "params": {"radius": 3.3, "depth": height + 2}, "at": (width * 0.3, depth * 0.25, -1)},
            {"op": "cut_hole", "id": "h1", "target": "h1a",
             "params": {"radius": 6, "depth": 4}, "at": (width * 0.3, depth * 0.25, height - 4)},
            {"op": "cut_hole", "id": "h2a", "target": "h1",
             "params": {"radius": 3.3, "depth": height + 2}, "at": (-width * 0.3, depth * 0.25, -1)},
            {"op": "cut_hole", "id": "h2", "target": "h2a",
             "params": {"radius": 6, "depth": 4}, "at": (-width * 0.3, depth * 0.25, height - 4)},
        ],
    }


def flange_plate(outer_d: float = 140.0, bolt_count: int = 8, bolt_circle: float = 100.0,
                 bolt_d: float = 11.0, thickness: float = 12.0,
                 center_bore: float = 50.0) -> dict:
    import math
    ops = [
        {"op": "cylinder", "id": "disc", "params": {"radius": outer_d / 2, "height": thickness}},
    ]
    last = "disc"
    if center_bore > 0:
        ops.append({"op": "cut_hole", "id": "bore", "target": "disc",
                    "params": {"radius": center_bore / 2, "depth": thickness + 2}, "at": (0, 0, -1)})
        last = "bore"
    n = max(1, min(int(bolt_count), 64))
    r = bolt_circle / 2
    for i in range(n):
        ang = 2 * math.pi * i / n
        nid = f"h{i}"
        ops.append({"op": "cut_hole", "id": nid, "target": last,
                    "params": {"radius": bolt_d / 2, "depth": thickness + 2},
                    "at": (round(r * math.cos(ang), 3), round(r * math.sin(ang), 3), -1)})
        last = nid
    return {"units": "mm", "result_id": last, "operations": ops}


def shaft_collar(bore: float = 12.0, outer_d: float | None = None, width: float | None = None,
                 screw_d: float = 6.6) -> dict:
    od = outer_d or bore * 2.0
    w = width or max(8.0, bore * 0.8)
    return {
        "units": "mm",
        "result_id": "clamp",
        "operations": [
            {"op": "cylinder", "id": "body", "params": {"radius": od / 2, "height": w}},
            {"op": "cut_hole", "id": "bore", "target": "body",
             "params": {"radius": bore / 2, "depth": w + 2}, "at": (0, 0, -1)},
            # Radial clamp slit on the +X side reaching the bore.
            {"op": "rectangular_cutout", "id": "slit", "target": "bore",
             "params": {"width": od, "depth": 2.5, "height": w + 2}, "at": (od / 2, 0, w / 2)},
            # Clamp screw across the slit (Y axis).
            {"op": "cylinder", "id": "scr", "params": {"radius": screw_d / 2, "height": od + 6}},
            {"op": "rotate", "id": "scrY", "source": "scr", "axis": "x", "params": {"angle": -90}},
            {"op": "translate", "id": "scrPos", "source": "scrY",
             "params": {"dx": od * 0.52, "dy": -(od / 2 + 3), "dz": w / 2}},
            {"op": "boolean_cut", "id": "clamp", "target": "slit", "tool": "scrPos"},
        ],
    }


def from_prompt(prompt: str) -> dict | None:
    """Return a feature-graph dict for a recognized non-template part, else None."""
    t = prompt.lower()
    if "bearing housing" in t or ("bearing" in t and "housing" in t):
        return bearing_housing(_num(t, "shaft", "bore", "for a", default=20.0) or 20.0)
    if ("hex" in t or "hexagon" in t) and ("spacer" in t or "standoff" in t or "nut" in t):
        return hex_spacer(
            across_corners=_num(t, "across", "width", "size", default=16.0) or 16.0,
            bore=_num(t, "through hole", "bore", "hole", "shaft", default=6.0) or 6.0,
            height=_num(t, "long", "tall", "height", "thick", default=20.0) or 20.0,
        )
    if "elbow" in t or ("90" in t and "pipe" in t):
        return pipe_elbow(pipe_d=_num(t, "pipe", "diameter", default=40.0) or 40.0)
    if ("slot" in t and "block" in t) or "stepped slot" in t:
        return stepped_slot_block()
    if "shaft collar" in t or ("collar" in t and "shaft" in t):
        bore = _num(t, "shaft", "bore", "for a", default=12.0) or 12.0
        screw = _screw_clearance(t)
        return shaft_collar(bore=bore, screw_d=screw)
    if "flange plate" in t or ("flange" in t and "bolt circle" in t) or (
        "plate" in t and "bolt circle" in t):
        return flange_plate(
            bolt_count=int(_num(t, "holes", "bolts", "bolt", default=8) or 8),
            bolt_circle=_num(t, "bolt circle", "pcd", "circle", default=100.0) or 100.0,
        )
    return None


def _screw_clearance(text: str) -> float:
    import re
    m = re.search(r"m(\d+(?:\.\d+)?)", text)
    if m:
        d = float(m.group(1))
        return {3: 3.4, 4: 4.5, 5: 5.5, 6: 6.6, 8: 9.0, 10: 11.0}.get(int(d), d + 0.6)
    return 6.6
