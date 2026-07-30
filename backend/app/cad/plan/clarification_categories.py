"""The deterministic ask-vs-default category vocabulary (the product
contract's clarification policy — see docs/product-contract.md).

The LLM planner may report WHICH kind of thing it is unsure about (an
``ambiguity_flags`` tag on the CadPlan it emits), but it never decides
whether that translates into asking the user — ``decide_clarification()``
(``app/cad/plan/policy.py``) makes that call deterministically, from this
fixed category table alone. This is what guarantees the same category of
ambiguity is handled the same way every time, regardless of how a given LLM
response happened to phrase ``clarification_required``.
"""
from __future__ import annotations

# Missing/ambiguous information in these categories changes whether the part
# even fits together correctly or is safe -- ALWAYS ask, never guess, even if
# the plan technically has enough features to compile something.
ASK_REQUIRED_CATEGORIES: frozenset[str] = frozenset({
    "topology",                # basic shape/structure of the part is unclear
    "overall_size",            # primary scale/envelope is unclear
    "fit",                     # a mating/clearance fit is unspecified
    "mating_geometry",         # how this part connects to another is unclear
    "fastener_standard",       # which screw/bolt/thread standard is intended
    "bearing_shaft_interface", # a bearing or shaft seat/fit is unspecified
    "assembly_relationship",   # how this part relates to other parts/assembly
    "safety_or_load",          # a load-bearing or safety-relevant assumption
})

# Missing information in these categories is cosmetic or has an
# industry-conventional safe default -- NEVER ask; generate and surface a
# visible, structured assumption instead.
SAFE_DEFAULT_CATEGORIES: frozenset[str] = frozenset({
    "cosmetic_fillet",
    "minor_chamfer",
    "noncritical_radius",
    "profile_derived_wall_thickness",
    "preview_only_cosmetic",
})

ALL_CATEGORIES: frozenset[str] = ASK_REQUIRED_CATEGORIES | SAFE_DEFAULT_CATEGORIES

# Canned question text per ask-required category, used when the plan didn't
# supply its own clarification_questions text.
CATEGORY_QUESTIONS: dict[str, str] = {
    "topology": "Could you describe the part's basic shape/structure in more detail?",
    "overall_size": "What are the part's overall (primary) dimensions?",
    "fit": "What fit or clearance should this feature have (e.g. slip, press, clearance)?",
    "mating_geometry": "What does this part need to connect to, and how?",
    "fastener_standard": "Which fastener/thread standard should this use (e.g. M6, #10-24)?",
    "bearing_shaft_interface": "What bearing or shaft size/fit does this need to seat?",
    "assembly_relationship": "How does this part fit into the larger assembly?",
    "safety_or_load": "What load or safety requirement applies here?",
}


def classify(flag: str) -> str | None:
    """"ask" | "default" | None (unrecognized flag -- ignored, not fatal)."""
    if flag in ASK_REQUIRED_CATEGORIES:
        return "ask"
    if flag in SAFE_DEFAULT_CATEGORIES:
        return "default"
    return None


def questions_for(flags: list[str]) -> list[str]:
    seen: list[str] = []
    for f in flags:
        q = CATEGORY_QUESTIONS.get(f)
        if q and q not in seen:
            seen.append(q)
    return seen
