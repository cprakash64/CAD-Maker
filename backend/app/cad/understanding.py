"""Prompt understanding layer — a structured reading of a plain-English request
computed BEFORE any geometry is attempted.

This sits one level above :mod:`app.cad.classification`. The classifier answers
*which family / strategy* a prompt maps to; this layer adds the concrete things a
caller needs to decide a SAFE route under the universal generation contract:

  * ``object_type``        — the canonical generator type (or ``None`` if unknown)
  * ``family``             — the family id from the registry
  * ``dimensions``         — numeric dimensions parsed out of the text (mm)
  * ``features``           — mechanical features the prompt mentions
  * ``missing_fields``     — required inputs that are absent
  * ``complexity``         — coarse bucket (simple|moderate|complex|huge)
  * ``recommended_route``  — how the contract should handle this prompt

It is pure string analysis on top of the (already offline) classifier: no LLM and
no CadQuery, so it is cheap and safe to run on every request and to assert in
tests. It never raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.cad.classification import classify_prompt
from app.cad.families import (
    GENERIC_PART_FAMILY,
    DesignMode,
    Maturity,
    get_family,
)

# Recommended routes — what the universal contract should DO with this prompt.
# These map onto the contract's terminal states (see app.cad.contract).
ROUTE_SINGLE_PART = "generate_single_part"
ROUTE_ASSEMBLY = "generate_assembly"
ROUTE_CONCEPT_FALLBACK = "concept_fallback"
ROUTE_CLARIFY = "needs_clarification"
ROUTE_DECOMPOSE = "needs_decomposition"
ROUTE_UNSUPPORTED = "unsupported"


# --- lightweight dimension / feature extraction ---------------------------

# "120x80x8", "120 x 80 x 8 mm" -> (length, width, thickness/height)
_TRIPLE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×]\s*(\d+(?:\.\d+)?)",
    re.I,
)
# "120 x 80" pair.
_PAIR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm)?(?![x×\d])", re.I
)
# Diameter callouts: "Ø12", "12mm diameter", "diameter of 12", "dia 12".
_DIA_RE = re.compile(
    r"(?:ø|⌀)\s*(\d+(?:\.\d+)?)"
    r"|(\d+(?:\.\d+)?)\s*mm\s*(?:outer\s+)?dia(?:meter)?\b"
    r"|\bdia(?:meter)?\s*(?:of\s*)?(\d+(?:\.\d+)?)",
    re.I,
)
_BORE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\s*bore|\bbore\s*(?:of\s*)?(\d+(?:\.\d+)?)", re.I)
_THICK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\s*thick|\bthick(?:ness)?\s*(?:of\s*)?(\d+(?:\.\d+)?)", re.I)
_LEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\s*(?:long|length)|\blength\s*(?:of\s*)?(\d+(?:\.\d+)?)", re.I)
# Hole count + size: "two M6 holes", "4x 5mm holes", "six 9mm bolt holes".
_METRIC_RE = re.compile(r"\bM(\d+(?:\.\d+)?)\b")
_GENERIC_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\b", re.I)

# Common everyday objects we do NOT have a deterministic family for. Free-form
# CAD for these frequently yields disconnected / broken geometry, so the contract
# steers them to a clarification (no dims) or a labelled concept (with dims)
# instead of a confident single-part promise. This is the single source of truth
# shared with design_service's everyday-object gate.
# NB: hammer / wrench / pliers / wheel / fan / hook / handle / tool holder /
# stand / casing now have concept-fallback families and are intentionally NOT
# here — they generate concept CAD rather than clarifying.
UNSUPPORTED_EVERYDAY = (
    "saw", "axe", "hatchet", "chisel", "crowbar", "shovel", "rake", "scissors",
    "drill", "screw gun", "ratchet",
)


def detect_unsupported_everyday(prompt: str) -> str | None:
    """Return the matched unsupported-everyday object word, or None."""
    t = (prompt or "").lower()
    for word in UNSUPPORTED_EVERYDAY:
        if re.search(rf"\b{re.escape(word)}\b", t):
            return word
    return None


_FEATURE_WORDS = (
    "hole", "bore", "counterbore", "countersink", "slot", "fillet", "chamfer",
    "rib", "gusset", "boss", "keyway", "thread", "groove", "pocket", "flange",
    "bolt circle", "tooth", "teeth", "hub", "pivot", "shell", "wall", "lip",
    "bracket", "tab", "vent",
)


def _first(m: re.Match | None) -> float | None:
    if not m:
        return None
    for g in m.groups():
        if g:
            return float(g)
    return None


def extract_dimensions(prompt: str) -> dict[str, float]:
    """Best-effort numeric dimensions (mm) parsed from free text. Conservative:
    only records values it can attach to a role, plus a raw count of mm values."""
    text = prompt or ""
    dims: dict[str, float] = {}

    triple = _TRIPLE_RE.search(text)
    if triple:
        dims["length"] = float(triple.group(1))
        dims["width"] = float(triple.group(2))
        dims["height"] = float(triple.group(3))
    else:
        pair = _PAIR_RE.search(text)
        if pair:
            dims["length"] = float(pair.group(1))
            dims["width"] = float(pair.group(2))

    for key, rx in (("diameter", _DIA_RE), ("bore", _BORE_RE),
                    ("thickness", _THICK_RE), ("length", _LEN_RE)):
        v = _first(rx.search(text))
        if v is not None:
            dims.setdefault(key, v)

    metric = _METRIC_RE.search(text)
    if metric:
        dims.setdefault("hole_diameter", float(metric.group(1)))

    dims["numeric_value_count"] = float(len(_GENERIC_NUM_RE.findall(text))
                                        + (1 if metric else 0))
    return dims


def extract_features(prompt: str) -> list[str]:
    """Mechanical features mentioned in the prompt (deduped, stable order)."""
    text = (prompt or "").lower()
    found: list[str] = []
    for word in _FEATURE_WORDS:
        if word in text:
            canon = "tooth" if word == "teeth" else word
            if canon not in found:
                found.append(canon)
    return found


def _has_real_dimensions(dims: dict[str, float]) -> bool:
    """True when the prompt carries at least one concrete size (not just a count)."""
    return any(k != "numeric_value_count" for k in dims)


@dataclass
class PromptUnderstanding:
    object_type: str | None
    family: str
    dimensions: dict[str, float]
    features: list[str]
    missing_fields: list[str]
    complexity: str
    recommended_route: str
    confidence: float = 0.0
    display_name: str = ""
    maturity: str = ""
    reason: str = ""
    visible_assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "family": self.family,
            "display_name": self.display_name,
            "maturity": self.maturity,
            "dimensions": dict(self.dimensions),
            "features": list(self.features),
            "missing_fields": list(self.missing_fields),
            "complexity": self.complexity,
            "recommended_route": self.recommended_route,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "visible_assumptions": list(self.visible_assumptions),
        }


# Map the classifier's generation strategy onto a contract route. Generating
# strategies are refined by design mode (part vs assembly) and family maturity.
def _route_from_classification(cls, prompt: str, dims: dict[str, float]) -> str:
    strat = cls.generation_strategy
    if strat == "needs_decomposition":
        return ROUTE_DECOMPOSE
    if strat == "unsupported":
        return ROUTE_UNSUPPORTED
    if strat == "needs_clarification" or not cls.can_generate_now:
        return ROUTE_CLARIFY

    # Unsupported everyday object that only matched the generic fallback: don't
    # promise a part. With concrete dimensions build a simplified concept; without
    # them, ask. (Supported tools — screwdriver, knob — map to a real family and
    # never reach here.)
    if cls.family_id == GENERIC_PART_FAMILY and detect_unsupported_everyday(prompt):
        return ROUTE_CONCEPT_FALLBACK if _has_real_dimensions(dims) else ROUTE_CLARIFY

    fam = get_family(cls.family_id)
    if fam and fam.design_mode == DesignMode.assembly:
        return ROUTE_ASSEMBLY
    # A concept-maturity part with no concrete dimensions is a recognizable-but-
    # under-specified object: build a simplified concept rather than promising a
    # production part. With real dimensions it generates normally.
    if fam and fam.maturity == Maturity.concept and not _has_real_dimensions(dims):
        return ROUTE_CONCEPT_FALLBACK
    return ROUTE_SINGLE_PART


def understand_prompt(prompt: str) -> PromptUnderstanding:
    """Structured, offline reading of a prompt. Never raises."""
    cls = classify_prompt(prompt)
    fam = get_family(cls.family_id)
    dims = extract_dimensions(prompt or "")
    feats = extract_features(prompt or "")
    route = _route_from_classification(cls, prompt or "", dims)

    object_type = None
    if fam and fam.object_types:
        object_type = fam.object_types[0]

    missing = list(cls.required_missing_inputs)
    # If we're set to generate but the family wants dimensions and none were
    # parsed, surface that as a (non-blocking) missing field for transparency.
    if route in (ROUTE_SINGLE_PART, ROUTE_ASSEMBLY) and fam \
            and fam.required_dimensions and not _has_real_dimensions(dims):
        for d in fam.required_dimensions:
            if d not in missing:
                missing.append(d)

    return PromptUnderstanding(
        object_type=object_type,
        family=cls.family_id,
        dimensions=dims,
        features=feats,
        missing_fields=missing,
        complexity=cls.complexity,
        recommended_route=route,
        confidence=float(cls.confidence),
        display_name=cls.display_name,
        maturity=cls.maturity,
        reason=cls.reason,
        visible_assumptions=list(cls.visible_assumptions),
    )
