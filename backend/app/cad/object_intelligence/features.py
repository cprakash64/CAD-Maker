"""Requested-vs-generated feature contract.

Parses the FEATURES a user explicitly asked for from a prompt (USB-C cutout,
mounting holes, removable lid, ventilation, logo area, …) and diffs them against
what the build actually produced. A required feature that the geometry does not
contain blocks PASS — so a custom enclosure that "forgets" the requested USB-C
cutout can never be reported as exact/PASS.
"""
from __future__ import annotations

import re

# Canonical port cutout opening sizes (width × height mm), used by builders that
# cut user-requested ports as true through-wall openings.
PORT_CUTOUT_SIZES: dict[str, tuple[float, float]] = {
    "usb_c": (9.5, 3.5),
    "usb_a": (15.0, 16.5),
    "micro_usb": (8.0, 4.0),
    "micro_hdmi": (7.5, 4.5),
    "hdmi": (15.5, 7.0),
    "ethernet": (16.0, 13.5),
    "audio": (7.0, 6.0),
    "microsd": (12.0, 2.5),
    "power": (9.5, 11.0),
}

# Prompt phrase -> canonical feature id. Required features (must be present to PASS)
# vs advisory (cosmetic) are split by REQUIRED_FEATURES below.
_FEATURE_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("usb_c_cutout", re.compile(r"\busb[- ]?c\b", re.I)),
    ("usb_a_cutout", re.compile(r"\busb[- ]?a\b|\busb ports?\b", re.I)),
    ("micro_usb_cutout", re.compile(r"\bmicro[- ]?usb\b", re.I)),
    ("micro_hdmi_cutout", re.compile(r"\bmicro[- ]?hdmi\b", re.I)),
    ("hdmi_cutout", re.compile(r"(?<!micro[- ])(?<!micro )\bhdmi\b", re.I)),
    ("ethernet_cutout", re.compile(r"\bethernet\b|\brj45\b", re.I)),
    ("audio_cutout", re.compile(r"\baudio jack\b|\bheadphone\b|\b3\.5\s*mm jack\b", re.I)),
    ("microsd_access", re.compile(r"\bmicro[- ]?sd\b|\bsd card\b", re.I)),
    ("power_cutout", re.compile(r"\bpower (?:jack|port|cutout)\b|\bbarrel jack\b|\bdc jack\b", re.I)),
    ("mounting_holes", re.compile(r"\bmounting holes?\b|\bscrew holes?\b|\bmount posts?\b|\bmounting posts?\b", re.I)),
    ("removable_lid", re.compile(r"\bremovable lid\b|\bremovable top\b|\bremovable cover\b", re.I)),
    ("snap_fit_lid", re.compile(r"\bsnap[- ]?fit\b", re.I)),
    ("ventilation", re.compile(r"\bvent(?:ilation|s)?\b|\bvent slots?\b|\bairflow\b", re.I)),
    ("logo_area", re.compile(r"\blogo\b|\bemboss\w*\b", re.I)),
]

# Features that, when REQUESTED, must be present in the geometry to allow PASS.
REQUIRED_FEATURES = {
    "usb_c_cutout", "usb_a_cutout", "micro_usb_cutout", "micro_hdmi_cutout",
    "hdmi_cutout", "ethernet_cutout", "audio_cutout", "microsd_access",
    "power_cutout", "mounting_holes",
    # phone-holder structural features
    "cradle", "back_support", "bottom_lip", "cable_notch",
    # tire / rim / wheel structural features
    "tire_body", "tread_pattern", "no_rim", "hollow_tire_body", "center_opening",
    "sidewalls", "bead_lips", "rim_barrel", "center_bore", "bead_seat", "no_tire",
    "spokes", "solid_disc",
}

# feature id -> the PORT_CUTOUT_SIZES key it maps to (for builders).
FEATURE_TO_PORT = {
    "usb_c_cutout": "usb_c", "usb_a_cutout": "usb_a", "micro_usb_cutout": "micro_usb",
    "micro_hdmi_cutout": "micro_hdmi", "hdmi_cutout": "hdmi",
    "ethernet_cutout": "ethernet", "audio_cutout": "audio",
    "microsd_access": "microsd", "power_cutout": "power",
}


def parse_requested_features(prompt: str) -> list[str]:
    """Canonical feature ids the user explicitly asked for, in prompt order."""
    t = prompt or ""
    out: list[str] = []
    for fid, pat in _FEATURE_PATTERNS:
        if pat.search(t) and fid not in out:
            out.append(fid)
    return out


def requested_port_cutouts(prompt: str) -> list[str]:
    """The PORT_CUTOUT_SIZES keys for the port cutouts a prompt requests."""
    feats = parse_requested_features(prompt)
    return [FEATURE_TO_PORT[f] for f in feats if f in FEATURE_TO_PORT]


def build_feature_contract(requested: list[str], generated: list[str],
                           approximate: list[str] | None = None,
                           unsupported: list[str] | None = None) -> dict:
    """Diff requested vs generated features. ``pass_blocking_missing_features`` lists
    REQUIRED requested features absent from the geometry — non-empty ⇒ cannot PASS."""
    gen = set(generated)
    appr = set(approximate or [])
    unsup = set(unsupported or [])
    missing = [f for f in requested if f not in gen]
    blocking = [f for f in missing if f in REQUIRED_FEATURES and f not in unsup]
    return {
        "requested_features": list(requested),
        "generated_features": list(generated),
        "missing_features": missing,
        "approximate_features": sorted(appr),
        "unsupported_features": sorted(unsup),
        "pass_blocking_missing_features": blocking,
    }
