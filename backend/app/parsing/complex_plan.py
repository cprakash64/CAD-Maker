"""Complex-CAD planning for long / complex prompts.

Deterministic, offline-safe classification + metadata extraction (works without
an LLM). Routes a prompt to one of: simple_template, advanced_template,
feature_graph, or unsupported — emitting strict JSON only. Separates engineering
requirements from visual/material notes so styling never breaks CAD generation.
"""
from __future__ import annotations

import re

from app.llm.base import LLMProvider
from app.llm.factory import get_provider
from app.parsing.prompt_parser import parse_prompt
from app.schemas.complex_cad import (
    CADIntentClassification,
    CADIntentKind,
    ComplexCADPlan,
)

# Templates considered "advanced" (engineered, many parameters).
_ADVANCED = {"inline_4_crankshaft", "flanged_pipe_branch", "simple_gear_or_pulley"}
_SIMPLE = {
    "rectangular_bracket", "l_bracket", "enclosure", "spacer", "pipe_clamp",
    "drill_jig", "handle", "adapter_plate",
}

_MATERIALS = [
    "steel", "stainless", "aluminum", "aluminium", "brass", "bronze", "titanium",
    "cast iron", "iron", "pla", "abs", "petg", "nylon", "resin", "copper", "carbon fiber",
]
_VISUAL = [
    "realistic", "photorealistic", "render", "rendering", "texture", "textured",
    "polished", "shiny", "matte", "color", "colour", "painted", "anodized",
    "brushed", "glossy", "studio lighting", "pbr", "material look",
]


def extract_metadata(prompt: str) -> dict:
    """Pull out materials, visual-style notes, and coarse counts/dims. Visual
    notes are metadata only and must never affect geometry."""
    text = prompt.lower()
    materials = sorted({m for m in _MATERIALS if re.search(r"\b" + re.escape(m) + r"\b", text)})
    visual = sorted({v for v in _VISUAL if v in text})
    counts = {}
    for label in ("cylinder", "cylinders", "hole", "holes", "bolt", "bolts", "tooth", "teeth"):
        m = re.search(r"(\d+)\s+" + label, text)
        if m:
            counts[label] = int(m.group(1))
    return {"materials": materials, "visual_notes": visual, "counts": counts,
            "word_count": len(prompt.split())}


# Strong indicators that a prompt is for a specific advanced template even if it
# doesn't use the exact template name.
_ADVANCED_INDICATORS = {
    "inline_4_crankshaft": (
        "crankshaft", "crank shaft", "main journal", "rod journal", "throw radius",
        "counterweight", "flywheel flange", "inline four", "inline-4", "4-cylinder",
        "four cylinder", "four-cylinder", "connecting-rod journal",
    ),
    "flanged_pipe_branch": (
        "flanged pipe", "pipe branch", "pipe spool", "pipe tee",
        "flange face", "branch pipe",
    ),
    "simple_gear_or_pulley": ("spur gear", "gear wheel", "pulley", "sprocket", "timing pulley"),
}


def detect_advanced_template(prompt: str) -> str | None:
    text = prompt.lower()
    for ot, indicators in _ADVANCED_INDICATORS.items():
        if any(ind in text for ind in indicators):
            return ot
    return None


def looks_complex(prompt: str) -> bool:
    """Should this prompt go through the complex planner rather than the simple
    parser? True for long prompts or those with advanced-template indicators."""
    return len(prompt) > 1500 or detect_advanced_template(prompt) is not None


def classify_intent(prompt: str, provider: LLMProvider | None = None) -> CADIntentClassification:
    """Classify a prompt into a routing kind.

    Uses advanced-template indicators first, then a strict keyword classifier
    (no bracket fallback) so genuinely unknown prompts route to ``unsupported``.
    """
    from app.llm.mock_provider import _find_type_strict

    advanced = detect_advanced_template(prompt)
    if advanced:
        return CADIntentClassification(
            kind=CADIntentKind.advanced_template, template_candidate=advanced,
            confidence=0.88, reason=f"Detected advanced-template indicators for '{advanced}'.")

    candidate = _find_type_strict(prompt.lower())

    if candidate in _ADVANCED:
        return CADIntentClassification(
            kind=CADIntentKind.advanced_template, template_candidate=candidate,
            confidence=0.85, reason=f"Matched advanced template '{candidate}'.")
    if candidate in _SIMPLE:
        return CADIntentClassification(
            kind=CADIntentKind.simple_template, template_candidate=candidate,
            confidence=0.8, reason=f"Matched simple template '{candidate}'.")
    return CADIntentClassification(
        kind=CADIntentKind.unsupported, confidence=0.3,
        unsupported_reason="No supported template or safe feature graph matched this prompt.")


