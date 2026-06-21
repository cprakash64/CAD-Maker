"""Assembly-mode validation report (detailed / reference buggy tubular chassis).

Mirrors the single-part dimension report shape (so the DTO, export gating and the
frontend ValidationPanel work unchanged) but applies ASSEMBLY rules:

* multi-body is EXPECTED — never a failure.
* validate: non-empty geometry, exports present, positive volume, approximate
  envelope, tube/component counts, required ZONES + SYSTEMS, left/right symmetry,
  and (reference-grade) plate/tab/hole/slot/roof/section counts.
* watertight/manifold are advisory for an assembly of intersecting welded members
  (surfaced, never hidden).

CRITICAL (export-blocking): no geometry, missing exports, zero/impossible
dimensions, missing main-frame rails, a missing required zone or system, or a
severe envelope mismatch.
"""
from __future__ import annotations

import collections

from app.cad.assembly.chassis import ChassisBuild, required_systems, required_zones
from app.cad.measure import measure_solid, mesh_facts

_WITHIN_FRAC = 0.20      # within this -> "within tolerance"
_SEVERE_FRAC = 0.40      # beyond this -> critical envelope mismatch
_FLOORS = {"reference": (140, 190), "detailed": (70, 90), "simple": (14, 20)}

# Reference-grade minimums.
_REF_MIN = {
    "plate_count": 25, "hole_feature_count": 40, "slot_feature_count": 4,
    "suspension_tab_count": 16, "side_plate_count": 2, "floor_plate_count": 2,
    "gusset_count": 12, "roof_member_count": 8,
}
_ROOF_TYPES = {"roof_rail", "roof_crossbar", "roof_diagonal"}
_SIDE_PLATE_TYPES = {"side_skid_plate", "bumper_side_plate"}
_FLOOR_PLATE_TYPES = {"floor_pan", "seat_mount", "floor_panel"}


def _envelope_compare(env: dict, measured: dict) -> tuple[list[dict], list[str], list[str]]:
    comparisons, warnings, criticals = [], [], []
    for axis in ("x", "y", "z"):
        want = float(env.get(axis, 0.0))
        got = float(measured.get(axis, 0.0))
        frac = abs(got - want) / want if want > 0 else 1.0
        comparisons.append({
            "name": f"envelope_{axis}", "requested_mm": round(want, 1),
            "measured_mm": round(got, 1), "tolerance_mm": round(want * _WITHIN_FRAC, 1),
            "delta_mm": round(got - want, 1), "within": frac <= _WITHIN_FRAC,
        })
        if got <= 0 or frac > _SEVERE_FRAC:
            criticals.append(
                f"Severe envelope mismatch on {axis}: requested {want:g}mm, got {got:g}mm."
            )
        elif frac > _WITHIN_FRAC:
            warnings.append(
                f"Envelope {axis} is {got:g}mm vs requested {want:g}mm (approximate)."
            )
    return comparisons, warnings, criticals


def _symmetry_ok(build: ChassisBuild) -> bool:
    ids = {c.id for c in build.components}
    lefts = [i for i in ids if i.endswith("_left")]
    return bool(lefts) and all(i[:-5] + "_right" in ids for i in lefts)


def _plates_within_envelope(build: ChassisBuild, bbox: dict) -> bool:
    mx = bbox["x"] / 2 * 1.15 + 60
    my = bbox["y"] / 2 * 1.15 + 60
    mz = bbox["z"] * 1.15 + 60
    for c in build.components:
        if c.kind != "plate":
            continue
        px, py, pz = c.center
        if abs(px) > mx or abs(py) > my or pz > mz or pz < -60:
            return False
    return True


