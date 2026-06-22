"""Validation for the deterministic frame / concept-assembly generators.

Produces a report in the SAME shape as the chassis assembly report (so the DTO,
export gating and the frontend ValidationPanel work unchanged), but driven by
per-family *validation profiles* + the build's declared requirements instead of
the chassis taxonomy.

Profiles:
  * structural_frame_assembly — machine frame / engine test stand
  * drone_frame               — quadcopter X-frame (checks motor diagonal)
  * motorcycle_subframe       — tube subframe
  * motor_mount_component     — a single fused component (e.g. skateboard mount)

Assembly rules (vs. single part):
  * multi-body is EXPECTED for assemblies — never a failure.
  * watertight/manifold are advisory for intersecting welded members.

CRITICAL (export-blocking) only when: no geometry / zero volume, missing
exports, a required component role is absent, or a severe envelope mismatch —
matching the brief's "no critical failure unless required systems are missing or
geometry invalid".
"""
from __future__ import annotations

import collections

from app.cad.assembly.frames import AssemblyBuild
from app.cad.measure import measure_solid, mesh_facts

_WITHIN_FRAC = 0.20
_SEVERE_FRAC = 0.45

_PROFILE_LABELS = {
    "structural_frame_assembly": "Structural frame assembly (concept)",
    "drone_frame": "Drone frame assembly (concept)",
    "motorcycle_subframe": "Motorcycle subframe (concept)",
    "motor_mount_component": "Motor mount component (concept)",
}


def _envelope_compare(env: dict, measured: dict):
    comparisons, warnings, criticals = [], [], []
    for axis in ("x", "y", "z"):
        want = float(env.get(axis, 0.0))
        if want <= 0:
            continue  # axis not constrained by the prompt (e.g. drone span)
        got = float(measured.get(axis, 0.0))
        frac = abs(got - want) / want if want > 0 else 1.0
        comparisons.append({
            "name": f"envelope_{axis}", "requested_mm": round(want, 1),
            "measured_mm": round(got, 1), "tolerance_mm": round(want * _WITHIN_FRAC, 1),
            "delta_mm": round(got - want, 1), "within": frac <= _WITHIN_FRAC,
        })
        if got <= 0 or frac > _SEVERE_FRAC:
            criticals.append(
                f"Severe envelope mismatch on {axis}: requested {want:g}mm, got {got:g}mm.")
        elif frac > _WITHIN_FRAC:
            warnings.append(
                f"Envelope {axis} is {got:g}mm vs requested {want:g}mm (approximate).")
    return comparisons, warnings, criticals


def _motor_diagonal(build: AssemblyBuild) -> float | None:
    """Max distance between any two motor-mount anchor points (drone)."""
    pts = [m.center for m in build.members if m.role == "motor_mount"]
    if len(pts) < 2:
        return None
    best = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = sum((a - b) ** 2 for a, b in zip(pts[i], pts[j])) ** 0.5
            best = max(best, d)
    return best


