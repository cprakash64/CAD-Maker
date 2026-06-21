"""Prompt classification — a structured, offline verdict computed BEFORE
generation.

This sits on top of the existing routing primitives (``assess_complexity``,
``route_prompt``, the chassis detail detector, and the family registry) and
produces a single structured object describing *what* the prompt is, *how* it
will be built, and *whether* it can be built right now. It is deterministic and
makes no LLM/CadQuery calls, so it is cheap and safe to run on every request and
to assert against in tests.

The classification is stored on the design (``semantic_json["classification"]``)
and surfaced via the API so the frontend can set honest expectations.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.cad.complexity import assess_complexity, detect_assembly_family
from app.cad.families import (
    GENERIC_ASSEMBLY_FAMILY,
    GENERIC_PART_FAMILY,
    CADFamily,
    DesignMode,
    GenerationStrategy,
    family_for_object_type,
    get_family,
)


# Coarse complexity buckets (independent of the family's maturity).
COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_MODERATE = "moderate"
COMPLEXITY_COMPLEX = "complex"
COMPLEXITY_HUGE = "huge"


@dataclass
class PromptClassification:
    family_id: str
    confidence: float
    design_mode: str
    complexity: str
    generation_strategy: str
    can_generate_now: bool
    required_missing_inputs: list[str] = field(default_factory=list)
    visible_assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    maturity: str = ""
    display_name: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "family_id": self.family_id,
            "display_name": self.display_name,
            "maturity": self.maturity,
            "confidence": round(self.confidence, 3),
            "design_mode": self.design_mode,
            "complexity": self.complexity,
            "generation_strategy": self.generation_strategy,
            "can_generate_now": self.can_generate_now,
            "required_missing_inputs": list(self.required_missing_inputs),
            "visible_assumptions": list(self.visible_assumptions),
            "limitations": list(self.limitations),
            "reason": self.reason,
        }


def _word_count(prompt: str) -> int:
    return len((prompt or "").split())


def _from_family(
    fam: CADFamily,
    *,
    confidence: float,
    complexity: str,
    can_generate_now: bool,
    required_missing: list[str] | None = None,
    strategy: GenerationStrategy | None = None,
    reason: str = "",
) -> PromptClassification:
    return PromptClassification(
        family_id=fam.family_id,
        display_name=fam.display_name,
        maturity=fam.maturity.value,
        confidence=confidence,
        design_mode=fam.design_mode.value,
        complexity=complexity,
        generation_strategy=(strategy or fam.generation_strategy).value,
        can_generate_now=can_generate_now,
        required_missing_inputs=required_missing or [],
        visible_assumptions=list(fam.default_assumptions),
        limitations=list(fam.known_limitations),
        reason=reason,
    )


# Map a single-part router decision to a generation strategy enum.
_ROUTE_TO_STRATEGY = {
    "precision_template": GenerationStrategy.deterministic_template,
    "feature_graph": GenerationStrategy.cadplan,
    "scad_generator": GenerationStrategy.cadplan,
    "clarification": GenerationStrategy.needs_clarification,
}


def classify_prompt(prompt: str) -> PromptClassification:
    """Classify a prompt into a structured, offline verdict. Never raises."""
    text = (prompt or "").strip()
    if not text:
        fam = get_family(GENERIC_PART_FAMILY)
        return _from_family(
            fam, confidence=0.0, complexity=COMPLEXITY_SIMPLE,
            can_generate_now=False, strategy=GenerationStrategy.needs_clarification,
            required_missing=["A description of the part you want"],
            reason="Empty prompt.",
        )

    # 1) Large-assembly gate first — mirrors create_design's complexity gate so
    #    classification and routing agree on whole-machine prompts.
    assessment = assess_complexity(text)
    if assessment.is_complex:
        return _classify_assembly(text, assessment)

    # 2) Single-part routing — reuse the deterministic router so the classifier's
    #    strategy matches what the generator will actually do.
    from app.generation.router import route_prompt

    route = route_prompt(text)
    object_type = route.target_template
    strategy = _ROUTE_TO_STRATEGY.get(route.route.value, GenerationStrategy.cadplan)

    # Resolve a family HONESTLY. A precision-template route names the template it
    # will use (object_type) -> map straight to that part family. Anything else
    # (feature graph / SCAD / clarification) is built from primitives, not a
    # named template, so it is the generic feature-graph family rather than a
    # template family guessed from a loose keyword.
    fam = family_for_object_type(object_type) or get_family(GENERIC_PART_FAMILY)

    complexity = _part_complexity(text)
    can_generate = route.route.value != "clarification"
    required_missing: list[str] = []
    if not can_generate:
        required_missing = list(route.unsupported_features) or [
            "A clearer mechanical description with key dimensions"
        ]

    reason = route.reason or f"Classified as {fam.display_name}."
    return _from_family(
        fam, confidence=float(route.confidence), complexity=complexity,
        can_generate_now=can_generate, required_missing=required_missing,
        strategy=strategy, reason=reason,
    )


def _classify_assembly(text: str, assessment) -> PromptClassification:
    """Classify a prompt the complexity gate flagged as a large assembly."""
    family = detect_assembly_family(text)  # "tubular_chassis" | None
    if family == "tubular_chassis":
        from app.cad.assembly.chassis import detect_detail_level

        level = detect_detail_level(text)
        fam = get_family(
            "reference_buggy_tubular_chassis" if level == "reference" else "tube_chassis"
        )
        return _from_family(
            fam, confidence=0.8, complexity=COMPLEXITY_HUGE,
            can_generate_now=True, strategy=GenerationStrategy.assembly_generator,
            reason="Supported concept assembly (tubular chassis / space frame).",
        )

    # Unsupported large assembly -> decomposition guidance, no geometry.
    fam = get_family(GENERIC_ASSEMBLY_FAMILY)
    return _from_family(
        fam, confidence=0.5, complexity=COMPLEXITY_HUGE,
        can_generate_now=False, strategy=GenerationStrategy.needs_decomposition,
        required_missing=["Pick one single component to generate first"],
        reason=assessment.reason or "Large multi-part assembly — decompose into single parts.",
    )


def _part_complexity(text: str) -> str:
    """Coarse single-part complexity from prompt length + feature density."""
    words = _word_count(text)
    feature_words = (
        "hole", "bore", "boss", "rib", "gusset", "slot", "counterbore",
        "countersink", "fillet", "chamfer", "pattern", "pocket", "thread",
    )
    n_features = sum(text.count(w) for w in feature_words)
    if words >= 80 or n_features >= 6:
        return COMPLEXITY_COMPLEX
    if words >= 30 or n_features >= 2:
        return COMPLEXITY_MODERATE
    return COMPLEXITY_SIMPLE
