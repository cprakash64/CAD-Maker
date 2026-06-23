"""Semantic feature audits — guard against geometry that is dimensionally fine
but the WRONG SHAPE for what was asked.

Bbox / watertight / manifold / hole-count checks can all pass while a "gear"
renders as a smooth disc. These audits inspect the GENERATED geometry (measured
mesh facts, not just declared metadata) and report a mismatch so the validation
layer can refuse a misleading PASS.

Each auditor returns a list of :class:`AuditIssue`. Severity:
  * "critical" -> production-blocking; downgrades the build to critical_failure
    (export blocked, contract outcome failed_safe).
  * "warning"  -> advisory; downgrades a PASS to warning (REVIEW), never blocks.

The auditors are pure functions over already-measured facts, so they are cheap,
deterministic, and unit-testable without a CAD kernel.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass


@dataclass
class AuditIssue:
    check: str
    severity: str  # "critical" | "warning"
    message: str


def _stl_xy(stl_bytes: bytes) -> list[tuple[float, float]]:
    """Extract (x, y) of every triangle vertex from a binary STL (best effort)."""
    if not stl_bytes or len(stl_bytes) < 84:
        return []
    n = struct.unpack("<I", stl_bytes[80:84])[0]
    pts: list[tuple[float, float]] = []
    off = 84
    for _ in range(n):
        if off + 50 > len(stl_bytes):
            break
        for v in range(3):
            base = off + 12 + v * 12
            x, y, _z = struct.unpack("<fff", stl_bytes[base:base + 12])
            pts.append((x, y))
        off += 50
    return pts


def measure_radial_teeth(stl_bytes: bytes, bins: int = 360) -> dict:
    """Measure the rim's radial silhouette about the Z axis to detect teeth.

    For each angular bin we keep the MAX vertex radius (the outer boundary at that
    angle); on a gear the tooth bins sit at the tip radius and the gap bins at the
    (smaller) root radius, so ``depth_ratio = (tip - root) / tip`` is clearly
    positive, while a smooth disc/cylinder gives ~0. ``peaks`` counts the local
    maxima around the circle (≈ tooth count). Robust to shallow teeth (unlike a
    fixed near-tip radius cutoff).
    """
    pts = _stl_xy(stl_bytes)
    if len(pts) < 16:
        return {"depth_ratio": None, "peaks": None}
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    radii = [math.hypot(x - cx, y - cy) for x, y in pts]
    rmax = max(radii) if radii else 0.0
    if rmax <= 0:
        return {"depth_ratio": None, "peaks": None}

    # RIM vertices only: keep the outer band and drop the much smaller center-bore
    # cluster (so an empty angular sector can't be "filled" by a bore vertex and
    # fake a tooth). The tip sits at rmax; tooth roots sit just below it.
    rim = [(x, y, r) for (x, y), r in zip(pts, radii) if r >= 0.5 * rmax]
    rim_radii = [r for _, _, r in rim]
    if len(rim_radii) < 8:
        return {"depth_ratio": None, "peaks": None}
    rmin = min(rim_radii)
    depth_ratio = (rmax - rmin) / rmax

    # Per-angle outer silhouette over the rim, to count tooth peaks (~tooth count).
    bin_max = [0.0] * bins
    for x, y, r in rim:
        b = int((math.atan2(y - cy, x - cx) + math.pi) / (2 * math.pi) * bins) % bins
        if r > bin_max[b]:
            bin_max[b] = r
    mid = (rmax + rmin) / 2.0
    peaks = 0
    for i in range(bins):
        r = bin_max[i]
        if r <= mid or r <= 0:
            continue
        prev = bin_max[(i - 1) % bins]
        nxt = bin_max[(i + 1) % bins]
        if r >= prev and r >= nxt:
            peaks += 1
    return {"depth_ratio": round(depth_ratio, 4), "peaks": peaks}


# --- gear ------------------------------------------------------------------

# Object types that represent a (spur) gear request.
GEAR_OBJECT_TYPES = {"simple_gear_or_pulley", "spur_gear", "gear", "sprocket"}
# Minimum tip-to-root radial depth ratio for the rim to count as "toothed". A
# smooth disc/cylinder gives ~0 (tessellation noise <0.005); a module-based spur
# gear gives 4.5/(z+2) — ≈0.17 at 24 teeth, still ≈0.022 at 200 teeth. The 0.015
# floor sits well above disc noise yet below the finest real gear, so a disc can
# never pass and a real gear never false-fails.
MIN_TOOTH_DEPTH_RATIO = 0.015
MIN_REASONABLE_TEETH = 8


def is_gear_request(object_type: str | None, tooth_count) -> bool:
    """A gear is intended when the type is a gear type AND a positive tooth count
    is present (a tooth_count of 0/None on simple_gear_or_pulley is a pulley/hex
    blank, not a gear, so it is not audited as a gear)."""
    ot = (object_type or "").lower()
    if ot not in GEAR_OBJECT_TYPES:
        return False
    try:
        return tooth_count is not None and int(tooth_count) > 0
    except (TypeError, ValueError):
        return False


# Gear types we do NOT model to type — built as a spur blank and flagged so the
# build can never report a misleading PASS for the requested type.
UNSUPPORTED_GEAR_TYPES = ("helical", "bevel", "worm", "herringbone", "internal", "ring")


def detect_gear_subtype(text: str | None) -> str | None:
    """Return an unsupported gear subtype word present in the text, else None."""
    t = (text or "").lower()
    return next((g for g in UNSUPPORTED_GEAR_TYPES if g in t), None)


def audit_gear(*, object_type: str | None, tooth_count, tooth_depth_ratio: float | None,
               measured_peaks: int | None = None,
               requested_gear_type: str | None = None,
               gear_intent: bool = False) -> list[AuditIssue]:
    """Audit a gear's GENERATED rim against its gear claim.

    ``tooth_depth_ratio`` is the measured tip-to-root radial depth ratio of the
    rim (see :func:`measure_radial_teeth`): ~0 for a smooth disc, clearly positive
    for real teeth. A gear whose rim is effectively circular is a CRITICAL
    mismatch — the exact "smooth cylinder reported as a gear" failure this guards
    against. ``measured_peaks`` (≈ tooth count) is a secondary signal.

    ``gear_intent`` forces the audit even when tooth_count metadata is absent (a
    gear prompt whose generator forgot the tooth count must still be checked — and
    flagged for the missing count — rather than silently passing as a disc).
    """
    has_count = is_gear_request(object_type, tooth_count)
    if not has_count and not gear_intent:
        return []

    issues: list[AuditIssue] = []
    z = int(tooth_count) if has_count else 0

    if not has_count:
        issues.append(AuditIssue(
            "gear_tooth_count_missing", "warning",
            "Gear requested but no tooth_count is present in the metadata."))

    # The tip-to-root radial depth is the reliable gate. (Peak counting is a noisy
    # secondary signal — a finely tessellated disc shows many phantom local maxima
    # — so it is metadata only and never rescues a no-depth rim.)
    if tooth_depth_ratio is None:
        issues.append(AuditIssue(
            "gear_teeth_unverified", "warning",
            "Could not measure the gear rim to confirm teeth are present."))
    elif tooth_depth_ratio < MIN_TOOTH_DEPTH_RATIO:
        issues.append(AuditIssue(
            "gear_has_no_teeth", "critical",
            "Gear rim is effectively circular (no radial tooth variation) — a "
            "smooth disc/cylinder, not a gear. Visible teeth are required."))

    if has_count and z < MIN_REASONABLE_TEETH:
        issues.append(AuditIssue(
            "gear_too_few_teeth", "warning",
            f"Only {z} teeth requested; a usable spur gear normally has "
            f"{MIN_REASONABLE_TEETH}+ teeth."))

    if requested_gear_type:
        issues.append(AuditIssue(
            "gear_type_approximated", "warning",
            f"A {requested_gear_type} gear was requested but only a SPUR blank is "
            f"supported — modelled as a spur approximation, not a true "
            f"{requested_gear_type} gear."))

    return issues


def audit_gear_metadata(object_type: str | None, tooth_count) -> list[AuditIssue]:
    """Metadata-only gate: a gear type with NO tooth count in metadata can never
    be trusted as a gear. Used where mesh facts aren't available."""
    ot = (object_type or "").lower()
    if ot not in GEAR_OBJECT_TYPES:
        return []
    try:
        valid = tooth_count is not None and int(tooth_count) > 0
    except (TypeError, ValueError):
        valid = False
    if not valid and ot in {"spur_gear", "gear", "sprocket"}:
        return [AuditIssue("gear_tooth_count_missing", "warning",
                           "Gear is missing a tooth_count in its metadata.")]
    return []


