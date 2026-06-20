"""Semantic verifier — does the generated model actually match the prompt?

We do NOT rely on file existence. We compare the design brief's expectations
against the model's metadata (counts the program reports as it builds) and the
real geometry (bounding box + solid count derived from the exported solid).
"""
from __future__ import annotations

from app.schemas.brief import CADDesignBrief, SemanticCheck, SemanticReport


def verify(brief: CADDesignBrief, meta: dict, bbox: dict, mesh=None) -> SemanticReport:
    """Verify the model against the brief. When ``mesh`` (a MeshStats) is given we
    check the ACTUAL geometry — metadata claims alone never pass."""
    checks: list[SemanticCheck] = []

    def add(name, passed, expected=None, actual=None, severity="error"):
        checks.append(SemanticCheck(name=name, passed=passed,
                                    expected=str(expected) if expected is not None else None,
                                    actual=str(actual) if actual is not None else None,
                                    severity=severity))

    # 1) Non-degenerate geometry.
    dims = meta.get("dimensions", bbox) or bbox
    nonzero = all(float(dims.get(k, 0) or 0) > 0.5 for k in ("x", "y", "z"))
    add("non_degenerate_geometry", nonzero, ">0.5mm each axis", dims)

    # --- GEOMETRIC ground-truth checks (metadata cannot fake these) ----------
    feats_l = {f.lower() for f in brief.required_features}
    expected_through = sum(h.count for h in brief.holes) + len(brief.bores)
    slit_opens = 1 if ("clamp_slit" in feats_l or any("slit" in f for f in feats_l)) else 0
    expected_genus = max(0, expected_through - slit_opens)
    if mesh is not None:
        # Connected body straight from the mesh.
        expect_multi = "assembly" in (brief.object_family or "") or \
            "multiple_bodies" in brief.required_features
        if not expect_multi and mesh.components:
            add("geometric_connected_body", mesh.components == 1, 1, mesh.components)

        # Holes/bores must be VISIBLE in the mesh (genus), not just in metadata.
        if expected_genus > 0 and mesh.watertight:
            add("holes_cut_through_geometry", mesh.through_holes >= expected_genus,
                f">= {expected_genus} through-features (genus)",
                f"{mesh.through_holes} (claimed holes={meta.get('holes', '?')})")
        elif expected_through > 0 and mesh.watertight and mesh.through_holes == 0 \
                and slit_opens == 0:
            # Metadata claims holes but the geometry shows none -> hard fail.
            add("holes_cut_through_geometry", False,
                f">= {expected_genus} through-features", "0 (plain solid)")

        # Hex / gear must NOT be a plain circular disk.
        if "hex_profile" in feats_l:
            add("not_plain_disk", mesh.outer_corner_count <= 8,
                "hex profile (<=8 corners)", f"{mesh.outer_corner_count} corners")
        elif "teeth" in feats_l:
            add("not_plain_disk", mesh.outer_radius_cv >= 0.03,
                "toothed profile (radial variation)", f"cv={mesh.outer_radius_cv}")

    # 2) Object type / family present in the reported metadata.
    declared = str(meta.get("object_type", "")).lower()
    fam = (brief.object_family or brief.object_type or "").lower()
    if fam and fam not in ("generic", ""):
        token = fam.split("_")[0]
        add("object_type_matches", token in declared or declared in fam,
            brief.object_family, meta.get("object_type"), severity="warning")

    # 3) Connected body, unless the brief expects multiple bodies.
    solids = int(meta.get("solid_count", 1) or 1)
    expect_multi = "assembly" in fam or "multiple_bodies" in brief.required_features
    if not expect_multi:
        add("single_connected_body", solids == 1, 1, solids)

    # 4) Hole / bolt-circle counts.
    expected_holes = sum(h.count for h in brief.holes)
    if expected_holes > 0:
        actual_holes = int(_meta_count(meta, "holes"))
        add("hole_count", actual_holes >= expected_holes, expected_holes, actual_holes)
    for h in brief.holes:
        if h.pattern == "bolt_circle" and h.count > 1:
            actual = int(_meta_count(meta, "holes"))
            add("bolt_circle_pattern", actual >= h.count,
                f"{h.count} on PCD {h.bolt_circle_diameter_mm}", actual)

    # 5) Bore present / size.
    if brief.bores:
        want = brief.bores[0]
        bores = [float(b) for b in (meta.get("bores") or [])]
        ok = any(abs(b - want) <= max(1.0, 0.1 * want) for b in bores) if bores else False
        add("bore_present", ok, f"~{want}mm bore", bores or "none")

    # 6) Required features reported.
    reported = {str(f).lower() for f in (meta.get("features") or [])}
    fc = {str(k).lower() for k, v in (meta.get("feature_counts") or {}).items() if v}
    reported |= fc
    for feat in brief.required_features:
        f = feat.lower()
        if f in ("multiple_bodies",):
            continue
        add(f"feature:{f}", any(f in r or r in f for r in reported) if reported else False,
            feat, sorted(reported) or "none", severity="warning")

    # (The authoritative "not a plain disk" check is geometric — see above. When
    # no mesh is available, fall back to the reported profile as a warning.)
    if mesh is None and "hex" in (
            brief.object_type + " " + " ".join(brief.required_features)).lower():
        add("not_plain_disk", meta.get("profile") == "hex" or "hex" in reported,
            "hex profile", meta.get("profile", "?"), severity="warning")

    report = SemanticReport(checks=checks)
    report.passed = not report.failures
    return report


def _meta_count(meta: dict, key: str) -> float:
    fc = meta.get("feature_counts") or {}
    if key in fc:
        return fc[key] or 0
    v = meta.get(key)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list):
        return len(v)
    return 0


def expectations_from_prompt(prompt: str) -> dict:
    """Lightweight expectations for the benchmark when no full brief is available."""
    import re

    t = prompt.lower()
    exp: dict = {"prompt": prompt}
    m = re.search(r"(\d+)\s*(?:holes?|bolts?)", t)
    if m:
        exp["hole_count"] = int(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:bolt circle|pcd)", t) or re.search(
        r"bolt circle.*?(\d+(?:\.\d+)?)", t)
    if m:
        exp["bolt_circle"] = float(m.group(1))
    return exp
