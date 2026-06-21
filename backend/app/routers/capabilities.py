"""Capability endpoint — an honest, machine-readable catalog of what SourceCAD
can generate.

The frontend uses this to show realistic examples per family, label maturity
(production-ready / beta / concept), and avoid promising parts the engine can't
build. It is derived entirely from the central family registry, so the catalog
can never drift from the generators.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.cad.families import all_families

router = APIRouter(prefix="/api", tags=["capabilities"])


# Short, plain-English explanation of each maturity level (kept honest).
_MATURITY_MEANING = {
    "production_ready": "Validated, dimension-checked, and exportable as STEP + STL.",
    "beta": "Generates real CAD with fewer guarantees / narrower coverage.",
    "concept": "Plausible concept geometry — not certified or analysis-validated.",
    "unsupported": "Not generated as one part; routed to decomposition guidance.",
}


def _family_payload(fam) -> dict:
    return {
        "family_id": fam.family_id,
        "display_name": fam.display_name,
        "design_mode": fam.design_mode.value,
        "maturity": fam.maturity.value,
        "maturity_meaning": _MATURITY_MEANING.get(fam.maturity.value, ""),
        "generation_strategy": fam.generation_strategy.value,
        "required_dimensions": list(fam.required_dimensions),
        "optional_dimensions": list(fam.optional_dimensions),
        "default_assumptions": list(fam.default_assumptions),
        "validation_profile": fam.validation_profile,
        "export_policy": list(fam.export_policy),
        "exportable": bool(fam.export_policy),
        "known_limitations": list(fam.known_limitations),
        "example_prompts": list(fam.example_prompts),
        "supports_drawing_input": fam.supports_drawing_input,
    }


@router.get("/capabilities")
def list_capabilities() -> dict:
    """Return the full CAD family catalog with maturity, examples and limits."""
    families = [_family_payload(f) for f in all_families()]
    by_maturity: dict[str, int] = {}
    for f in families:
        by_maturity[f["maturity"]] = by_maturity.get(f["maturity"], 0) + 1
    return {
        "families": families,
        "maturity_levels": _MATURITY_MEANING,
        "counts": {
            "total": len(families),
            "by_maturity": by_maturity,
        },
        "notes": [
            "This catalog is generated from the backend family registry, so it "
            "reflects exactly what the engine routes to — no fake capabilities.",
            "'validated' means: STEP+STL exported, non-empty, bounding box within "
            "tolerance of the request, and expected hole counts matched.",
            "'concept' assemblies (e.g. tubular chassis) are not FEA-certified.",
        ],
    }
