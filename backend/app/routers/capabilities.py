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


# Categories we deliberately do NOT generate as one free-form part — surfaced so
# the UI can set honest expectations instead of letting them fail silently.
_UNSUPPORTED_CATEGORIES = [
    {
        "category": "Whole machines / vehicles",
        "examples": ["a complete car", "an aircraft", "a full drone with electronics"],
        "handling": "Decomposed into buildable single parts (needs_decomposition).",
    },
    {
        "category": "Multi-system assemblies",
        "examples": ["a gearbox with internals", "an engine", "a robot with joints"],
        "handling": "Asked to pick one component, or decomposed.",
    },
    {
        "category": "Free-form / organic surfaces",
        "examples": ["a sculpted ergonomic grip", "an aerodynamic body shell"],
        "handling": "Approximated from primitives or a clarification is requested; "
                    "no broken free-form geometry is shipped.",
    },
    {
        "category": "Standards-driven / certified parts",
        "examples": ["an ASME flange to spec", "an involute power-transmission gear"],
        "handling": "Built as geometry only and clearly labelled concept — not "
                    "standards-certified.",
    },
    {
        "category": "Non-spur gears (helical / bevel / worm / internal)",
        "examples": ["a helical gear", "a bevel gear", "a worm gear"],
        "handling": "Only an approximate SPUR gear is modelled; other gear types "
                    "are built as a spur blank and flagged (warning), never passed "
                    "off as the requested type.",
    },
]

# Vague category prompts that trigger a clarification with ready-to-run options.
_NEEDS_CLARIFICATION_EXAMPLES = [
    {"prompt": "Make a bracket", "why": "No bracket type or dimensions given."},
    {"prompt": "Make a mount", "why": "Ambiguous — many mount types."},
    {"prompt": "Make a holder", "why": "No object, size, or hole count given."},
]


@router.get("/capabilities")
def list_capabilities() -> dict:
    """Return the full CAD family catalog with maturity, examples and limits, plus
    grouped views (production-ready / concept-ready), clarification examples,
    unsupported categories, and the internal defaults the engine fills in."""
    from app.cad.standards import defaults as std_defaults
    from app.services.design_service import VAGUE_SUGGESTIONS

    families = [_family_payload(f) for f in all_families()]
    by_maturity: dict[str, int] = {}
    for f in families:
        by_maturity[f["maturity"]] = by_maturity.get(f["maturity"], 0) + 1

    production_ready = [f for f in families if f["maturity"] == "production_ready"]
    concept_ready = [f for f in families
                     if f["maturity"] in ("concept", "beta") and f["exportable"]]
    known_limitations = sorted({lim for f in families for lim in f["known_limitations"]})

    return {
        "families": families,
        "maturity_levels": _MATURITY_MEANING,
        "counts": {
            "total": len(families),
            "by_maturity": by_maturity,
        },
        # Grouped views for the frontend.
        "production_ready_families": production_ready,
        "concept_ready_families": concept_ready,
        "needs_clarification_examples": _NEEDS_CLARIFICATION_EXAMPLES,
        "clarification_suggestions": VAGUE_SUGGESTIONS,
        "unsupported_categories": _UNSUPPORTED_CATEGORIES,
        "known_limitations": known_limitations,
        # Internal (NOT standards-certified) defaults the engine fills in.
        "internal_defaults": std_defaults.as_dict(),
        "notes": [
            "This catalog is generated from the backend family registry, so it "
            "reflects exactly what the engine routes to — no fake capabilities.",
            "'validated' means: STEP+STL exported, non-empty, bounding box within "
            "tolerance of the request, and expected hole counts matched.",
            "'concept' parts/assemblies are geometry only — not FEA- or "
            "standards-certified.",
            "Default dimensions are SourceCAD internal defaults, not ASME/ISO/"
            "Machinery's Handbook certified.",
        ],
    }
