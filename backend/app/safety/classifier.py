"""Deterministic, regex/keyword-based safety-category detection.

Not an NLP/LLM moderation system -- a first line of defense chosen
deliberately for two reasons: (1) it is fully deterministic and testable, so
"cannot be bypassed with synonym X" is a claim we can actually prove with a
unit test; (2) it needs no external call, so it never adds latency, cost, or
an availability dependency to the generation/edit path it gates.

Known limitation (documented, not silently assumed away): this classifies
TEXT (prompts, edit notes, drawing hints/notes, object-type/family names). It
cannot infer intent from geometry alone -- a request built up purely through
numeric parameter edits with no descriptive text (or an uploaded drawing with
no notes and geometry our part-family router doesn't already recognize) is a
real, acknowledged gap. See docs/legal/safety-and-engineering-disclaimer.md.

Each category has:
  * STRONG patterns -- specific enough to trigger alone (false-positive risk
    judged low: "steering knuckle", "brake caliper", "pressure vessel").
  * WEAK patterns -- ordinary mechanical-engineering terms that are only
    treated as a signal when combined with a CONTEXT word for that category
    (e.g. "hub" alone is generic; "hub" + "car"/"motorcycle"/"vehicle" is
    not). This keeps the classifier usable for everyday CAD work instead of
    flagging every bracket and hole.

Matching is done on a normalized copy of the text: lowercased, with common
separator-insertion evasions (``g-u-n``, ``g.u.n``, ``g_u_n``) collapsed
before pattern matching, so basic spacing/punctuation obfuscation doesn't
bypass a keyword match.
"""
from __future__ import annotations

import re

from app.safety.categories import SafetyCategory

# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

# A "spelled out" run: 2+ single alphanumeric characters each separated by a
# single -/_/. (```g-u-n```, ``g_u_n``, ``g.u.n``). Bounded with explicit
# character-class lookarounds, NOT \b -- \b treats "_" as a word character,
# so a \b-based boundary check silently fails to find the edge of a
# run that uses underscores as its separator.
_SPELLED_OUT_RUN = re.compile(r"(?<![a-z0-9])((?:[a-z0-9][\-_.]){2,}[a-z0-9])(?![a-z0-9])")


def _normalize(text: str) -> str:
    """Lowercase and collapse spelled-out-with-separators evasion
    (``g-u-n``/``g_u_n``/``g.u.n`` -> ``gun``) while leaving ordinary
    hyphenated words ("brake-caliper", "3d-printed") untouched -- only a RUN
    of 2+ single-character segments is treated as evasion, not one hyphen
    between two real words."""
    lowered = text.lower()
    collapsed = _SPELLED_OUT_RUN.sub(
        lambda m: re.sub(r"[\-_.]", "", m.group(1)), lowered)
    # Remaining hyphens are ordinary compound-word punctuation ("brake-
    # caliper", "3d-printed") -- treat them as spaces so every \s+-based
    # phrase pattern matches both the hyphenated and spaced spelling without
    # needing two copies of every pattern.
    collapsed = collapsed.replace("-", " ")
    return f" {collapsed} "  # pad so \b-based patterns match at string edges


# --------------------------------------------------------------------------
# Category patterns: (strong_patterns, weak_patterns, context_words)
# --------------------------------------------------------------------------

_CONTEXT_VEHICLE = [
    r"\bcars?\b", r"\btrucks?\b", r"\bmotorcycles?\b", r"\bmotorbikes?\b",
    r"\bautomobiles?\b", r"\bvehicles?\b", r"\batvs?\b", r"\bgo[-\s]?karts?\b",
    r"\bkarts?\b", r"\bmoped\b", r"\bscooter\b",
]

_CONTEXT_MAINS = [
    r"\bmains?\b", r"\b1[01]0v\b", r"\b120v\b", r"\b220v\b", r"\b230v\b",
    r"\b240v\b", r"\bline\s+voltage\b", r"\bhousehold\s+(?:current|power|voltage)\b",
]