def plan_prompt(prompt: str, provider: LLMProvider | None = None):
    """Unified generate-first routing for any prompt -> ParseResult.

    GenerationRouter decides: precision_template | feature_graph | scad_generator |
    clarification. Template specs go through the self-check/repair loop so an
    under-specified gear/hex/bored part is fixed rather than built wrong.
    """
    from app.generation.router import route_prompt
    from app.generation.self_check import repair_spec
    from app.parsing.prompt_parser import parse_prompt
    from app.schemas.design_spec import ParseResult
    from app.schemas.generation import GenerationRouteKind

    provider = provider or get_provider()
    route = route_prompt(prompt, provider)

    def _with_route(result: ParseResult) -> ParseResult:
        result.route = route.route.value
        result.route_reason = route.reason
        return result

    # 1) Precision template (+ self-repair).
    if route.route == GenerationRouteKind.precision_template:
        result = parse_prompt(prompt, provider)
        if result.spec is not None:
            spec, notes = repair_spec(prompt, result.spec)
            result.spec = spec
            if notes:
                result.assumptions = list(result.assumptions) + notes
                result.auto_repaired = True
        return _with_route(result)

    # 2) Feature graph.
    if route.route == GenerationRouteKind.feature_graph:
        result = _feature_graph_result(prompt, provider, route.target_template)
        return _with_route(result)

    # 3) Restricted SCAD generator.
    if route.route == GenerationRouteKind.scad_generator:
        result = _scad_result(prompt, provider)
        return _with_route(result)

    # 4) Clarification.
    return _with_route(ParseResult(
        missing_required=["object_type"],
        clarification_question=(
            route.reason + " Could you describe it with basic shapes and key "
            "dimensions (e.g. 'a 40mm cube with a 10mm hole')?"
        ),
    ))


def _feature_graph_result(prompt: str, provider, target_template: str | None):
    from app.parsing.prompt_parser import parse_prompt
    from app.schemas.design_spec import ParseResult

    graph_raw = None
    try:
        graph_raw = provider.plan_feature_graph(prompt)
    except NotImplementedError:
        graph_raw = None
    if graph_raw:
        spec = _feature_graph_spec(graph_raw, provider, prompt)
        if spec is not None:
            return ParseResult(
                spec=spec,
                assumptions=["Built with the flexible CAD feature-graph fallback "
                             "(no exact template matched)"],
            )
    # Hex override but no graph -> fall back to the round template (+ repair).
    if target_template:
        from app.generation.self_check import repair_spec
        result = parse_prompt(prompt, provider)
        if result.spec is not None:
            result.spec, notes = repair_spec(prompt, result.spec)
            if notes:
                result.assumptions = list(result.assumptions) + notes
                result.auto_repaired = True
        return result
    return ParseResult(
        missing_required=["object_type"],
        clarification_question=(
            "I couldn't build a safe feature graph for that. Could you describe it "
            "with basic shapes and key dimensions?"
        ),
    )


def _scad_result(prompt: str, provider):
    """Restricted SCAD fallback: validated GeneralCADPlan -> sandboxed STL."""
    from app.schemas.design_spec import ParseResult

    try:
        plan_raw = provider.plan_general_cad(prompt)
    except NotImplementedError:
        plan_raw = None
    if not plan_raw:
        return ParseResult(
            missing_required=["object_type"],
            clarification_question=(
                "I couldn't plan that part. Could you describe it with basic shapes "
                "and key dimensions?"
            ),
        )
    from app.generation.scad_generate import plan_to_design
    return plan_to_design(plan_raw)


def _feature_graph_spec(graph_raw: dict, provider, prompt: str):
    """Validate + dry-build a feature graph (repair once); return a DesignSpec."""
    from app.cad.base import CadGenerationError
    from app.cad.feature_graph import build_feature_graph
    from app.schemas.complex_cad import CADFeatureGraph
    from app.schemas.design_spec import DesignSpec
    from pydantic import ValidationError

    for attempt in (graph_raw,):
        try:
            fg = CADFeatureGraph(**attempt)
            build_feature_graph(fg)  # dry build validates the geometry compiles
            return DesignSpec(object_type="feature_graph", feature_graph=fg.model_dump())
        except (ValidationError, CadGenerationError):
            return None
    return None


def build_complex_plan(prompt: str, provider: LLMProvider | None = None) -> ComplexCADPlan:
    """Produce a validated ComplexCADPlan. The mock never fabricates a feature
    graph from prose; it routes to templates or asks for clarification."""
    provider = provider or get_provider()
    classification = classify_intent(prompt, provider)
    meta = extract_metadata(prompt)

    plan = ComplexCADPlan(
        classification=classification,
        materials=meta["materials"],
        visual_notes=meta["visual_notes"],
    )

    if classification.kind in (CADIntentKind.simple_template, CADIntentKind.advanced_template):
        result = parse_prompt(prompt, provider=provider)
        if result.spec is not None:
            plan.template_object_type = result.spec.object_type
            plan.template_dimensions = result.spec.dims_in_mm()
        else:
            plan.clarification_question = (
                result.clarification_question or "Please provide the missing dimensions."
            )
    elif classification.kind == CADIntentKind.unsupported:
        plan.clarification_question = (
            "I can't map this to a supported template yet. Try one of the supported "
            "part types, or describe it with explicit primitives."
        )

    # Visual/material requirements that can't affect geometry are recorded only.
    if meta["visual_notes"]:
        plan.visual_notes = meta["visual_notes"]
    return plan
