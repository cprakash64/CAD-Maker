"""Part Family Contract — the honesty layer that guarantees LunaiCAD never
silently substitutes a different part family.

A prompt declares a REQUESTED family (and sometimes a variant). Generation
produces a RESOLVED family. The contract records both, plus what was unsupported
or substituted, and a single ``generation_honesty_status``:

  * ``exact``        — built exactly the requested family/variant
  * ``partial``      — built it but inferred/assumed a required input (REVIEW)
  * ``substituted``  — built a DIFFERENT family/variant than requested (REVIEW)
  * ``unsupported``  — the requested family/variant isn't implemented; no fake
                       geometry is offered as if it were the real thing

The router (``detect_part_request``) recognizes families up front so the pipeline
can route to the right builder or stop honestly — e.g. a "GT2 pulley" must never
become a spur gear, a "nyloc nut" must never silently become a plain hex nut.

This module is pure parsing/strings (no CAD kernel, no LLM), so it is cheap and
deterministic on every request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Honesty statuses.
HONESTY_EXACT = "exact"
HONESTY_PARTIAL = "partial"
HONESTY_SUBSTITUTED = "substituted"
HONESTY_UNSUPPORTED = "unsupported"


@dataclass
class PartRequest:
    """What the prompt asked for (before generation)."""

    requested_family: str
    requested_variant: str | None = None
    # "buildable" -> we have a template; "unsupported_family"/"unsupported_variant"
    # -> recognized but not implemented; None -> not a recognized special family.
    support: str = "buildable"
    object_type: str | None = None        # template object_type for buildable parts
    params: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)        # required inputs we had to assume
    unsupported_features: list[str] = field(default_factory=list)
    note: str = ""


# --- shared parsing helpers -------------------------------------------------
_METRIC = re.compile(r"\bM\s?(\d+(?:\.\d+)?)(?!\.?\d)", re.I)
_PITCH = re.compile(r"\bM\s?\d+(?:\.\d+)?\s*[x×\-]\s*(\d+(?:\.\d+)?)\b", re.I)

ISO_COARSE_PITCH = {
    2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0, 8.0: 1.25,
    10.0: 1.5, 12.0: 1.75, 14.0: 2.0, 16.0: 2.0, 18.0: 2.5, 20.0: 2.5,
    22.0: 2.5, 24.0: 3.0,
}

# ISO 7089 flat washer (outer Ø, inner Ø, thickness), mm, by nominal bolt size.
ISO_FLAT_WASHER = {
    3.0: {"od": 7.0, "id": 3.2, "t": 0.5}, 4.0: {"od": 9.0, "id": 4.3, "t": 0.8},
    5.0: {"od": 10.0, "id": 5.3, "t": 1.0}, 6.0: {"od": 12.0, "id": 6.4, "t": 1.6},
    8.0: {"od": 16.0, "id": 8.4, "t": 1.6}, 10.0: {"od": 20.0, "id": 10.5, "t": 2.0},
    12.0: {"od": 24.0, "id": 13.0, "t": 2.5}, 16.0: {"od": 30.0, "id": 17.0, "t": 3.0},
    20.0: {"od": 37.0, "id": 21.0, "t": 3.0},
}


def _num(text: str, *labels: str) -> float | None:
    """First number that appears immediately before any of the given labels."""
    for label in labels:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*" + label, text, re.I)
        if m:
            return float(m.group(1))
    return None


def _metric_size(prompt: str) -> tuple[float, float] | None:
    """(major_diameter, pitch) for a metric callout like 'M12' or 'M12x1.25'."""
    m = _METRIC.search(prompt or "")
    if not m:
        return None
    major = float(m.group(1))
    pm = _PITCH.search(prompt or "")
    pitch = float(pm.group(1)) if pm else (ISO_COARSE_PITCH.get(round(major, 3))
                                           or round(major * 0.15, 3))
    return major, pitch


_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "eight": 8}


def _set_screw_count(prompt: str) -> int:
    """How many set-screw holes the prompt asks for (word or digit before
    'set screw'), e.g. 'two M4 set screw holes' -> 2, 'four M5 set screws' -> 4."""
    t = prompt or ""
    if not _SET_SCREW_CTX.search(t):
        return 0
    m = re.search(r"\b(one|two|three|four|five|six|eight|\d+)\b[\sa-z0-9.x×-]*?"
                  r"set[- ]?screw", t, re.I)
    if not m:
        return 2  # set screws mentioned without a count — assume a pair
    tok = m.group(1).lower()
    return _NUMBER_WORDS.get(tok, int(tok) if tok.isdigit() else 2)


def _length(prompt: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:long|length|in length)\b", prompt or "", re.I)
    if m:
        return float(m.group(1))
    return _num(prompt or "", "long", "length")


# --- variant / family detectors --------------------------------------------
_NYLOC = re.compile(r"\b(nyloc|nylon[- ]?insert|nylon[- ]?lock|insert lock nut|"
                    r"lock\s*nut)\b", re.I)
_GT2 = re.compile(r"\bgt2\b|\btiming[- ]?belt\b|\btiming[- ]?pulley\b", re.I)
_GT2_CONTEXT = re.compile(r"\bgt2\b|\btiming\b|\bbelt\b", re.I)
_SQUARE_NUT = re.compile(r"\bsquare\s+nut\b", re.I)
_HEX_NUT = re.compile(r"\bhex(?:agon(?:al)?)?\s+nut\b", re.I)
_BARE_NUT = re.compile(r"\bnuts?\b", re.I)
_BOLT = re.compile(r"\bbolt\b", re.I)
_SCREW = re.compile(r"\bscrew\b", re.I)
_THREADED_ROD = re.compile(r"\bthreaded\s+rod\b|\bthreaded\s+stud\b|\bstud\b|\ball[- ]?thread\b", re.I)
_WASHER = re.compile(r"\bwasher\b", re.I)
_SHAFT_COUPLER = re.compile(r"\bshaft\s+coupler\b|\bshaft\s+coupling\b|\bcoupler\b|\bcoupling\b", re.I)
_PULLEY = re.compile(r"\bpulley\b", re.I)
_SET_SCREW_CTX = re.compile(r"\bset[- ]?screw\b", re.I)
# A container/assembly part: when present, a "screw"/"bolt"/"nut" mention is a
# FEATURE (screw boss, bolt hole, captive nut), not the part the user wants — so
# the fastener detectors must not hijack it.
_CONTAINER = re.compile(
    r"\benclosure\b|\bproject box\b|\bcase\b|\bhousing\b|\bbox\b|\bbracket\b|"
    r"\bplate\b|\bmount\b|\bchassis\b|\bpanel\b|\blid\b|\bcover\b", re.I)
# Fastener-as-feature phrases that must never trigger the standalone fastener
# families (e.g. "screw boss", "bolt circle", "captive nut", "nut pocket").
_FASTENER_FEATURE = re.compile(
    r"\bscrew\s+(?:boss|bosses|hole|holes|post|posts|terminal|pillar)\b|"
    r"\bbolt\s+(?:circle|hole|holes|pattern)\b|"
    r"\bnut\s+(?:pocket|trap|slot|seat|recess|catch)\b|\bcaptive\s+nut\b", re.I)


def detect_part_request(prompt: str) -> PartRequest | None:
    """Recognize a specialized mechanical part family/variant from the prompt.

    Returns a :class:`PartRequest` (buildable, or recognized-but-unsupported), or
    None when the prompt is not one of these special families (the normal pipeline
    then handles it). Ordering matters: variants and the GT2 pulley are checked
    before the families they would otherwise be mis-routed into."""
    t = prompt or ""

    # Guard: in a container/assembly prompt (enclosure, bracket, plate, …) a
    # "screw"/"bolt"/"nut" is a FEATURE, not the requested part — don't let the
    # standalone fastener families hijack it. (GT2 pulley / shaft coupler are
    # distinct parts and remain detectable.)
    container = bool(_CONTAINER.search(t))
    feature_only = bool(_FASTENER_FEATURE.search(t))

    # 1) NYLOC / lock nut — a variant of the hex nut. Recognized but the nylon
    #    insert mechanics are not modeled, so this is an unsupported VARIANT (never
    #    silently a plain hex nut).
    if (_NYLOC.search(t) and not container and not feature_only
            and (_BARE_NUT.search(t) or "nut" in t.lower())):
        size = _metric_size(t)
        return PartRequest(
            requested_family="hex_nut", requested_variant="nyloc",
            support="unsupported_variant", object_type="hex_nut",
            params={"metric": size} if size else {},
            unsupported_features=["nylon insert / locking collar"],
            note="Nylon-insert lock nut variant is not implemented.")

    # 2) GT2 / timing pulley — must be caught BEFORE the generic gear/pulley route
    #    so it never becomes a spur gear.
    if _PULLEY.search(t) and _GT2_CONTEXT.search(t):
        teeth = _num(t, "teeth", "tooth", "-tooth", "t ")
        bore = _num(t, "bore", "shaft hole", "inner diameter", "id")
        belt = _num(t, "belt width", "belt") or _num(t, "wide")
        missing = []
        if teeth is None:
            teeth = 20.0
            missing.append("tooth count (assumed 20)")
        return PartRequest(
            requested_family="timing_pulley_gt2", support="buildable",
            object_type="timing_pulley_gt2",
            params={"teeth": teeth, "bore_diameter": bore or 5.0,
                    "belt_width": belt or 6.0},
            missing=missing)

    # 3) Shaft coupler.
    if _SHAFT_COUPLER.search(t):
        size = _metric_size(t)  # set-screw size, if any (e.g. "M4 set screws")
        bores = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*mm\s*bore", t, re.I)]
        b1 = bores[0] if len(bores) >= 1 else _num(t, "bore") or 6.0
        b2 = bores[1] if len(bores) >= 2 else b1
        od = _num(t, "outer diameter", "outside diameter", "od", "diameter") or 20.0
        length = _length(t) or 25.0
        ss_count = _set_screw_count(t)
        ss_d = (size[0] if size else 4.0)
        return PartRequest(
            requested_family="shaft_coupler", support="buildable",
            object_type="shaft_coupler",
            params={"length": length, "outer_diameter": od, "bore_1": b1,
                    "bore_2": b2, "set_screw_diameter": ss_d,
                    "set_screw_count": ss_count},
            unsupported_features=(["modeled set-screw threads (cosmetic radial holes)"]
                                  if ss_count else []))

    # 4) Threaded rod / stud.
    if _THREADED_ROD.search(t) and not container:
        size = _metric_size(t)
        if size is None:
            return PartRequest(
                requested_family="threaded_rod", support="unsupported_family",
                missing=["nominal thread size (e.g. M12)"],
                note="Threaded rod needs a thread size.")
        length = _length(t)
        missing = []
        if length is None:
            length = 30.0
            missing.append("length (assumed 30mm)")
        return PartRequest(
            requested_family="threaded_rod", support="buildable",
            object_type="threaded_rod",
            params={"thread_major_diameter": size[0], "thread_pitch": size[1],
                    "length": length}, missing=missing)

    # 5) Square nut.
    if _SQUARE_NUT.search(t) and not (container or feature_only):
        size = _metric_size(t)
        if size is None:
            return PartRequest(
                requested_family="square_nut", support="unsupported_family",
                missing=["nominal thread size (e.g. M12)"])
        from app.cad.templates.square_nut import square_nut_dims

        d = square_nut_dims(size[0])
        return PartRequest(
            requested_family="square_nut", support="buildable",
            object_type="square_nut",
            params={"width": d["s"], "height": d["m"],
                    "thread_major_diameter": size[0], "thread_pitch": size[1]})

    # 6) Bolt (hex-head by default).
    if _BOLT.search(t) and not (container or feature_only):
        size = _metric_size(t)
        if size is None:
            return PartRequest(
                requested_family="bolt", support="unsupported_family",
                missing=["nominal thread size (e.g. M12)"])
        from app.cad.templates.bolt import hex_head_dims

        head = hex_head_dims(size[0])
        length = _length(t)
        threaded_length = _num(t, "mm threaded length", "threaded length",
                               "thread length", "of thread", "threaded")
        missing = []
        if length is None:
            # Assume a short, fully-threaded bolt so the thread is actually modeled
            # and visible (long bolts fall back to cosmetic). Documented as REVIEW.
            length = 20.0
            missing.append(f"shank length (assumed {length:g}mm, fully threaded, hex head)")
        else:
            missing.append("hex head type (assumed ISO hex head)")
        if threaded_length is None:
            threaded_length = length  # fully threaded
        else:
            threaded_length = min(threaded_length, length)
        return PartRequest(
            requested_family="bolt", support="buildable", object_type="bolt",
            params={"thread_major_diameter": size[0], "thread_pitch": size[1],
                    "length": length, "threaded_length": threaded_length,
                    "head_across_flats": head["s"], "head_height": head["k"]},
            missing=missing)

    # 7) Washer — a flat annular disc (no thread). Built on the disc/spacer body.
    if _WASHER.search(t) and not (container or feature_only):
        od = _num(t, "outer diameter", "outside diameter", "od", "o.d.")
        bore = _num(t, "inner diameter", "inside diameter", "bore", "id", "i.d.")
        thick = _num(t, "thick", "thickness")
        size = _metric_size(t)
        if size and (od is None or bore is None or thick is None):
            w = ISO_FLAT_WASHER.get(round(size[0], 3))
            if w:
                od = od or w["od"]
                bore = bore or w["id"]
                thick = thick or w["t"]
        if od and bore and thick and bore < od:
            return PartRequest(
                requested_family="washer", support="buildable",
                object_type="spacer",
                params={"outer_diameter": od, "bore_diameter": bore, "length": thick})
        # Not enough info to dimension a washer — let the normal pipeline handle it.
        return None

    # 8) A standalone screw (not a set screw / screw-boss mention) — needs head + length.
    if (_SCREW.search(t) and not _SET_SCREW_CTX.search(t)
            and not container and not feature_only):
        return PartRequest(
            requested_family="screw", support="unsupported_family",
            missing=["head type (e.g. socket/pan/flat) and length"],
            note="Screw family needs head type and length; not implemented yet.")

    return None


def honesty_status(request: PartRequest | None, *, modeled_ok: bool = True) -> str:
    """Resolve the honesty status for a buildable request from what we had to
    assume. (Unsupported requests are handled by the router, not here.)"""
    if request is None:
        return HONESTY_EXACT
    if request.support in ("unsupported_family", "unsupported_variant"):
        return HONESTY_UNSUPPORTED
    if request.missing or request.unsupported_features or not modeled_ok:
        return HONESTY_PARTIAL
    return HONESTY_EXACT


def build_contract(*, requested_family: str | None, resolved_family: str | None,
                   requested_variant: str | None = None,
                   resolved_variant: str | None = None,
                   standard_part: bool = False, standard: str | None = None,
                   unsupported_features: list[str] | None = None,
                   substituted_features: list[str] | None = None,
                   honesty: str = HONESTY_EXACT,
                   missing: list[str] | None = None,
                   reason: str | None = None) -> dict:
    """Assemble the part-family contract block stored on semantic_json."""
    return {
        "requested_family": requested_family,
        "resolved_family": resolved_family,
        "requested_variant": requested_variant,
        "resolved_variant": resolved_variant,
        "standard_part": bool(standard_part),
        "standard": standard,
        "unsupported_features": list(unsupported_features or []),
        "substituted_features": list(substituted_features or []),
        "missing_inputs": list(missing or []),
        "generation_honesty_status": honesty,
        "reason": reason,
    }