def build_assembly_report(build: ChassisBuild, stl_bytes: bytes, step_bytes: bytes) -> dict:
    brep = measure_solid(build.solid)
    mesh = mesh_facts(stl_bytes)
    spec = build.spec
    level = spec.design_detail_level
    is_reference = level == "reference"
    zones_present = build.zones_present
    systems_present = build.systems_present
    req_zones = required_zones(level)
    req_systems = required_systems(level)
    min_tubes, min_components = _FLOORS.get(level, _FLOORS["detailed"])

    types = collections.Counter(c.type for c in build.components)
    plate_count = sum(1 for c in build.components if c.kind == "plate")
    hole_count = sum(c.bolt_holes for c in build.components if c.kind == "plate")
    slot_count = sum(c.slots for c in build.components if c.kind == "plate")
    susp_tab_count = types.get("suspension_tab", 0)
    gusset_count = types.get("gusset", 0)
    side_plate_count = sum(types.get(t, 0) for t in _SIDE_PLATE_TYPES)
    floor_plate_count = sum(types.get(t, 0) for t in _FLOOR_PLATE_TYPES)
    roof_member_count = sum(types.get(t, 0) for t in _ROOF_TYPES)
    has_nose = types.get("nose_perimeter", 0) > 0
    has_rear_hoop = types.get("rear_hoop", 0) > 0
    side_impact_present = types.get("side_impact_bar", 0) > 0
    has_front_nose_zone = "front_nose" in zones_present or "front" in zones_present
    has_rear_frame_zone = "rear_frame" in zones_present or "rear" in zones_present
    has_steering = "steering_column_mount" in systems_present
    has_floor_panels = "floor_panels" in systems_present

    measured = {
        "bbox_mm": brep["bbox_mm"],
        "volume_mm3": brep["volume_mm3"],
        "surface_area_mm2": brep["surface_area_mm2"],
        "component_count": len(build.components),
        "tube_count": build.tube_count,
        "plate_count": plate_count,
        "hole_feature_count": hole_count,
        "slot_feature_count": slot_count,
        "suspension_tab_count": susp_tab_count,
        "gusset_count": gusset_count,
        "side_plate_count": side_plate_count,
        "floor_plate_count": floor_plate_count,
        "roof_member_count": roof_member_count,
        "mesh_components": mesh["components"],
        "watertight": mesh["watertight"],
        "manifold": mesh["manifold"],
        "zones_present": zones_present,
        "systems_present": systems_present,
        "sections_present": zones_present,  # back-compat
        "has_front_nose_section": has_nose,
        "has_rear_hoop_section": has_rear_hoop,
        "side_impact_present": side_impact_present,
        "front_nose_present": has_front_nose_zone,
        "rear_frame_present": has_rear_frame_zone,
        "steering_column_mount_present": has_steering,
        "floor_panels_present": has_floor_panels,
        "chassis_style": spec.chassis_style,
    }

    comparisons, env_warn, env_crit = _envelope_compare(build.envelope_mm, brep["bbox_mm"])
    comparisons.append({
        "name": "tube_count", "requested_mm": min_tubes, "measured_mm": build.tube_count,
        "tolerance_mm": 0, "delta_mm": build.tube_count - min_tubes,
        "within": build.tube_count >= min_tubes,
    })
    comparisons.append({
        "name": "component_count", "requested_mm": min_components,
        "measured_mm": len(build.components), "tolerance_mm": 0,
        "delta_mm": len(build.components) - min_components,
        "within": len(build.components) >= min_components,
    })

    missing_zones = [z for z in req_zones if z not in zones_present]
    missing_systems = [s for s in req_systems if s not in systems_present]
    has_main_rails = any(c.type == "lower_rail" for c in build.components)

    crit: list[str] = list(env_crit)
    warn: list[str] = list(env_warn)

    if brep["volume_mm3"] <= 0 or mesh["triangles"] <= 0:
        crit.append("No geometry was generated.")
    if not stl_bytes or not step_bytes:
        crit.append("Missing STEP/STL export.")
    if not has_main_rails:
        crit.append("Missing main-frame lower rails.")
    if missing_zones:
        crit.append("Missing required zones: " + ", ".join(missing_zones) + ".")
    if missing_systems:
        crit.append("Missing required systems: " + ", ".join(missing_systems) + ".")

    if build.tube_count < min_tubes:
        warn.append(f"Only {build.tube_count} tubes (expected ≥ {min_tubes}).")
    if len(build.components) < min_components:
        warn.append(f"Only {len(build.components)} components (expected ≥ {min_components}).")
    if not _symmetry_ok(build):
        warn.append("Left/right symmetry could not be confirmed.")
    if not _plates_within_envelope(build, brep["bbox_mm"]):
        warn.append("One or more mount plates sit outside the chassis envelope.")
    if not (mesh["watertight"] and mesh["manifold"]):
        warn.append("Mesh is not fully watertight/manifold (intersecting welded "
                    "members; advisory for a concept assembly).")

    if is_reference:
        ref_counts = {
            "plate_count": plate_count, "hole_feature_count": hole_count,
            "slot_feature_count": slot_count, "suspension_tab_count": susp_tab_count,
            "side_plate_count": side_plate_count, "floor_plate_count": floor_plate_count,
            "gusset_count": gusset_count, "roof_member_count": roof_member_count,
        }
        for key, minimum in _REF_MIN.items():
            if ref_counts[key] < minimum:
                warn.append(f"{key.replace('_', ' ')} is {ref_counts[key]} (expected ≥ {minimum}).")
        if not has_nose:
            warn.append("No tapered front nose section.")
        if not has_rear_hoop:
            warn.append("No rear hoop section.")
        if not side_impact_present:
            warn.append("No side-impact members.")

    status = "critical_failure" if crit else ("warning" if warn else "pass")
    within = all(c["within"] for c in comparisons) if comparisons else None

    print_readiness = {
        "printable": brep["volume_mm3"] > 0 and build.tube_count > 0 and not crit,
        "watertight": mesh["watertight"],
        "manifold": mesh["manifold"],
        "positive_volume": brep["volume_mm3"] > 0,
        "multi_body_expected": True,
        "min_wall_checked": False,
        "min_hole_diameter_mm": None,
        "issues": [w for w in warn if "watertight" in w or "symmetry" in w
                   or "outside the chassis" in w],
    }

    tube_groups = collections.Counter(
        c.group for c in build.components if c.kind == "tube" and c.group)
    plate_groups = collections.Counter(c.type for c in build.components if c.kind == "plate")
    snapshot = {
        "chassis_style": spec.chassis_style,
        "detail_level": level,
        "envelope_mm": build.envelope_mm,
        "tube_outer_diameter_mm": spec.tube_outer_diameter_mm,
        "tube_wall_thickness_mm": spec.tube_wall_thickness_mm,
        "tube_count": build.tube_count,
        "plate_count": plate_count,
        "component_count": len(build.components),
        "hole_feature_count": hole_count,
        "slot_feature_count": slot_count,
        "zones": zones_present,
        "systems": systems_present,
        "tube_groups": dict(tube_groups),
        "plate_groups": dict(plate_groups),
        "symmetry_pairs": sum(1 for c in build.components if c.id.endswith("_left")),
    }

    return {
        "unit": "mm",
        "design_mode": "assembly",
        "tolerance": {"unit": "mm", "envelope_tolerance_frac": _WITHIN_FRAC},
        "spec": spec.to_meta(),
        "recommended_material": spec.to_meta()["recommended_material"],
        "requested": {
            "envelope_mm": build.envelope_mm,
            "min_tube_count": min_tubes,
            "min_component_count": min_components,
            "required_zones": req_zones,
            "required_systems": req_systems,
        },
        "measured": measured,
        "comparisons": comparisons,
        "within_tolerance": within,
        "print_readiness": print_readiness,
        "validation": {"status": status, "critical_failures": crit, "warnings": warn},
        "sections": {"present": zones_present, "missing": missing_zones},
        "zones": {"present": zones_present, "missing": missing_zones},
        "systems": {"present": systems_present, "missing": missing_systems},
        "snapshot": snapshot,
        "components": [c.to_meta() for c in build.components],
        "notes": [
            "Detailed concept CAD only. Not FEA analyzed. Not structurally "
            "certified. Requires engineering review before fabrication.",
            f"Tubes exported as solid cylinders; real wall thickness "
            f"Ø{build.tube_od:g}mm × {build.tube_wall:g}mm is carried as cut-list metadata.",
            f"Recommended appearance: {spec.material_name}.",
        ],
    }
