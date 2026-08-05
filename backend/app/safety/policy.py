"""Safety policy engine: categories -> enforcement decision.

This is the ONLY place that turns a set of detected `SafetyCategory` values
into (a) an enforcement action and (b) the exact user-facing message. Every
choke point in app.services.design_service calls `decide()` (via
`safety_gate()` below) instead of hand-rolling behavior per category.

Sticky/cumulative classification: `merge_categories()` unions a design's
previously-detected categories with newly-detected ones from an edit and NEVER
drops a category. This is what stops an edit from "laundering" a design that
was already flagged -- e.g. create a plain bracket, then edit the prompt to
add brake-caliper geometry: the design's stored `safety.categories` keeps
BOTH, and the resulting policy is the more restrictive of the two forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.cad.base import CadGenerationError
from app.safety.categories import (
    CATEGORY_LABELS,
    CATEGORY_MESSAGES,
    CATEGORY_POLICY,
    PolicyAction,
    SafetyCategory,
    most_restrictive,
)

ENGINEERING_REVIEW_NOTICE = (
    "Engineering review by a qualified professional is required before "
    "manufacture or use."
)


@dataclass
class SafetyDecision:
    categories: list[str]  # SafetyCategory values, sorted, sticky-merged
    policy: PolicyAction
    message: str | None
    engineering_review_required: bool
    acknowledged: bool = False

    def to_dict(self) -> dict:
        return {
            "categories": list(self.categories),
            "policy": self.policy.value,
            "message": self.message,
            "engineering_review_required": self.engineering_review_required,
            "acknowledged": self.acknowledged,
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> "SafetyDecision":
        raw = raw or {}
        return cls(
            categories=list(raw.get("categories") or []),
            policy=PolicyAction(raw.get("policy") or PolicyAction.NONE.value),
            message=raw.get("message"),
            engineering_review_required=bool(raw.get("engineering_review_required")),
            acknowledged=bool(raw.get("acknowledged")),
        )

    @property
    def blocks_export(self) -> bool:
        return self.policy in (
            PolicyAction.CONCEPTUAL_ONLY,
            PolicyAction.BLOCK_EXPORT,
        ) or (self.policy is PolicyAction.REQUIRE_ACKNOWLEDGMENT and not self.acknowledged)


class SafetyRefusalError(CadGenerationError):
    """Raised when a request's merged safety categories resolve to REFUSE.

    Subclasses CadGenerationError deliberately: every existing router already
    catches CadGenerationError and turns it into a clean 422 with the
    exception's own (safe) message, and the job-queue error classifier
    (app.services.job_service.classify_error) already maps it to
    category="invalid_input" (one attempt, no pointless retries). Reusing
    that path means zero new router wiring for the REFUSE behavior.
    """

    def __init__(self, decision: SafetyDecision):
        super().__init__(decision.message or "This request cannot be generated.")
        self.decision = decision


def merge_categories(existing: list[str], new: set[SafetyCategory]) -> list[str]:
    """Sticky union: every category ever detected for a design stays flagged,
    even if the triggering text is later edited away."""
    merged = {SafetyCategory(c) for c in existing if c in {e.value for e in SafetyCategory}}
    merged |= new
    return sorted(c.value for c in merged)


def decide(categories: list[str] | set[SafetyCategory]) -> SafetyDecision:
    cats = sorted(
        (c.value if isinstance(c, SafetyCategory) else c) for c in categories
    )
    if not cats:
        return SafetyDecision(
            categories=[], policy=PolicyAction.NONE, message=None,
            engineering_review_required=False,
        )
    enum_cats = [SafetyCategory(c) for c in cats]
    policy = most_restrictive([CATEGORY_POLICY[c] for c in enum_cats])
    # Message: every matched category's own explanation, most-restrictive
    # first, deduplicated in insertion order.
    ordered = sorted(enum_cats, key=lambda c: list(CATEGORY_POLICY).index(c)
                      if c in CATEGORY_POLICY else 999)
    seen: list[str] = []
    for c in ordered:
        msg = CATEGORY_MESSAGES.get(c)
        if msg and msg not in seen:
            seen.append(msg)
    message = " ".join(seen) if seen else None
    engineering_review_required = policy != PolicyAction.NONE
    return SafetyDecision(
        categories=cats, policy=policy, message=message,
        engineering_review_required=engineering_review_required,
    )


def safety_gate(existing_categories: list[str], *texts: str | None) -> SafetyDecision:
    """The single call every generation/edit choke point makes. Classifies
    `texts`, merges with `existing_categories` (sticky), and returns the
    resulting decision. Raises SafetyRefusalError if the result is REFUSE --
    callers must call this BEFORE spending compute on generation, and before
    mutating the design, so a refusal never partially applies."""
    from app.safety.classifier import classify_text

    new = classify_text(*texts)
    merged = merge_categories(existing_categories, new)
    decision = decide(merged)
    if decision.policy is PolicyAction.REFUSE:
        raise SafetyRefusalError(decision)
    return decision


def category_label(category: str) -> str:
    try:
        return CATEGORY_LABELS[SafetyCategory(category)]
    except ValueError:
        return category
