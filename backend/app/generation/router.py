"""GenerationRouter — decide how to build a prompt before building it.

precision_template → feature_graph → scad_generator → clarification.
Deterministic and offline-safe (used by the mock); the same decision shape is
returned regardless of provider.
"""
from __future__ import annotations

from app.schemas.generation import GenerationRoute, GenerationRouteKind

# Phrases that must use the feature graph even though a template keyword matches
# (e.g. "bearing housing" contains the enclosure keyword "housing").
_FORCE_FEATURE_GRAPH = (
    "bearing housing", "pipe elbow", "elbow", "stepped slot", "hex spacer",
    "hexagonal spacer", "hex standoff", "hexagonal standoff", "shaft collar",
    "flange plate", "bolt circle",
)

# Clearly non-mechanical / decorative → clarify (never guess geometry).
_DECORATIVE = (
    "statue", "sculpture", "figurine", "dragon", "castle", "animal", "face",
    "artistic", "art piece", "ornament", "toy model", "character", "logo",
)


def route_prompt(prompt: str, provider=None) -> GenerationRoute:
    from app.cad.fallback_graphs import from_prompt
    from app.llm.mock_provider import _find_type_strict
    from app.parsing.complex_plan import detect_advanced_template

    text = prompt.lower()
    strict = detect_advanced_template(prompt) or _find_type_strict(text)
    is_hex = "hex" in text or "hexagon" in text
    hex_override = is_hex and strict in (None, "spacer")
    force_fg = any(p in text for p in _FORCE_FEATURE_GRAPH)

    # 1) Strong template (unless we explicitly need a more flexible route).
    if strict and not hex_override and not force_fg:
        return GenerationRoute(
            route=GenerationRouteKind.precision_template,
            confidence=0.85, target_template=strict,
            reason=f"Matched a strong template for '{strict}'.",
        )

    # 2) Feature graph — either a recognized fallback pattern, or hex/forced.
    if from_prompt(prompt) is not None or hex_override or force_fg:
        return GenerationRoute(
            route=GenerationRouteKind.feature_graph, confidence=0.8,
            reason="Buildable from safe primitives (feature graph).",
            target_template=strict if hex_override else None,
        )

    # 3) Decorative / non-mechanical → clarify.
    if any(d in text for d in _DECORATIVE):
        return GenerationRoute(
            route=GenerationRouteKind.clarification, confidence=0.4,
            reason="This looks decorative/organic rather than a mechanical part.",
            unsupported_features=["organic/decorative geometry"],
        )

    # 4) Mechanical-but-unmatched → general planner (compiles to a trusted feature
    #    graph for STL+STEP; falls back to the sandboxed OpenSCAD runner only for
    #    shapes the feature graph can't express, when the binary is installed).
    if _looks_mechanical(text):
        return GenerationRoute(
            route=GenerationRouteKind.scad_generator, confidence=0.55,
            reason="No template/feature-graph fit; using the general CAD planner.",
        )
    return GenerationRoute(
        route=GenerationRouteKind.clarification, confidence=0.4,
        reason="I can't map this to a supported part or safe geometry yet.",
    )


_MECH_HINTS = (
    "mm", "bracket", "plate", "block", "mount", "bore", "hole", "shaft", "bracket",
    "flange", "spacer", "standoff", "clamp", "housing", "boss", "rib", "slot",
    "cylinder", "tube", "ring", "disc", "disk", "hub", "adapter", "jig", "fixture",
    "gear", "pulley", "cube", "prism", "washer", "nut", "bushing", "coupler",
)


def _looks_mechanical(text: str) -> bool:
    return any(h in text for h in _MECH_HINTS)
