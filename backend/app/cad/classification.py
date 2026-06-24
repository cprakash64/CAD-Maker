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

import re
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

    low = text.lower()

    # 1) Supported deterministic frame / concept-assembly families (machine
    #    frame, drone, motorcycle subframe, skateboard mount, ...). Detected the
    #    same way the generator routes them, so classification == what is built,
    #    and these never fall through to decomposition/timeout.
    from app.cad.assembly.frames import detect_frame_family

    frame_fam_id = detect_frame_family(low)
    if frame_fam_id:
        fam = get_family(frame_fam_id)
        complexity = (COMPLEXITY_COMPLEX if fam.design_mode == DesignMode.single_part
                      else COMPLEXITY_HUGE)
        return _from_family(
            fam, confidence=0.85, complexity=complexity, can_generate_now=True,
            strategy=GenerationStrategy.assembly_generator,
            reason=f"Supported deterministic {fam.display_name}.",
        )

    # 2) Large-assembly gate — mirrors create_design's complexity gate so
    #    classification and routing agree on whole-machine prompts.
    assessment = assess_complexity(text)
    if assessment.is_complex:
        return _classify_assembly(text, assessment)

    # 3) Dedicated medium single-part families that the keyword router would
    #    otherwise mislabel (U bracket -> flat plate, robotic arm base -> plate).
    #    Detected the same way the deterministic planner dispatches them.
    part_fam_id = _detect_dedicated_part_family(low)
    if part_fam_id:
        fam = get_family(part_fam_id)
        return _from_family(
            fam, confidence=0.8, complexity=_part_complexity(text),
            can_generate_now=True, strategy=GenerationStrategy.cadplan,
            reason=f"Classified as {fam.display_name}.",
        )

    # 4) Single-part routing — reuse the deterministic router so the classifier's
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


# Dedicated medium-part detection — mirrors the deterministic planner's dispatch
# (app.cad.plan.deterministic._FAMILIES), most-specific-first, so the classifier
# names the same family the generator will build.
def _detect_dedicated_part_family(low: str) -> str | None:
    # Hex standoff/spacer is built by a dedicated deterministic route — classify
    # it the same way so the verdict matches what is generated.
    from app.cad.hex_standoff import is_hex_standoff_prompt

    if is_hex_standoff_prompt(low):
        return "hex_standoff"
    if "screwdriver" in low or "screw driver" in low:
        return "screwdriver"
    if re.search(r"\brobot(?:ic)?\b", low) and "arm" in low \
            and re.search(r"\bbase\b|\bbracket\b|\btower\b|\bgusset\b|\bbearing\b", low):
        return "robotic_arm_base_bracket"
    if "clamp" in low and re.search(r"\btube\b|\bpipe\b|\brod\b|\bbar\b|\bshaft\b", low):
        return "clamp_block"
    if re.search(r"\bu[- ]?(shaped|bracket)\b", low):
        return "u_bracket"
    if re.search(r"\bhinge\b", low):
        return "hinge_bracket"
    # Everyday concept-fallback families — mirror the deterministic planner's
    # dispatch order so the classifier names the same family the generator builds.
    concept = _detect_concept_family(low)
    if concept:
        return concept
    return None


# family_id for each everyday concept-fallback family, detected the same way the
# deterministic planner (_FAMILIES) routes them, most-specific-first.
def _detect_concept_family(low: str) -> str | None:
    if re.search(r"\bhammer\b|\bmallet\b", low):
        return "hammer"
    if re.search(r"\bwrench(?:es)?\b|\bspanner\b", low):
        return "wrench"
    if re.search(r"\bpliers?\b", low):
        return "pliers"
    if re.search(r"\bfan\b|\bimpeller\b", low):
        return "fan_blade"
    if re.search(r"\bwheel\b", low):
        return "wheel"
    if re.search(r"\bhook\b", low):
        return "hook"
    if re.search(r"\btool[- ]?(holder|rack|organi[sz]er)\b", low):
        return "tool_holder"
    if re.search(r"\bcasing\b", low) or "simple case" in low:
        return "simple_casing"
    if re.search(r"\bstand\b", low):
        return "generic_stand"
    if (re.search(r"\bhandle\b|\bgrip\b", low) or "drawer pull" in low
            or "door pull" in low) and "screwdriver" not in low:
        return "generic_handle"
    return None


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
