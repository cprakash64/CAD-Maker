"""Object resolver — prompt → MechanicalObjectSpec + a buildable CAD family.

Resolution order (local-first, never a generic box for a known object):
  1. curated local board presets (Raspberry Pi, Arduino, ESP32, Jetson) → enclosure
  2. curated local standards (NEMA stepper faces → motor mount; ball bearings →
     bearing holder)
  3. user-provided dimensions (custom PCB/box) → fitted enclosure
  4. trusted source search (bounded, cached, offline-safe) → extracted spec
  5. otherwise: a named-but-unknown device asks for dimensions (never a fake PASS)

Each resolution records its dimension trust level (``source_type``) so the
validation layer can decide PASS / REVIEW / CONCEPT / clarify.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.cad.device_presets import (
    detect_device_preset,
    object_type_for_preset,
)
from app.cad.object_intelligence import cache, source_search
from app.cad.object_intelligence.confidence import status_ceiling
from app.cad.object_intelligence.mechanical_spec import (
    CAT_BEARING,
    CAT_GENERIC,
    CAT_MCU,
    CAT_MOTOR,
    CAT_SBC,
    FAM_BEARING_HOLDER,
    FAM_BOARD_ENCLOSURE,
    FAM_DEVICE_ENCLOSURE,
    FAM_GENERIC_FITTED_BOX,
    FAM_MOTOR_MOUNT,
    SOURCE_LOCAL_VERIFIED,
    SOURCE_UNKNOWN,
    SOURCE_USER,
    MechanicalObjectSpec,
)
from app.cad.object_intelligence.standards import bearing, nema_face

# Enclosure/holder/mount intent words (so "Raspberry Pi" alone isn't hijacked).
# Typo-tolerant: "enclouser"/"encloser" are common misspellings of "enclosure".
_ENCL = r"enclosure|enclouser|encloser|enclosur|case|box|housing|shell"
_ENCLOSURE_WORDS = re.compile(rf"\b({_ENCL})\b", re.I)
_MOUNT_WORDS = re.compile(r"\b(mount|bracket|holder|plate|stand)\b", re.I)
_DEVICE_PHRASE = re.compile(
    rf"\b(?:{_ENCL}|mount|bracket|holder|bezel|stand)\s+for\s+(?:an?\s+|my\s+|the\s+)?([a-z0-9][\w .\-]{{1,40}})",
    re.I)
# Brand / family tokens that mark a SPECIFIC product we should not generic-box.
_BRAND = re.compile(
    r"\b(waveshare|adafruit|sparkfun|seeed|jetson|nvidia|orange\s*pi|banana\s*pi|"
    r"beaglebone|teensy|stm32|pico|odroid|coral|lattepanda)\b", re.I)
_NEMA = re.compile(r"\bnema\s*-?\s*(\d{1,2})\b", re.I)
_BEARING = re.compile(r"\b(608|623|625|626|688|6000|6001|6002|6200|6201)\b")
_DIMS_2D = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:by|x|×|\*)\s*(\d+(?:\.\d+)?)\s*mm", re.I)
_HOLE_COUNT = re.compile(r"\b(\d+|two|three|four|six|eight)\b\s*(?:[\d.]+\s*mm\s*)?(?:mounting|screw)?\s*holes?", re.I)
_HOLE_DIA = re.compile(r"(\d+(?:\.\d+)?)\s*mm\s*(?:mounting|screw)?\s*holes?", re.I)
_WORD_NUM = {"two": 2, "three": 3, "four": 4, "six": 6, "eight": 8}


@dataclass
class ObjectResolution:
    spec: MechanicalObjectSpec
    object_type: str | None              # registry object_type to build (None = clarify)
    dimensions: dict = field(default_factory=dict)
    preset_id: str | None = None
    status_ceiling: str = "review"       # best achievable validation status
    clarify: bool = False
    clarification: str | None = None
    requested_features: list = field(default_factory=list)


def _wall_from_prompt(prompt: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:wall|walls)", prompt, re.I)
    return float(m.group(1)) if m else None


def _wants_logo(prompt: str) -> bool:
    t = prompt.lower()
    return "logo" in t or "emboss" in t


def _resolve_board(prompt: str) -> ObjectResolution | None:
    preset = detect_device_preset(prompt)
    if preset is None:
        return None
    object_type = object_type_for_preset(preset.id)
    approximate = (any(getattr(c, "approximate", False) for c in preset.connectors)
                   or any(getattr(s, "approximate", True) for s in preset.cable_slots)
                   or preset.header is not None)
    is_pi = preset.id.startswith("raspberry")
    category = CAT_SBC if (is_pi or "jetson" in preset.id) else CAT_MCU
    spec = MechanicalObjectSpec(
        object_name=preset.display_name, normalized_name=preset.id, category=category,
        manufacturer=preset.display_name.split()[0], model=preset.display_name,
        source_urls=[preset.source], source_type=SOURCE_LOCAL_VERIFIED,
        confidence_score=0.92,
        board_outline={"length_mm": preset.board.length_mm, "width_mm": preset.board.width_mm},
        mounting_holes=[{"x": m.x_mm, "y": m.y_mm, "d": m.diameter_mm} for m in preset.mounting_holes],
        connector_cutouts=[{"name": c.name, "side": c.side} for c in preset.connectors],
        standards=[], generated_family=(FAM_DEVICE_ENCLOSURE if is_pi else FAM_BOARD_ENCLOSURE),
        validation_requirements=["mounting_posts>=1", "required_ports_through_hole"],
        assumptions=(["Some connector positions are to a tolerance — REVIEW the fit."]
                     if approximate else []))
    dims: dict[str, float] = {"wall_thickness": _wall_from_prompt(prompt)
                              or preset.enclosure.wall_thickness_mm}
    if _wants_logo(prompt):
        dims["logo"] = 1.0
    # local_verified can PASS; approximate connectors lower it to REVIEW.
    ceiling = "review" if approximate else status_ceiling(SOURCE_LOCAL_VERIFIED, 0.92)
    return ObjectResolution(spec=spec, object_type=object_type, dimensions=dims,
                            preset_id=(None if object_type != "board_enclosure" else preset.id),
                            status_ceiling=ceiling)


def _resolve_nema(prompt: str) -> ObjectResolution | None:
    m = _NEMA.search(prompt)
    if not m or not _MOUNT_WORDS.search(prompt):
        return None
    face = nema_face(int(m.group(1)))
    if face is None:
        return None
    spec = MechanicalObjectSpec(
        object_name=f"NEMA {face.nema} motor mount", normalized_name=f"nema{face.nema}_mount",
        category=CAT_MOTOR, model=f"NEMA {face.nema}", source_type=SOURCE_LOCAL_VERIFIED,
        confidence_score=0.95,
        hole_pattern={"type": "square", "spacing_mm": face.bolt_spacing_mm,
                      "hole_mm": face.bolt_hole_mm, "count": 4},
        standards=[f"NEMA {face.nema} ({face.bolt_spacing_mm:g}mm square, {face.bolt_size})"],
        generated_family=FAM_MOTOR_MOUNT,
        validation_requirements=["four_hole_pattern", "centre_pilot_bore"],
        assumptions=[f"NEMA {face.nema} face: {face.bolt_spacing_mm:g}mm bolt circle, "
                     f"{face.bolt_size} screws, Ø{face.pilot_diameter_mm:g}mm pilot."])
    dims = {"nema_size": float(face.nema), "bolt_spacing": face.bolt_spacing_mm,
            "bolt_hole": face.bolt_hole_mm, "pilot_diameter": face.pilot_diameter_mm + 0.5,
            "plate_size": round(face.body_mm + 8.0, 1), "thickness": 6.0}
    return ObjectResolution(spec=spec, object_type="motor_mount", dimensions=dims,
                            status_ceiling=status_ceiling(SOURCE_LOCAL_VERIFIED, 0.95))


def _resolve_bearing(prompt: str) -> ObjectResolution | None:
    m = _BEARING.search(prompt)
    if not m or "bearing" not in prompt.lower():
        return None
    b = bearing(m.group(1))
    if b is None:
        return None
    press = "press" in prompt.lower()
    clr = -0.02 if press else 0.05    # press (interference) vs slip fit
    fit = "press-fit (interference)" if press else "slip-fit"
    spec = MechanicalObjectSpec(
        object_name=f"{b.name} bearing holder", normalized_name=f"bearing_{b.name}_holder",
        category=CAT_BEARING, model=f"{b.name} bearing", source_type=SOURCE_LOCAL_VERIFIED,
        confidence_score=0.95,
        dimensions={"bore_mm": b.bore_mm, "outer_mm": b.outer_mm, "width_mm": b.width_mm},
        standards=[f"{b.name} deep-groove ball bearing (Ø{b.outer_mm:g}×{b.width_mm:g}, "
                   f"bore Ø{b.bore_mm:g})"],
        generated_family=FAM_BEARING_HOLDER,
        validation_requirements=["bore_matches_bearing_od", "retention_lip"],
        assumptions=[f"{b.name} bearing: OD Ø{b.outer_mm:g}mm, bore Ø{b.bore_mm:g}mm, "
                     f"width {b.width_mm:g}mm.",
                     f"Seat is {fit} ({clr:+.2f}mm on the OD) — verify for your tolerance."])
    dims = {"bearing_outer": b.outer_mm, "bearing_bore": b.bore_mm,
            "bearing_width": b.width_mm, "fit_clearance": clr, "lip": 1.5,
            "wall": 4.0, "thickness": round(b.width_mm + 4.0, 1)}
    return ObjectResolution(spec=spec, object_type="bearing_holder", dimensions=dims,
                            status_ceiling=status_ceiling(SOURCE_LOCAL_VERIFIED, 0.95))


_DIMS_3D = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:by|x|×|\*)\s*(\d+(?:\.\d+)?)\s*(?:mm)?\s*"
    r"(?:by|x|×|\*)\s*(\d+(?:\.\d+)?)\s*mm", re.I)


def _resolve_user_pcb(prompt: str) -> ObjectResolution | None:
    from app.cad.object_intelligence.features import (
        FEATURE_TO_PORT,
        parse_requested_features,
    )

    t = prompt.lower()
    if not (_ENCLOSURE_WORDS.search(t) and ("pcb" in t or "board" in t)):
        return None
    d3 = _DIMS_3D.search(prompt)
    d2 = _DIMS_2D.search(prompt)
    if d3:
        length, width, height = (float(d3.group(1)), float(d3.group(2)), float(d3.group(3)))
    elif d2:
        length, width, height = float(d2.group(1)), float(d2.group(2)), 30.0
    else:
        return None
    hc = _HOLE_COUNT.search(prompt)
    count = 0
    if hc:
        g = hc.group(1).lower()
        count = _WORD_NUM.get(g, int(g) if g.isdigit() else 0)
    hd = _HOLE_DIA.search(prompt)
    hole = float(hd.group(1)) if hd else (3.0 if count else 0.0)

    requested = parse_requested_features(prompt)
    assumptions = [f"User-provided enclosure for a {length:g}×{width:g}×{height:g}mm board."]
    if count:
        assumptions.append(f"{count}× Ø{hole:g}mm mounting holes on corner posts.")
    spec = MechanicalObjectSpec(
        object_name=f"custom PCB enclosure {length:g}×{width:g}×{height:g}mm",
        normalized_name="custom_pcb_enclosure", category=CAT_GENERIC,
        source_type=SOURCE_USER, confidence_score=1.0,
        board_outline={"length_mm": length, "width_mm": width, "height_mm": height},
        mounting_holes=[{"d": hole}] * count,
        connector_cutouts=[{"name": FEATURE_TO_PORT[f]} for f in requested
                           if f in FEATURE_TO_PORT],
        generated_family=FAM_GENERIC_FITTED_BOX,
        validation_requirements=(["mounting_posts>=1"] if count else []),
        assumptions=assumptions)
    dims = {"board_length": length, "board_width": width, "board_height": height,
            "wall_thickness": _wall_from_prompt(prompt) or 2.5,
            "mount_hole": hole, "mount_count": float(min(count, 4))}
    if _wants_logo(prompt):
        dims["logo"] = 1.0
    # Port-cutout flags drive the builder's true through-wall openings.
    for f in requested:
        if f in FEATURE_TO_PORT:
            dims[f"cut_{FEATURE_TO_PORT[f]}"] = 1.0
    return ObjectResolution(spec=spec, object_type="generic_fitted_box", dimensions=dims,
                            status_ceiling=status_ceiling(SOURCE_USER, 1.0),
                            requested_features=requested)


_PHONE_HOLDER = re.compile(r"\bphone\s*(?:holder|stand|cradle|dock|mount)\b", re.I)


def _resolve_phone_holder(prompt: str) -> ObjectResolution | None:
    from app.cad.object_intelligence.mechanical_spec import (
        CAT_HOLDER,
        FAM_PHONE_HOLDER,
    )
    from app.cad.object_intelligence.phones import detect_phone

    t = prompt.lower()
    if not (_PHONE_HOLDER.search(t) or ("phone" in t and _MOUNT_WORDS.search(t))):
        return None
    phone = detect_phone(prompt)
    if phone is None:
        # A phone holder with no recognized model — ask for the model/dimensions
        # rather than guess a fit.
        spec = MechanicalObjectSpec(
            object_name="phone holder", category=CAT_HOLDER, source_type=SOURCE_UNKNOWN,
            generated_family=FAM_PHONE_HOLDER, unsupported_features=["phone dimensions"],
            assumptions=["No phone model recognized."])
        return ObjectResolution(
            spec=spec, object_type=None, status_ceiling="clarify", clarify=True,
            clarification=("Which phone is this holder for? Tell me the model (e.g. "
                           "iPhone 15) or its width × height × thickness in mm and I'll "
                           "fit the cradle."))
    spec = MechanicalObjectSpec(
        object_name=f"{phone.display_name} holder",
        normalized_name=f"{phone.id}_holder", category=CAT_HOLDER,
        manufacturer="Apple" if "iphone" in phone.id else None, model=phone.display_name,
        source_urls=[phone.source], source_type=SOURCE_LOCAL_VERIFIED, confidence_score=0.9,
        dimensions={"phone_width_mm": phone.width_mm, "phone_height_mm": phone.length_mm,
                    "phone_depth_mm": phone.depth_mm},
        generated_family=FAM_PHONE_HOLDER,
        validation_requirements=["cradle", "back_support", "bottom_lip", "cable_notch"],
        assumptions=[f"{phone.display_name}: {phone.length_mm:g}×{phone.width_mm:g}×"
                     f"{phone.depth_mm:g}mm (official).",
                     "1.5mm fit clearance assumed — increase for a cased phone.",
                     f"Charging cable notch sized for {phone.charging_port.replace('_', '-')}."])
    dims = {"phone_width": phone.width_mm, "phone_depth": phone.depth_mm,
            "phone_length": phone.length_mm, "fit_clearance": 1.5, "lean_deg": 15.0,
            "wall": 4.0}
    # Source-backed official dimensions but real-world fit varies → REVIEW.
    return ObjectResolution(spec=spec, object_type="phone_holder", dimensions=dims,
                            status_ceiling="review",
                            requested_features=["cradle", "back_support", "bottom_lip",
                                                "cable_notch"])


def _resolve_unknown_named(prompt: str) -> ObjectResolution | None:
    """A SPECIFIC named device (brand/model) we have no local preset for, and no
    source/dimensions: ask for dimensions rather than silently generic-boxing it."""
    phrase = _DEVICE_PHRASE.search(prompt)
    if not phrase:
        return None
    name = phrase.group(1).strip()
    # Fire only for a SPECIFIC named product we can't resolve: a known brand
    # ("Waveshare 7 inch display"), a model code ("SuperBoard X9000", "RK3588"), or
    # a CamelCase product name. A generic mechanical description with a bare
    # dimension ("bearing housing for a 20mm shaft") is NOT a known-object request —
    # it belongs to the normal CAD pipeline, so we return None there.
    # A model code: letters glued to a 3+ digit number ("X9000"), or a capitalized
    # word followed by a 3+ digit number ("Frobnitz 3000", "Orange Pi 5000").
    model_code = re.search(
        r"\b[A-Za-z]{1,6}\d{3,}\b|\b\d{3,}[A-Za-z]{1,4}\b"
        r"|\b[A-Za-z]{2,}\s*-?\s*\d{3,}\b", name)
    camelcase = re.search(r"\b[A-Z][a-z]+[A-Z][A-Za-z]+\b", name)
    inch_part = re.search(r"\b\d+(?:\.\d+)?\s*(?:inch|in|\")\b", prompt, re.I)
    if not (_BRAND.search(prompt) or model_code or camelcase or inch_part):
        return None
    spec = MechanicalObjectSpec(
        object_name=name, normalized_name=re.sub(r"\W+", "_", name.lower()).strip("_"),
        category=CAT_GENERIC, source_type=SOURCE_UNKNOWN, confidence_score=0.0,
        generated_family=FAM_GENERIC_FITTED_BOX,
        assumptions=[f"No local preset or verified source for '{name}'."],
        unsupported_features=["verified dimensions"],
        validation_requirements=["dimensions_required"])
    return ObjectResolution(
        spec=spec, object_type=None, status_ceiling="clarify", clarify=True,
        clarification=(f"I don't have a verified preset or datasheet for '{name}'. "
                       "Give me its outline size (length × width × height) and any "
                       "mounting-hole pattern and I'll build an accurate fitted "
                       "enclosure, or say 'concept' for a rough estimated box."))


def resolve_object(prompt: str) -> ObjectResolution | None:
    """Resolve a prompt to a buildable object family, or None if it isn't an
    Object-Intelligence request (the normal pipeline then handles it)."""
    if not prompt:
        return None
    for resolver in (_resolve_board, _resolve_nema, _resolve_bearing,
                     _resolve_phone_holder, _resolve_user_pcb):
        res = resolver(prompt)
        if res is not None:
            return res
    # A specific named product with no local preset: try a bounded, CACHE-FIRST,
    # source-backed extraction before giving up. Offline/unconfigured → None, so we
    # fall to an honest clarify rather than a generic box.
    unknown = _resolve_unknown_named(prompt)
    if unknown is None:
        return None
    from app.cad.object_intelligence.extraction_pipeline import extract_object_spec

    spec = extract_object_spec(unknown.spec.object_name)
    if spec is not None:
        return _resolution_from_extracted_spec(spec)
    return unknown   # honest clarify (no verified dimensions)


def _resolution_from_extracted_spec(spec: MechanicalObjectSpec) -> ObjectResolution:
    """Turn a source/cache-extracted spec into a buildable resolution. A board-like
    spec (with an outline) builds a fitted enclosure; trust + confidence cap the
    verdict (web → REVIEW; official ≥0.85 → may PASS; never PASS for gpt/unknown)."""
    bo = spec.board_outline or {}
    dims = {
        "board_length": float(bo.get("length_mm", 80.0)),
        "board_width": float(bo.get("width_mm", 50.0)),
        "board_height": float(bo.get("height_mm", 20.0)),
        "wall_thickness": 2.5,
        "mount_count": float(min(len(spec.mounting_holes), 4)),
        "mount_hole": float((spec.mounting_holes[0].get("d", 3.0))
                            if spec.mounting_holes else 3.0),
    }
    if not spec.generated_family:
        spec.generated_family = FAM_GENERIC_FITTED_BOX
    return ObjectResolution(
        spec=spec, object_type="generic_fitted_box", dimensions=dims,
        status_ceiling=status_ceiling(spec.source_type, spec.confidence_score))
