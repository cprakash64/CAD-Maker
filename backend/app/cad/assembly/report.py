"""Assembly-mode validation report.

Mirrors the single-part dimension report shape (so the DTO, export gating and the
frontend ValidationPanel all work unchanged) but applies ASSEMBLY rules:

* multi-body is EXPECTED — never a failure.
* validate non-empty geometry, exports present, positive volume, approximate
  envelope, expected component/tube counts, required sections, symmetry.
* watertight/manifold are advisory (a hollow-tube end-cap tessellation artifact
  is not a structural defect in a concept model).

CRITICAL (export-blocking) for an assembly: no geometry, missing exports,
zero/impossible dimensions, missing main-frame rails, a missing required section,
or a severe envelope mismatch.
"""
from __future__ import annotations

from app.cad.assembly.chassis import REQUIRED_SECTIONS, ChassisBuild
from app.cad.measure import measure_solid, mesh_facts

# Approximate-envelope bands (a concept frame only roughly fills the envelope).
_WITHIN_FRAC = 0.20      # within this -> "within tolerance"
_SEVERE_FRAC = 0.40      # beyond this -> critical envelope mismatch
_MIN_COMPONENTS = 20     # soft floor for a credible chassis concept
_MIN_TUBES = 14


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
    return all(i[:-5] + "_right" in ids for i in lefts)


def build_assembly_report(build: ChassisBuild, stl_bytes: bytes, step_bytes: bytes) -> dict:
    brep = measure_solid(build.solid)
    mesh = mesh_facts(stl_bytes)
    sections_present = build.sections_present
    component_ids = {c.id for c in build.components}

    measured = {
        "bbox_mm": brep["bbox_mm"],
        "volume_mm3": brep["volume_mm3"],
        "surface_area_mm2": brep["surface_area_mm2"],
        "component_count": len(build.components),
        "tube_count": build.tube_count,
        "mesh_components": mesh["components"],
        "watertight": mesh["watertight"],
        "manifold": mesh["manifold"],
        "sections_present": sections_present,
    }

    comparisons, env_warn, env_crit = _envelope_compare(build.envelope_mm, brep["bbox_mm"])
    comparisons.append({
        "name": "component_count", "requested_mm": _MIN_COMPONENTS,
        "measured_mm": len(build.components), "tolerance_mm": 0,
        "delta_mm": len(build.components) - _MIN_COMPONENTS,
        "within": len(build.components) >= _MIN_COMPONENTS,
    })

    crit: list[str] = list(env_crit)
    warn: list[str] = list(env_warn)

    if brep["volume_mm3"] <= 0 or mesh["triangles"] <= 0:
        crit.append("No geometry was generated.")
    if not stl_bytes or not step_bytes:
        crit.append("Missing STEP/STL export.")
    if not ({"lower_rail_left", "lower_rail_right"} <= component_ids):
        crit.append("Missing main-frame lower rails.")
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in sections_present]
    if missing_sections:
        crit.append("Missing required sections: " + ", ".join(missing_sections) + ".")

    if build.tube_count < _MIN_TUBES:
        warn.append(f"Only {build.tube_count} tubes (expected ≥ {_MIN_TUBES}).")
    if len(build.components) < _MIN_COMPONENTS:
        warn.append(f"Only {len(build.components)} components (expected ≥ {_MIN_COMPONENTS}).")
    if not _symmetry_ok(build):
        warn.append("Left/right symmetry could not be confirmed.")
    if not (mesh["watertight"] and mesh["manifold"]):
        warn.append("Mesh is not fully watertight/manifold (hollow-tube ends; "
                    "advisory for a concept assembly).")

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
        "requested": {
            "envelope_mm": build.envelope_mm,
            "min_component_count": _MIN_COMPONENTS,
            "required_sections": REQUIRED_SECTIONS,
        },
        "measured": measured,
        "comparisons": comparisons,
        "within_tolerance": within,
        "print_readiness": print_readiness,
        "validation": {"status": status, "critical_failures": crit, "warnings": warn},
        "sections": {"present": sections_present, "missing": missing_sections},
        "components": [c.to_meta() for c in build.components],
        "notes": [
            "Concept assembly model — geometric first pass, NOT a certified or "
            "structurally-analyzed design (no FEA / load cases).",
            f"Round tubes: Ø{build.tube_od:g}mm, {build.tube_wall:g}mm wall (metadata).",
        ],
    }
