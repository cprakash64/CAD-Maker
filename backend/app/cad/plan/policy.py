"""Generation policy — decide whether a plan needs clarification.

ASSUMPTION-FIRST: the default is to GENERATE. Clarification is FATAL only when
the primary shape or primary scale cannot be inferred at all (no features and no
usable dimensions), or the plan is explicitly non-mechanical/impossible. Missing
SECONDARY dimensions are downgraded to warnings — the normalizer fills them.

(Python mirror of the requested ``src/lib/cad/generationPolicy.ts``.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.cad.plan.schema import CadPlan

# Phrases that signal a recognizable mechanical object even if the planner was
# unsure — we should generate, not interrogate.
_MECHANICAL_INTENT = (
    "bearing block", "hinge bracket", "sensor enclosure", "enclosure",
    "u bracket", "u-bracket", "u-shaped", "l bracket", "l-bracket",
    "pipe spool", "flanged tee", "flanged pipe", "pipe branch", "tee",
    "blind flange", "flange", "plate", "bracket", "boss", "mount", "spool",
)

ClarificationSeverity = str  # "none" | "warning" | "fatal"


@dataclass
class ClarificationDecision:
    severity: ClarificationSeverity
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def looks_mechanical(prompt: str) -> bool:
    t = (prompt or "").lower()
    return any(p in t for p in _MECHANICAL_INTENT)


def decide_clarification(plan: CadPlan, prompt: str = "") -> ClarificationDecision:
    """Assumption-first decision.

    - FATAL only when primary geometry/scale is impossible (no features to build)
      OR the plan flagged an ask-required ambiguity category (topology, overall
      size, fit, mating geometry, fastener standard, bearing/shaft interface,
      assembly relationship, safety/load) — see
      app.cad.plan.clarification_categories. That check runs FIRST and
      overrides everything below it: a category-level ambiguity is ALWAYS
      fatal, even for an otherwise-buildable plan, and this is a pure function
      of the category tags, not of clarification_required — so the same
      category is always handled the same way, regardless of what an LLM's
      boolean happened to say for a given response.
    - Otherwise NONE/WARNING — secondary/cosmetic gaps become assumptions, not
      questions.
    - An LLM that over-eagerly set clarification_required for a buildable part
      (with no ask-required category flagged) is downgraded to a warning (we
      generate anyway).
    """
    from app.cad.plan.clarification_categories import ASK_REQUIRED_CATEGORIES, questions_for

    ask_flags = [f for f in plan.ambiguity_flags if f in ASK_REQUIRED_CATEGORIES]
    if ask_flags:
        return ClarificationDecision(
            severity="fatal",
            questions=plan.clarification_questions or questions_for(ask_flags),
        )

    # A buildable plan is never fatal — generate it, regardless of what the LLM
    # put in clarification_required.
    if plan.features:
        warnings = list(plan.clarification_questions) if plan.clarification_required else []
        return ClarificationDecision(
            severity="warning" if warnings else "none", warnings=warnings,
        )

    # No features. A DELIBERATE clarification (the planner determined the part is
    # impossible or its PRIMARY scale/shape is missing) is fatal — ask.
    if plan.clarification_required:
        return ClarificationDecision(
            severity="fatal",
            questions=plan.clarification_questions
            or ["Could you give the part's main shape and size?"],
        )

    # No features and no deliberate clarification. If the object is recognizable,
    # let the caller fall back to a deterministic/legacy builder (warning); only a
    # genuinely unrecognizable, non-mechanical prompt is fatal.
    if looks_mechanical(prompt):
        return ClarificationDecision(
            severity="warning",
            warnings=["planner produced no geometry; using a default build"],
        )
    return ClarificationDecision(
        severity="fatal",
        questions=["What mechanical part should I build, and roughly what size?"],
    )