_CATEGORY_PATTERNS: dict[SafetyCategory, tuple[list[str], list[str], list[str]]] = {
    SafetyCategory.WEAPON_COMPONENTS: (
        [
            r"\bfire\s?arms?\b", r"\bguns?\b", r"\bpistols?\b", r"\brifles?\b",
            r"\bshotguns?\b", r"\bhandguns?\b", r"\bar[-\s]?15s?\b",
            r"\bak[-\s]?47s?\b", r"\bsilencers?\b", r"\bsuppressors?\b",
            r"\bgrenades?\b", r"\bexplosive\s+devices?\b", r"\bpipe\s+bombs?\b",
            r"\bghost\s+guns?\b", r"\bzip\s+guns?\b",
            r"\b(?:3d[-\s]?printed|printable)\s+guns?\b",
            r"\bstun\s+guns?\b", r"\btasers?\b", r"\bbrass\s+knuckles?\b",
            r"\bcrossbows?\b", r"\bflamethrowers?\b",
            r"\b(?:gun|rifle)\s+barrels?\b",
            r"\bfirearm\s+(?:receivers?|frames?|lowers?|uppers?|sears?|"
            r"hammers?|bolt\s+carriers?)\b",
            r"\b(?:lower|upper)\s+receivers?\b.{0,20}\bfirearm\b",
            r"\bfirearm\b.{0,20}\b(?:lower|upper)\s+receivers?\b",
            r"\btrigger\s+(?:group|mechanism|assembly)\b.{0,30}\b(?:gun|rifle|"
            r"pistol|firearm)\b",
            r"\b(?:gun|rifle|pistol|firearm)\b.{0,30}\btrigger\s+(?:group|"
            r"mechanism|assembly)\b",
        ],
        [],
        [],
    ),
    SafetyCategory.VEHICLE_STEERING_OR_BRAKING: (
        [
            r"\bbrake\s+(?:calipers?|rotors?|discs?|disks?|pads?|lines?|drums?|"
            r"master\s+cylinders?|boosters?)\b",
            r"\bsteering\s+(?:knuckles?|racks?|columns?|arms?|linkages?)\b",
            r"\btie\s+rods?\b", r"\bking\s?pins?\b",
        ],
        [
            r"\bball\s+joints?\b", r"\bwheel\s+hubs?\b", r"\baxles?\b",
            r"\bsuspension\s+(?:arms?|control\s+arms?)\b",
        ],
        _CONTEXT_VEHICLE,
    ),
    SafetyCategory.AEROSPACE_FLIGHT_COMPONENTS: (
        [
            r"\baircrafts?\b", r"\bairplanes?\b", r"\baeroplanes?\b",
            r"\bwing\s+spars?\b", r"\bfuselages?\b", r"\blanding\s+gears?\b",
            r"\bflight\s+control\s+surfaces?\b", r"\bailerons?\b",
            r"\belevators?\b(?!\s+(?:pit|door|shaft))", r"\brudders?\b",
            r"\bturbine\s+blades?\b", r"\brockets?\b", r"\bspacecrafts?\b",
            r"\bsatellites?\b", r"\bpropellers?\b.{0,20}\b(?:aircraft|drone|"
            r"plane|uav)\b",
        ],
        [],
        [],
    ),
    SafetyCategory.MEDICAL_DEVICES: (
        [
            r"\bimplants?\b", r"\bsurgical\s+instruments?\b",
            r"\bmedical\s+devices?\b", r"\bstents?\b", r"\bcatheters?\b",
            r"\bpacemakers?\b", r"\bventilator\s+(?:parts?|components?)\b",
            r"\bprosthetics?\b", r"\bprosthesis\b", r"\borthotics?\b",
        ],
        [
            r"\bsplints?\b", r"\bbraces?\b",
        ],
        [r"\bmedical\b", r"\bpatient\b", r"\bsurgical\b", r"\bclinical\b"],
    ),
    SafetyCategory.LIFTING_EQUIPMENT: (
        [
            r"\bcrane\s+hooks?\b", r"\bhoists?\b", r"\bwinches?\b",
            r"\block?ing\s+carabiners?\b", r"\blifting\s+eyes?\b",
            r"\bshackles?\b", r"\bslings?\b", r"\bengine\s+hoists?\b",
            r"\brigging\s+hardware\b",
        ],
        [r"\bhooks?\b", r"\bpulleys?\b", r"\bjacks?\b"],
        [r"\blift(?:ing)?\b", r"\bhoist(?:ing)?\b", r"\bsuspend(?:ing|ed)?\s+"
         r"(?:a\s+)?(?:load|weight|person|people)\b"],
    ),
    SafetyCategory.PRESSURE_CONTAINING: (
        [
            r"\bpressure\s+vessels?\b", r"\bgas\s+cylinders?\b",
            r"\bpropane\s+tanks?\b", r"\bscuba\s+tanks?\b", r"\bboilers?\b",
            r"\bcompressed\s+air\s+tanks?\b", r"\bautoclaves?\b",
            r"\bair\s+compressor\s+tanks?\b",
        ],
        [r"\btanks?\b", r"\breservoirs?\b", r"\bfittings?\b"],
        [r"\bpressure\b", r"\bpsi\b", r"\bbar(?:s)?\s+(?:of\s+)?pressure\b",
         r"\bcompressed\b"],
    ),
    SafetyCategory.CHILD_SAFETY_PRODUCTS: (
        [
            r"\bcribs?\b", r"\bcar\s+seats?\b", r"\bstrollers?\b",
            r"\bbaby\s+gates?\b", r"\bbassinets?\b", r"\bhigh\s+chairs?\b",
            r"\bplayground\s+equipment\b", r"\bteethers?\b",
        ],
        [r"\btoys?\b"],
        [r"\binfants?\b", r"\bbab(?:y|ies)\b", r"\btoddlers?\b",
         r"\bchild(?:ren)?\b"],
    ),
    SafetyCategory.FIRE_SAFETY_EQUIPMENT: (
        [
            r"\bfire\s+extinguishers?\b", r"\bsprinkler\s+heads?\b",
            r"\bfire\s+suppression\b", r"\bfire\s+escape\s+ladders?\b",
            r"\bsmoke\s+detectors?\b",
        ],
        [],
        [],
    ),
    SafetyCategory.MAINS_ELECTRICAL_SAFETY: (
        [
            r"\bmains?\s+(?:voltage\s+)?enclosures?\b",
            r"\belectrical\s+outlets?\b", r"\bwall\s+sockets?\b",
            r"\bcircuit\s+breaker\s+panels?\b", r"\bhigh[-\s]?voltage\s+"
            r"enclosures?\b",
        ],
        [r"\bjunction\s+boxe?s?\b", r"\bplug\s+housings?\b",
         r"\boutlet\s+covers?\b"],
        _CONTEXT_MAINS,
    ),
    # No dedicated keyword set: this is a floor triggered by generic
    # high-stakes phrasing that doesn't match any of the more specific
    # categories above (see classify_text()).
    SafetyCategory.OTHER_HIGH_CONSEQUENCE: ([], [], []),
    SafetyCategory.STRUCTURAL_LOAD_BEARING: (
        [
            r"\bload[-\s]?bearing\b", r"\bstructural\s+(?:support|member|"
            r"beam|column)\b", r"\broll\s+cages?\b", r"\bengine\s+mounts?\b",
            r"\btow\s+hooks?\b", r"\btrailer\s+hitch(?:es)?\b",
            r"\bfloor\s+joists?\b", r"\bjack\s+stands?\b",
        ],
        [r"\bbeams?\b", r"\bbrackets?\b", r"\bsupports?\b"],
        [r"\bsupport(?:ing|s)?\b.{0,40}\b(?:person|people|weight|load|"
         r"a\s+car|a\s+truck|a\s+vehicle)\b",
         r"\bstand(?:ing)?\s+on\b",
         r"\bhold(?:s|ing)?\b.{0,15}\bup\b"],
    ),
}

