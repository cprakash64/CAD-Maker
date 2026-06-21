"""Assembly-mode validation report (detailed tubular chassis).

Mirrors the single-part dimension report shape (so the DTO, export gating and the
frontend ValidationPanel work unchanged) but applies ASSEMBLY rules:

* multi-body is EXPECTED — never a failure.
* validate: non-empty geometry, exports present, positive volume, approximate
  envelope, tube/component counts, required ZONES + SYSTEMS, left/right symmetry.
* watertight/manifold are advisory for an assembly of intersecting members
  (surfaced, never hidden).

CRITICAL (export-blocking): no geometry, missing exports, zero/impossible
dimensions, missing main-frame rails, a missing required zone or system, or a
severe envelope mismatch.
"""
from __future__ import annotations

from app.cad.assembly.chassis import REQUIRED_SYSTEMS, REQUIRED_ZONES, ChassisBuild
from app.cad.measure import measure_solid, mesh_facts

_WITHIN_FRAC = 0.20      # within this -> "within tolerance"
_SEVERE_FRAC = 0.40      # beyond this -> critical envelope mismatch
# Count floors by detail level.
_FLOORS = {"detailed": (70, 90), "simple": (14, 20)}


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


def build_assembly_report(build: ChassisBuild, stl_bytes: bytes, step_bytes: bytes) -> dict:
    brep = measure_solid(build.solid)
    mesh = mesh_facts(stl_bytes)
    zones_present = build.zones_present
    systems_present = build.systems_present
    min_tubes, min_components = _FLOORS.get(build.spec.design_detail_level, _FLOORS["detailed"])

    measured = {
        "bbox_mm": brep["bbox_mm"],
        "volume_mm3": brep["volume_mm3"],
        "surface_area_mm2": brep["surface_area_mm2"],
        "component_count": len(build.components),
        "tube_count": build.tube_count,
        "mesh_components": mesh["components"],
        "watertight": mesh["watertight"],
        "manifold": mesh["manifold"],
        "zones_present": zones_present,
        "systems_present": systems_present,
        "sections_present": zones_present,  # back-compat
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

    missing_zones = [z for z in REQUIRED_ZONES if z not in zones_present]
    missing_systems = [s for s in REQUIRED_SYSTEMS if s not in systems_present]
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
    if not (mesh["watertight"] and mesh["manifold"]):
        warn.append("Mesh is not fully watertight/manifold (intersecting welded "
                    "members; advisory for a concept assembly).")

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
        "issues": [w for w in warn if "watertight" in w or "symmetry" in w],
    }

    return {
        "unit": "mm",
        "design_mode": "assembly",
        "tolerance": {"unit": "mm", "envelope_tolerance_frac": _WITHIN_FRAC},
        "spec": build.spec.to_meta(),
        "requested": {
            "envelope_mm": build.envelope_mm,
            "min_tube_count": min_tubes,
            "min_component_count": min_components,
            "required_zones": REQUIRED_ZONES,
            "required_systems": REQUIRED_SYSTEMS,
        },
        "measured": measured,
        "comparisons": comparisons,
        "within_tolerance": within,
        "print_readiness": print_readiness,
        "validation": {"status": status, "critical_failures": crit, "warnings": warn},
        "sections": {"present": zones_present, "missing": missing_zones},
        "zones": {"present": zones_present, "missing": missing_zones},
        "systems": {"present": systems_present, "missing": missing_systems},
        "components": [c.to_meta() for c in build.components],
        "notes": [
            "Concept assembly model — geometric first pass, NOT a certified or "
            "structurally-analyzed design (no FEA / load cases).",
            f"Tubes exported as solid cylinders; real wall thickness "
            f"Ø{build.tube_od:g}mm × {build.tube_wall:g}mm is carried as cut-list metadata.",
        ],
    }