# --- minimal audits for other families (extend incrementally) --------------
# These are intentionally light: a TODO-grade safety net so a clearly-wrong shape
# can't silently PASS. They operate on already-extracted feature ids / metadata.

def audit_hex_standoff(*, object_type: str | None, outer_corner_count: int | None,
                       is_hex: bool) -> list[AuditIssue]:
    """A hex standoff / hex gear blank must actually have ~6 outer flats."""
    if not is_hex:
        return []
    if outer_corner_count is not None and outer_corner_count > 8:
        return [AuditIssue("hex_not_faceted", "warning",
                           f"Hex profile requested but the outer boundary has "
                           f"{outer_corner_count} corners (expected ~6 flats).")]
    return []


def audit_screwdriver(feature_ids: list[str]) -> list[AuditIssue]:
    """A screwdriver must have a handle, a shaft, and a tip."""
    ids = " ".join(feature_ids).lower()
    missing = [part for part in ("handle", "shaft", "tip") if part not in ids]
    if missing:
        return [AuditIssue("screwdriver_incomplete", "warning",
                           f"Screwdriver is missing: {', '.join(missing)}.")]
    return []


def audit_wrench(feature_ids: list[str], hole_count: int | None) -> list[AuditIssue]:
    """A wrench must have a head opening / ring."""
    ids = " ".join(feature_ids).lower()
    has_opening = "jaw" in ids or "head" in ids or "ring" in ids or bool(hole_count)
    if not has_opening:
        return [AuditIssue("wrench_no_opening", "warning",
                           "Wrench has no head opening / ring jaw.")]
    return []


def audit_bracket(object_type: str | None, requested_type: str | None) -> list[AuditIssue]:
    """The built bracket should match the requested bracket type (l/u/hinge/...).

    Matches on type TOKENS, not bare substrings, so a single-letter type like "l"
    doesn't spuriously match the 'l' in 'flat_plate'."""
    if not requested_type:
        return []
    req = requested_type.lower().strip()
    ot = (object_type or "").lower()
    tokens = set(ot.replace("-", "_").split("_"))
    matched = (req in tokens or ot.startswith(f"{req}_") or ot.startswith(f"{req}-")
               or f"_{req}_" in ot or ot == req)
    if not matched:
        return [AuditIssue("bracket_type_mismatch", "warning",
                           f"Requested a {requested_type} bracket but built "
                           f"{object_type or 'unknown'}.")]
    return []
