"""Prompt complexity gate — runs BEFORE any expensive LLM / CadQuery work.

Some prompts describe whole machines or large multi-subsystem assemblies (a car
chassis, an airframe, a robot) that are far outside the scope of single-part
parametric generation. Attempting them synchronously wastes minutes of LLM
fallback time and produces nothing usable. This module detects those prompts
cheaply (pure string analysis, no model call) so the API can return a fast,
structured "decompose this" response instead of hanging.

It is deliberately CONSERVATIVE: it only fires on strong whole-machine signals
or a large number of distinct subsystems, so ordinary single parts (mounting
plate, bracket, flange, crankshaft, enclosure, …) are never blocked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Whole-machine / vehicle words. Short, ambiguous ones use word boundaries.
_MACHINE_PATTERNS = [
    r"\bchassis\b", r"\bcar\b", r"\btruck\b", r"\bvehicle\b", r"\bautomobile\b",
    r"\bmotorcycle\b", r"\baircraft\b", r"\bairplane\b", r"\bairframe\b",
    r"\bfuselage\b", r"\bdrone\b", r"\bquadcopter\b", r"\bspaceship\b",
    r"\brocket\b", r"\bgo[- ]?kart\b", r"\btractor\b",
    "sports car", "roll cage", "rollcage", "engine bay", "transmission tunnel",
]

# STRONG subsystem signals — these, in numbers, reliably indicate a whole
# multi-subsystem machine rather than a single part, so they drive the
# is_complex *threshold*. Kept conservative to avoid decomposing ordinary parts.
_SUBSYSTEMS = [
    "suspension", "roll cage", "rollcage", "engine bay", "engine mount",
    "transmission", "drivetrain", "cross-member", "cross member", "crossmember",
    "dashboard", "side-impact", "side impact", "fuel tank", "radiator",
    "body panel", "steering column", "subframe", "sub-frame", "roll bar",
    "floor pan", "bulkhead", "firewall", "differential", "exhaust system",
    "cooling system", "wheel hub", "suspension mount", "seat mount",
]

# BROADER, mechanical subsystem vocabulary used only to NAME detected systems in
# decomposition guidance (so the UI never shows "0 subsystems detected"). These
# do NOT lower the is_complex threshold — they only enrich the guidance once a
# prompt is already deemed complex.
_GUIDANCE_SUBSYSTEMS = _SUBSYSTEMS + [
    "frame", "gantry", "bed", "rails", "linear rail", "rail", "motor mount",
    "motor mounting", "motor plate", "mounting plate", "electronics panel",
    "electronics", "control panel", "controller", "battery tray", "battery",
    "bracket", "brackets", "gusset", "bearing", "axle", "actuator", "tray",
    "panel", "leg", "base frame", "side plate", "bridge", "spindle",
]

_ASSEMBLY_WORDS = [
    "assembly", "subsystem", "organized components", "full vehicle",
    "complete vehicle", "whole car", "entire frame",
]

_LONG_PROMPT = 1200  # chars


# Supported assembly families that we can generate a simplified concept model
# for (rather than only returning needs_decomposition).
_TUBULAR_CHASSIS_PATTERNS = [
    r"\bchassis\b", r"\bspace[- ]?frame\b", "roll cage", "rollcage", r"\broll hoop\b",
    r"tubular .{0,20}frame", r"welded .{0,20}(tube|tubular)", r"tube .{0,12}frame",
]


def detect_assembly_family(prompt: str) -> str | None:
    """Return a supported assembly family key, or None.

    Currently only the tubular vehicle chassis / roll cage / welded space-frame
    family is supported for concept-assembly generation."""
    t = (prompt or "").lower()
    if any(re.search(p, t) for p in _TUBULAR_CHASSIS_PATTERNS):
        return "tubular_chassis"
    return None


@dataclass
class ComplexityAssessment:
    is_complex: bool
    reason: str = ""
    supported_family: str | None = None
    subsystems: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    recommended_first: str = ""
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "components": self.components,
            "recommended_first": self.recommended_first,
            "examples": self.examples,
        }


def _found_in(t: str, vocab: list[str]) -> list[str]:
    seen: list[str] = []
    for s in vocab:
        if s in t and s.replace("-", " ") not in [x.replace("-", " ") for x in seen]:
            seen.append(s)
    return seen


def _found_subsystems(t: str) -> list[str]:
    """Strong subsystem signals — used for the is_complex threshold."""
    return _found_in(t, _SUBSYSTEMS)


def _found_guidance_systems(t: str) -> list[str]:
    """Broader detected systems — used to name components in guidance so the UI
    shows useful systems instead of '0 subsystems detected'."""
    return _found_in(t, _GUIDANCE_SUBSYSTEMS)


def _has_machine(t: str) -> bool:
    return any(re.search(p, t) for p in _MACHINE_PATTERNS)


def assess_complexity(prompt: str) -> ComplexityAssessment:
    """Decide whether a prompt is a large assembly that must be decomposed.

    Pure string analysis — no LLM call. Conservative by design."""
    t = (prompt or "").lower()
    subsystems = _found_subsystems(t)          # strong signals (drive threshold)
    machine = _has_machine(t)
    assembly_word = any(w in t for w in _ASSEMBLY_WORDS)

    is_complex = (
        machine
        or len(subsystems) >= 4
        or (len(t) > _LONG_PROMPT and len(subsystems) >= 2)
    )
    if not is_complex:
        return ComplexityAssessment(is_complex=False)

    family = detect_assembly_family(t)

    # Name detected systems using the broader vocabulary so guidance is specific
    # (never "0 subsystems detected"). Fall back to the strong list, then to a
    # generic-but-still-useful set.
    detected = _found_guidance_systems(t) or subsystems
    pretty = [s.replace("-", " ").title() for s in detected]
    components = pretty or ["Main frame", "Mounting brackets", "Sub-assemblies"]
    # Always lead with the simplest buildable unit.
    base_components = ["Main frame member / structural tube", *components]

    recommended_first = (
        "Start with one small, well-defined part — e.g. a single mounting "
        "bracket or one frame member — then assemble parts later."
    )

    examples = [
        "A rectangular mounting bracket 80mm × 40mm × 5mm with two M6 holes",
        "An L bracket with 60mm legs, 5mm thick, 20mm wide, two 6mm holes per face",
    ]
    if any("suspension" in s or "mount" in s for s in detected):
        examples.append(
            "A suspension mounting bracket: 60×40×6mm plate with a 12mm pivot hole"
        )
    if "roll cage" in t or "rollcage" in t or "tube" in t:
        examples.append(
            "A single roll-cage tube: round tube 38mm OD, 2mm wall, 600mm long"
        )
    if "engine bay" in t or "engine mount" in t:
        examples.append(
            "An engine mount bracket: 90×45×8mm plate with four M8 holes"
        )
    if any(s in detected for s in ("gantry", "bed", "rails", "rail", "linear rail")):
        examples.append(
            "A single gantry side plate: 200×120×10mm with linear-rail bolt holes"
        )

    n_named = len(detected)
    descriptor = (
        f" ({'machine/vehicle, ' if machine else ''}{n_named} subsystems detected)"
        if n_named else (" (whole machine/vehicle)" if machine else "")
    )
    reason = (
        "This describes a large multi-part assembly" + descriptor
        + ", which is beyond single-part generation. Generate one component at a "
        "time and assemble them, rather than the whole structure at once."
    )

    return ComplexityAssessment(
        is_complex=True,
        reason=reason,
        supported_family=family,
        subsystems=detected,
        components=base_components,
        recommended_first=recommended_first,
        examples=examples[:5],
    )
