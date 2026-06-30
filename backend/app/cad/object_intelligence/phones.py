"""Curated local phone presets (official manufacturer outline dimensions).

Dimensions are from the manufacturer's published specs and drive LunaiCAD's own
parametric holder — no copied CAD. ``local_verified`` trust (the holder still ships
REVIEW until fit clearances are confirmed, since case/grip tolerances vary).

Sources (official):
  * iPhone 15 — Apple "iPhone 15 — Technical Specifications" (147.6 × 71.6 × 7.80 mm).
  * iPhone 15 Pro — Apple specs (146.6 × 70.6 × 8.25 mm).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PhonePreset:
    id: str
    display_name: str
    source: str
    length_mm: float       # height (long axis)
    width_mm: float
    depth_mm: float        # thickness
    charging_port: str = "usb_c"   # bottom-centre


PHONE_PRESETS = {
    "iphone_15": PhonePreset("iphone_15", "iPhone 15",
                             "Apple iPhone 15 Technical Specifications",
                             147.6, 71.6, 7.80, "usb_c"),
    "iphone_15_pro": PhonePreset("iphone_15_pro", "iPhone 15 Pro",
                                 "Apple iPhone 15 Pro Technical Specifications",
                                 146.6, 70.6, 8.25, "usb_c"),
    "iphone_14": PhonePreset("iphone_14", "iPhone 14",
                             "Apple iPhone 14 Technical Specifications",
                             146.7, 71.5, 7.80, "lightning"),
}

_PHONE_PATTERNS = [
    (re.compile(r"\biphone\s*15\s*pro\b", re.I), "iphone_15_pro"),
    (re.compile(r"\biphone\s*15\b", re.I), "iphone_15"),
    (re.compile(r"\biphone\s*14\b", re.I), "iphone_14"),
]


def detect_phone(prompt: str) -> PhonePreset | None:
    for pat, pid in _PHONE_PATTERNS:
        if pat.search(prompt or ""):
            return PHONE_PRESETS[pid]
    return None