_GENERIC_HIGH_STAKES_PATTERNS = [
    r"\blife\s+support\b", r"\bsafety[-\s]?critical\b",
    r"\bfail[-\s]?safe\s+required\b", r"\bmust\s+not\s+fail\b",
    r"\bcertified\s+safe\b", r"\bstructural\s+integrity\s+(?:is\s+)?"
    r"(?:required|critical|essential)\b",
]


def classify_text(*texts: str | None) -> set[SafetyCategory]:
    """Classify one or more text fragments (prompt, edit note, drawing hint,
    object_type/family name, ...) and return the union of every safety
    category whose pattern matched somewhere across all of them."""
    joined = " ".join(t for t in texts if t)
    if not joined.strip():
        return set()
    norm = _normalize(joined)

    found: set[SafetyCategory] = set()
    for category, (strong, weak, context) in _CATEGORY_PATTERNS.items():
        if any(re.search(p, norm) for p in strong):
            found.add(category)
            continue
        if weak and any(re.search(p, norm) for p in weak):
            if not context or any(re.search(c, norm) for c in context):
                found.add(category)

    if SafetyCategory.OTHER_HIGH_CONSEQUENCE not in found and \
            any(re.search(p, norm) for p in _GENERIC_HIGH_STAKES_PATTERNS):
        found.add(SafetyCategory.OTHER_HIGH_CONSEQUENCE)

    return found