def build_frame_report(build: AssemblyBuild, stl_bytes: bytes, step_bytes: bytes) -> dict:
    brep = measure_solid(build.solid)
    mesh = mesh_facts(stl_bytes)
    req = build.requirements
    roles = collections.Counter(m.role for m in build.members)
    roles_present = set(roles)

    measured = {
        "bbox_mm": brep["bbox_mm"],
        "volume_mm3": brep["volume_mm3"],
        "surface_area_mm2": brep["surface_area_mm2"],
        "component_count": build.member_count,
        "beam_count": sum(1 for m in build.members if m.kind == "beam"),
        "tube_count": sum(1 for m in build.members if m.kind == "tube"),
        "plate_count": sum(1 for m in build.members if m.kind == "plate"),
        "hole_feature_count": build.total_holes(),
        "roles_present": sorted(roles_present),
        "mesh_components": mesh["components"],
        "watertight": mesh["watertight"],
        "manifold": mesh["manifold"],
    }

    crit: list[str] = []
    warn: list[str] = []

    comparisons, env_warn, env_crit = _envelope_compare(req.get("envelope", {}), brep["bbox_mm"])
    crit += env_crit
    warn += env_warn

    # Geometry + export integrity (critical).
    if brep["volume_mm3"] <= 0 or mesh["triangles"] <= 0:
        crit.append("No geometry was generated.")
    if not stl_bytes or not step_bytes:
        crit.append("Missing STEP/STL export.")

    # Required component roles (critical — "required systems missing").
    required_roles = req.get("required_roles", [])
    missing_roles = [r for r in required_roles if r not in roles_present]
    if missing_roles:
        crit.append("Missing required components: "
                    + ", ".join(r.replace("_", " ") for r in missing_roles) + ".")

    # Component-count floor + hole floor (advisory).
    min_components = req.get("min_components", 0)
    comparisons.append({
        "name": "component_count", "requested_mm": min_components,
        "measured_mm": build.member_count, "tolerance_mm": 0,
        "delta_mm": build.member_count - min_components,
        "within": build.member_count >= min_components,
    })
    if build.member_count < min_components:
        warn.append(f"Only {build.member_count} components (expected ≥ {min_components}).")

    min_holes = req.get("min_holes", 0)
    if min_holes and build.total_holes() < min_holes:
        warn.append(f"Only {build.total_holes()} mounting holes (expected ≥ {min_holes}).")

    # Per-profile structural minimums (advisory) + drone motor diagonal.
    for key, role in (("min_legs", "leg"), ("min_foot_plates", "foot_plate"),
                      ("min_caster_plates", "caster_plate"), ("min_arms", "arm"),
                      ("min_motor_mounts", "motor_mount")):
        want = req.get(key)
        if want and roles.get(role, 0) < want:
            warn.append(f"Only {roles.get(role, 0)} {role.replace('_', ' ')}(s) "
                        f"(expected ≥ {want}).")

    diag_target = req.get("motor_to_motor_diagonal_mm")
    if diag_target:
        got = _motor_diagonal(build)
        tol = req.get("diagonal_tolerance_frac", 0.2)
        if got is not None:
            within = abs(got - diag_target) <= diag_target * tol
            comparisons.append({
                "name": "motor_to_motor_diagonal", "requested_mm": round(diag_target, 1),
                "measured_mm": round(got, 1), "tolerance_mm": round(diag_target * tol, 1),
                "delta_mm": round(got - diag_target, 1), "within": within,
            })
            if not within:
                warn.append(f"Motor-to-motor diagonal {got:g}mm vs requested "
                            f"{diag_target:g}mm (approximate).")

    if not (mesh["watertight"] and mesh["manifold"]) and not build.fused:
        warn.append("Mesh is not fully watertight/manifold (intersecting members; "
                    "advisory for a concept assembly).")

    status = "critical_failure" if crit else ("warning" if warn else "pass")
    within_tol = all(c["within"] for c in comparisons) if comparisons else None

    print_readiness = {
        "printable": brep["volume_mm3"] > 0 and not crit,
        "watertight": mesh["watertight"],
        "manifold": mesh["manifold"],
        "positive_volume": brep["volume_mm3"] > 0,
        "multi_body_expected": build.design_mode == "assembly",
        "min_wall_checked": False,
        "min_hole_diameter_mm": None,
        "issues": [w for w in warn if "watertight" in w or "outside" in w],
    }

    notes = list(build.notes)
    if build.decomposition_note:
        notes.append(build.decomposition_note)

    return {
        "unit": "mm",
        "design_mode": build.design_mode,
        "validation_profile": build.profile,
        "profile_label": _PROFILE_LABELS.get(build.profile, build.profile),
        "tolerance": {"unit": "mm", "envelope_tolerance_frac": _WITHIN_FRAC},
        "spec": {"family_id": build.family_id, "display_name": build.display_name, **build.meta},
        "requested": {
            "envelope_mm": build.envelope_mm,
            "min_component_count": min_components,
            "required_roles": required_roles,
        },
        "measured": measured,
        "comparisons": comparisons,
        "within_tolerance": within_tol,
        "print_readiness": print_readiness,
        "validation": {"status": status, "critical_failures": crit, "warnings": warn},
        "roles": {"present": sorted(roles_present), "missing": missing_roles},
        "components": [m.to_meta() for m in build.members],
        "notes": notes,
    }
