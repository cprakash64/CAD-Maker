"""Deterministic, offline 'LLM' that parses common part prompts with heuristics.

This keeps local dev and the test suite fully functional with no API key and no
cost, while exercising the exact same downstream validation + generation path a
real provider would. It is intentionally rule-based and conservative.
"""
from __future__ import annotations

import re

from app.llm.base import LLMProvider

_SCREW_CLEARANCE = {"M3": 3.4, "M4": 4.5, "M5": 5.5, "M6": 6.6, "M8": 9.0}
_WORD_NUM = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "eight": 8,
}

_TYPE_KEYWORDS = [
    ("inline_4_crankshaft", ("crankshaft", "crank shaft", "inline-4", "inline 4",
                             "4-cylinder", "four cylinder", "four-cylinder")),
    ("flanged_pipe_branch", ("flanged pipe", "pipe branch", "pipe tee", "pipe spool",
                             "pipe flange", "flanged tee", "branch pipe", "pipe fitting")),
    ("simple_gear_or_pulley", ("gear", "pulley", "sprocket", "cog wheel", "timing pulley")),
    ("l_bracket", ("l-bracket", "l bracket", "angle bracket", "right angle")),
    ("pipe_clamp", ("pipe clamp", "pipe", "hose clamp", "tube clamp", "saddle")),
    ("enclosure", ("enclosure", "box", "project box", "electronics box", "case",
                   "housing", "lid")),
    ("spacer", ("spacer", "standoff", "stand-off", "bushing")),
    ("drill_jig", ("drill jig", "jig plate", "drilling template", "drill guide")),
    ("handle", ("handle", "knob", "grip")),
    ("adapter_plate", ("adapter plate", "adapter", "transition plate", "flange")),
    ("rectangular_bracket", ("bracket", "mounting plate", "plate", "mount")),
]


def _find_type(text: str) -> str:
    # Word-boundary match so short keywords ("l bracket") don't accidentally
    # match inside other words ("wal-l bracket").
    for object_type, keywords in _TYPE_KEYWORDS:
        for k in keywords:
            if re.search(r"\b" + re.escape(k) + r"\b", text):
                return object_type
    return "rectangular_bracket"


def _find_units(text: str) -> str:
    if re.search(r"\b(inch|inches|\")\b", text):
        return "inch"
    if re.search(r"\bcm\b|centimet", text):
        return "cm"
    return "mm"


def _find_method(text: str) -> str:
    if "cnc" in text or "mill" in text or "aluminum" in text or "aluminium" in text:
        return "cnc_milling"
    if "laser" in text:
        return "laser_cut"
    if "sheet metal" in text or "bent" in text:
        return "sheet_metal"
    if "resin" in text or "sla" in text:
        return "sla_3d_print"
    return "fdm_3d_print"


def _num_before(text: str, *labels: str) -> float | None:
    for label in labels:
        m = re.search(
            r"(-?\d+(?:\.\d+)?)\s*(?:mm|cm|inch|inches|\")?\s*" + label, text
        )
        if m:
            return float(m.group(1))
    return None


def _num_after(text: str, *labels: str) -> float | None:
    """Find a number that FOLLOWS a label, e.g. 'spaced 25mm', 'every 25 mm'."""
    for label in labels:
        m = re.search(
            label + r"\s*(?:at|by|=|:)?\s*(-?\d+(?:\.\d+)?)\s*(?:mm|cm|inch|inches|\")?",
            text,
        )
        if m:
            return float(m.group(1))
    return None


def _dims_pair(text: str) -> tuple[float, float] | None:
    """Parse a plan-dimension pair like '120mm by 80mm', '120 x 80', '120×80'."""
    m = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(?:mm|cm|inch|inches|\")?\s*(?:by|x|×)\s*"
        r"(-?\d+(?:\.\d+)?)\s*(?:mm|cm|inch|inches|\")?",
        text,
    )
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _all_dims(text: str) -> list[float]:
    return [
        float(x)
        for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:mm|cm|inch|inches|\")", text)
    ]


def _screw_holes(text: str) -> tuple[list[float], list[str]]:
    """Return (clearance diameters, assumption notes) from screw callouts."""
    diameters: list[float] = []
    notes: list[str] = []
    count_word = "|".join(_WORD_NUM)
    pattern = rf"(\d+|{count_word})?\s*x?\s*(M\d+)\b"
    for m in re.finditer(pattern, text, re.IGNORECASE):
        raw = m.group(1)
        if raw is None:
            count = 1
        elif raw.isdigit():
            count = int(raw)
        else:
            count = _WORD_NUM.get(raw.lower(), 1)
        size = m.group(2).upper()
        clearance = _SCREW_CLEARANCE.get(size)
        if clearance:
            diameters.extend([clearance] * count)
            notes.append(f"{size} -> {clearance}mm clearance hole")
    return diameters, notes


def _hole_finish(text: str) -> str:
    """Detect requested hole finish from screw-head language."""
    if re.search(r"counterbore|c'?bore|socket head|cap screw|recessed", text):
        return "counterbore"
    if re.search(r"countersink|countersunk|csk|flat head|flush", text):
        return "countersink"
    return "simple"


class MockLLMProvider(LLMProvider):
    name = "mock"

    def parse_prompt(self, prompt: str) -> dict:
        text = prompt.lower()
        object_type = _find_type(text)
        units = _find_units(text)
        method = _find_method(text)
        assumptions: list[str] = []
        missing: list[str] = []
        clarification: str | None = None

        dims: dict[str, float] = {}
        holes: list[dict] = []
        fillet = None

        width = _num_before(text, "wide", "width") or _num_before(text, "mm wide")
        thickness = _num_before(text, "thick", "thickness")
        height = _num_before(text, "tall", "high", "height")
        depth = _num_before(text, "deep", "depth", "long", "length")
        diameter = _num_before(text, "diameter", "dia", "od")
        wall = _num_before(text, "wall thickness", "walls", "wall")
        square = _num_before(text, "square")
        bore = _num_before(text, "center bore", "center hole", "bore")
        if square is not None:  # "100mm square" sets both plan dimensions
            width = width or square
            depth = depth or square
        # "120mm by 80mm" / "120 x 80" -> a plan-dimension pair.
        pair = _dims_pair(text)
        if pair:
            width = width or pair[0]
            depth = depth or pair[1]
        pipe = _num_before(text, "pipe", "tube") or (
            diameter if object_type == "pipe_clamp" else None
        )

        screw_dias, screw_notes = _screw_holes(prompt)
        assumptions.extend(screw_notes)
        finish = _hole_finish(text)
        if finish != "simple":
            assumptions.append(f"Holes finished as {finish}")

        if object_type == "rectangular_bracket":
            dims = {
                "width": width or 80.0,
                "depth": depth or 40.0,
                "thickness": thickness or 5.0,
            }
            if "gusset" in text:
                dims["gusset_height"] = round(dims["depth"] * 0.5, 1)
                assumptions.append("Added a stiffening gusset rib")
            holes = _layout_holes(screw_dias, dims["width"], dims["depth"], finish)
        elif object_type == "l_bracket":
            dims = {
                "length": depth or 60.0,
                "width": width or 40.0,
                "height": height or 60.0,
                "thickness": thickness or 5.0,
                "hole_diameter": screw_dias[0] if screw_dias else 6.0,
            }
        elif object_type == "enclosure":
            wt = wall or thickness or 2.5
            dims = {
                "width": width or 80.0,
                "depth": depth or 60.0,
                "height": height or 40.0,
                "wall_thickness": wt,
                "lid_thickness": wt,
            }
        elif object_type == "spacer":
            dims = {
                "outer_diameter": diameter or 12.0,
                "length": height or depth or 20.0,
                "bore_diameter": (screw_dias[0] if screw_dias else 6.4),
            }
        elif object_type == "pipe_clamp":
            if not pipe:
                missing.append("pipe_diameter")
                clarification = "What is the outer diameter of the pipe to clamp?"
            dims = {
                "pipe_diameter": pipe or 25.0,
                "width": width or 25.0,
                "thickness": thickness or 6.0,
                "ear_width": 18.0,
                "hole_diameter": screw_dias[0] if screw_dias else 6.0,
            }
        elif object_type == "drill_jig":
            # "120mm by 80mm" -> length x width; "6mm guide holes"; "spaced 25mm".
            guide = _num_before(text, "guide holes", "guide hole", "guide", "holes", "hole")
            spacing = (
                _num_before(text, "spacing", "apart", "pitch")
                or _num_after(text, "spaced", "spacing", "pitch", "every")
            )
            dims = {
                "length": (pair[0] if pair else None) or depth or width or 100.0,
                "width": (pair[1] if pair else None) or 60.0,
                "thickness": thickness or 6.0,
                "hole_diameter": (screw_dias[0] if screw_dias else guide) or 5.0,
                "hole_spacing": spacing or 20.0,
            }
            if "lip" in text or "registration" in text:
                dims["lip_height"] = _num_before(text, "lip") or 10.0
                assumptions.append("Added a registration lip (default 10mm)")
            assumptions.append("Default guide-hole count/pattern centered on the plate")
        elif object_type == "handle":
            dims = {
                "diameter": diameter or 30.0,
                "height": height or 25.0,
                "bore_diameter": (screw_dias[0] if screw_dias else 8.0),
                "bore_depth": 12.0,
            }
        elif object_type == "inline_4_crankshaft":
            # Many engineered parameters; defaults define a realistic inline-4.
            # Honor an explicit total length if the user gave one.
            total = _num_before(text, "long", "length", "total length")
            dims = {"total_length_mm": total} if total else {}
            assumptions.append(
                "Generated a standard inline-4 crankshaft (5 mains, 4 rod journals, "
                "counterweighted webs); adjust parameters as needed"
            )
        elif object_type == "flanged_pipe_branch":
            dims = {}
            md = _num_before(text, "main pipe", "pipe") or diameter
            if md:
                dims["main_pipe_outer_diameter_mm"] = md
            bc = _num_before(text, "bolt", "bolts")
            if bc:
                dims["bolt_count"] = bc
            assumptions.append(
                "Generated a simplified flanged pipe branch (defaults for missing dims)"
            )
        elif object_type == "simple_gear_or_pulley":
            teeth = _num_before(text, "teeth", "tooth", "-tooth")
            # Shaft / bore diameter (e.g. "10mm shaft", "8mm bore", "12mm center bore").
            shaft = _num_before(
                text, "center bore", "centre bore", "shaft hole", "shaft", "bore"
            ) or _num_after(text, "center bore", "shaft", "bore")
            outer = diameter or _num_before(
                text, "outer diameter", "outside diameter", "od", "tip diameter")
            module = _num_before(text, "module", "mod ") or _num_after(text, "module", "mod")
            pitch = _num_before(text, "pitch diameter", "pitch dia", "pitch")
            is_gear = "gear" in text or "sprocket" in text or bool(teeth)
            is_hex = "hex" in text or "hexagon" in text
            if is_hex:
                dims = {
                    "outer_diameter_mm": outer or 60.0,
                    "thickness_mm": thickness or height or 12.0,
                    "bore_diameter_mm": shaft or 10.0,
                    "hex": 1.0,
                }
                assumptions.append("Interpreted as a hexagonal outer profile (gear blank)")
            elif is_gear:
                # Resolve a coherent module-based spur gear (default module 2, 24
                # teeth) so the OD, module and tooth count always agree.
                from app.cad.templates.gear_pulley import (
                    DEFAULT_TOOTH_COUNT,
                    resolve_gear_geometry,
                )

                z = int(teeth) if teeth else DEFAULT_TOOTH_COUNT
                g = resolve_gear_geometry(
                    tooth_count=z, module_mm=module or 0.0,
                    outer_diameter_mm=outer or 0.0, pitch_diameter_mm=pitch or 0.0)
                dims = {
                    "outer_diameter_mm": g["outer_diameter_mm"],
                    "thickness_mm": thickness or height or 12.0,
                    "bore_diameter_mm": shaft or 8.0,   # default 8mm bore
                    "tooth_count": float(z),
                    "module_mm": g["module_mm"],
                    "pitch_diameter_mm": g["pitch_diameter_mm"],
                }
                if not teeth:
                    assumptions.append("Assumed 24 teeth (none specified)")
                assumptions.append(
                    f"Spur gear: module {g['module_mm']:g}mm, {z} teeth, "
                    f"Ø{g['outer_diameter_mm']:g}mm tip, Ø{g['pitch_diameter_mm']:g}mm pitch "
                    "(approximate trapezoidal teeth — concept, not certified AGMA/ISO).")
            else:
                dims = {
                    "outer_diameter_mm": outer or 60.0,
                    "thickness_mm": thickness or height or 12.0,
                    "bore_diameter_mm": shaft or 10.0,
                }
                assumptions.append("Made a grooved pulley (no teeth requested)")
        elif object_type == "adapter_plate":
            dims = {
                "width": width or 100.0,
                "depth": depth or 100.0,
                "thickness": thickness or 6.0,
                "center_bore": bore or diameter or 0.0,
            }
            # Corner bolt pattern keeps holes clear of any center bore.
            holes = _corner_holes(screw_dias, dims["width"], dims["depth"], finish)

        fr = _num_before(text, "fillet", "round")
        if fr:
            fillet = fr
        elif re.search(r"\b(rounded edges|rounded corners|round the edges|filleted)\b", text):
            fillet = 3.0
            assumptions.append("Added 3mm rounded edges")

        if not assumptions:
            assumptions.append(f"Assumed {method} / {units}; filled defaults where unspecified")

        # Zero means "feature off" (e.g. no center bore); drop zeros so the
        # template default applies. Keep NEGATIVE values so the strict schema
        # rejects them and we ask for a clarification instead of guessing.
        dims = {k: v for k, v in dims.items() if v != 0}

        return {
            "object_type": object_type,
            "units": units,
            "manufacturing_method": method,
            "material": "aluminum" if method == "cnc_milling" else "PLA",
            "dimensions": dims,
            "holes": holes,
            "fillet_radius": fillet,
            "missing_required": missing,
            "clarification_question": clarification,
            "assumptions": assumptions,
        }

    def parse_modification(self, prompt: str, current_spec: dict) -> dict:
        return _parse_modification(prompt, current_spec)

    def plan_cad(self, prompt: str, feedback: str | None = None) -> dict | None:
        """Deterministic, offline CadPlan (the same engine the eval suite uses).
        Returns None for parts no specific family recognizes, so the caller falls
        back to the legacy pipeline."""
        from app.cad.plan import deterministic

        plan = deterministic.plan(prompt)
        return plan.model_dump(mode="json") if plan is not None else None

    def plan_feature_graph(self, prompt: str) -> dict | None:
        from app.cad.fallback_graphs import from_prompt

        return from_prompt(prompt)

    def cad_program(self, prompt: str, feedback: str | None = None):
        from app.generation.cad_programs import generate_program

        return generate_program(prompt)

    def plan_general_cad(self, prompt: str) -> dict | None:
        """Deterministic GeneralCADPlan for a few generic mechanical shapes (the
        SCAD-generator route). Offline stand-in for the real LLM planner."""
        t = prompt.lower()
        dim = _num_before(t, "mm", "cube", "block", "wide", "across") or 40.0
        bore = _num_before(t, "hole", "bore") or (
            _num_after(t, "hole", "bore") if "hole" in t or "bore" in t else None
        )
        if any(w in t for w in ("cube", "block", "box")):
            prims = [{"kind": "box", "id": "b",
                      "params": {"width": dim, "depth": dim, "height": dim}}]
            holes = [{"diameter": bore or 10.0, "x": 0, "y": 0}] if (bore or "hole" in t) else []
            return {"object_name": "block", "units": "mm", "primitives": prims,
                    "holes": holes, "assumptions": ["Generic block built from primitives"]}
        if any(w in t for w in ("ring", "washer", "bushing")):
            outer_r = dim / 2
            inner_r = (bore / 2) if bore else dim / 6
            inner_r = min(inner_r, outer_r - 1.5)  # keep a wall
            return {"object_name": "ring", "units": "mm",
                    "primitives": [{"kind": "tube", "id": "t",
                                    "params": {"radius": outer_r, "inner_radius": inner_r,
                                               "height": max(4.0, dim / 6)}}],
                    "assumptions": ["Ring built as a tube primitive"]}
        return None

    def interpret_drawing(
        self, image_b64: str, media_type: str = "image/png", hint: str | None = None
    ) -> dict:
        """Offline stand-in for vision interpretation.

        The mock CANNOT actually read images, so without a user 'correct
        interpretation' hint it returns LOW confidence and refuses to guess a
        template (never silently maps a complex drawing to a bracket). When a
        hint is provided it classifies from that text and extracts numbers.
        """
        if not hint or not hint.strip():
            return {
                "title": "Uploaded drawing",
                "units": "mm",
                "suggested_object_type": None,
                "detected_object_type": "unknown",
                "template_candidate": None,
                "views": [{"view_type": "unknown"}],
                "overall_dimensions": {},
                "holes": [],
                "assumptions": [
                    {"field": "provider",
                     "assumption": "Mock mode cannot read drawings; interpretation is "
                                   "not reliable without a 'correct interpretation' hint."}
                ],
                "clarification_questions": [
                    {"field": "interpretation",
                     "question": "Mock mode can't read the image. Describe the part "
                                 "(e.g. 'flanged pipe branch, 90mm main pipe, 12 bolts "
                                 "per flange') so it can be classified."}
                ],
                "missing_critical_dimensions": ["overall_dimensions"],
                "overall_confidence": 0.2,
                "drawing_units_confidence": 0.3,
                "view_detection_confidence": 0.2,
                "dimension_extraction_confidence": 0.1,
                "unsupported_reason": None,
                "interpretation_rationale": "No legible data could be extracted from the "
                "image in mock mode; awaiting a textual description.",
            }
        return _interpret_from_hint(hint)

    parse_drawing_hint = staticmethod(lambda hint: _interpret_from_hint(hint))


# Words that bump a dimension up or down when no explicit value is given.
_SCALE_UP = 1.25
_SCALE_DOWN = 0.8


def _dim_key(text: str, dims: dict) -> str | None:
    """Map a size word in the edit prompt to an existing dimension key."""
    if "wall" in text and "wall_thickness" in dims:
        return "wall_thickness"
    if re.search(r"\bthick|thickness\b", text):
        return "wall_thickness" if "wall_thickness" in dims else (
            "thickness" if "thickness" in dims else None
        )
    if re.search(r"\bwid|width\b", text):
        return "width" if "width" in dims else None
    if re.search(r"\btall|high|height\b", text):
        return "height" if "height" in dims else None
    if re.search(r"\blong|length|deep|depth\b", text):
        for k in ("length", "depth"):
            if k in dims:
                return k
    return None


def _parse_modification(prompt: str, current_spec: dict) -> dict:
    text = prompt.lower()
    dims = current_spec.get("dimensions", {})
    out: dict = {
        "set_dimensions": {},
        "scale_dimensions": {},
    }
    summary_bits: list[str] = []

    # Explicit "<dim> <value> mm" → absolute override.
    explicit = re.search(
        r"(?:set|make)?[^\d]*?(\d+(?:\.\d+)?)\s*(?:mm|millimet\w*)", text
    )
    target = _dim_key(text, dims)
    if explicit and target:
        val = float(explicit.group(1))
        out["set_dimensions"][target] = val
        summary_bits.append(f"set {target} to {val}mm")

    # Relative size words.
    if not out["set_dimensions"]:
        if re.search(r"\bbigger|larger|scale up|grow\b", text):
            for k in dims:
                out["scale_dimensions"][k] = _SCALE_UP
            summary_bits.append("scaled the whole part up")
        elif re.search(r"\bsmaller|shrink|scale down\b", text):
            for k in dims:
                out["scale_dimensions"][k] = _SCALE_DOWN
            summary_bits.append("scaled the whole part down")
        elif target and re.search(r"\bmore|increase|wider|taller|longer|thicker\b", text):
            out["scale_dimensions"][target] = _SCALE_UP
            summary_bits.append(f"increased {target}")
        elif target and re.search(r"\bless|decrease|narrower|shorter|thinner\b", text):
            out["scale_dimensions"][target] = _SCALE_DOWN
            summary_bits.append(f"reduced {target}")

    # Hole spread.
    if re.search(r"farther apart|further apart|spread .* out|wider spacing|move .* apart", text):
        out["hole_spread_factor"] = 1.3
        summary_bits.append("moved holes farther apart")
    elif re.search(r"closer together|tighter spacing|move .* together", text):
        out["hole_spread_factor"] = 0.75
        summary_bits.append("moved holes closer together")

    # Edge treatment.
    fr = re.search(r"(?:fillet|round\w*)\D*(\d+(?:\.\d+)?)\s*mm", text)
    cf = re.search(r"(?:chamfer|bevel)\D*(\d+(?:\.\d+)?)\s*mm", text)
    if cf:
        out["set_chamfer_size"] = float(cf.group(1))
        summary_bits.append(f"chamfered edges {cf.group(1)}mm")
    elif re.search(r"chamfer|bevel", text):
        out["set_chamfer_size"] = 1.5
        summary_bits.append("added chamfered edges")
    elif fr:
        out["set_fillet_radius"] = float(fr.group(1))
        summary_bits.append(f"rounded edges {fr.group(1)}mm")
    elif re.search(r"round\w* edges|rounded corners|add fillet|round the", text):
        out["set_fillet_radius"] = 3.0
        summary_bits.append("added rounded edges")

    # Material / method.
    if "aluminum" in text or "aluminium" in text:
        out["set_material"] = "aluminum"
        out["set_manufacturing_method"] = "cnc_milling"
        summary_bits.append("switched to machined aluminum")

    # Nothing recognized → ask.
    if (
        not out["set_dimensions"]
        and not out["scale_dimensions"]
        and out.get("hole_spread_factor") is None
        and out.get("set_fillet_radius") is None
        and out.get("set_chamfer_size") is None
        and not out.get("set_material")
    ):
        out["clarification_question"] = (
            "I couldn't tell what to change. Try e.g. 'make it 100mm wide', "
            "'move the holes farther apart', or 'add rounded edges'."
        )
    else:
        out["summary"] = "; ".join(summary_bits)
    return out


def _find_type_strict(text: str) -> str | None:
    """Classify text to a template by keyword, with NO bracket fallback."""
    for object_type, keywords in _TYPE_KEYWORDS:
        for k in keywords:
            if re.search(r"\b" + re.escape(k) + r"\b", text):
                return object_type
    return None


def _interpret_from_hint(hint: str) -> dict:
    """Deterministic drawing interpretation from the user's 'correct
    interpretation' text. Used by the offline mock (which can't read images)."""
    text = hint.lower()
    ot = _find_type_strict(text)
    base = {
        "title": "Drawing (from description)",
        "units": "mm",
        "views": [{"view_type": "front"}, {"view_type": "top"}, {"view_type": "section"}],
        "sections": [],
        "holes": [],
        "assumptions": [
            {"field": "source",
             "assumption": "Classified from the user's description, not from image OCR."}
        ],
        "clarification_questions": [],
        "missing_critical_dimensions": [],
        "drawing_units_confidence": 0.7,
        "view_detection_confidence": 0.7,
        "dimension_extraction_confidence": 0.6,
        "unsupported_reason": None,
    }

    if ot == "flanged_pipe_branch":
        main_d = _num_before(text, "main pipe", "pipe", "diameter") or 90.0
        bolts = _num_before(text, "bolt", "bolts", "holes per flange") or 8.0
        base.update(
            suggested_object_type="flanged_pipe_branch",
            detected_object_type="flanged_pipe_branch",
            template_candidate="flanged_pipe_branch",
            overall_dimensions={"main_pipe_outer_diameter_mm": main_d, "bolt_count": bolts},
            holes=[{"diameter": 14.0, "count": int(bolts), "callout": f"{int(bolts)}x bolt",
                    "confidence": 0.7}],
            overall_confidence=0.82,
            interpretation_rationale="Circular flanges, a bolt circle and a cylindrical "
            "pipe body with a side branch indicate a flanged pipe branch, not a flat plate.",
        )
        return base

    if ot in ("rectangular_bracket", "adapter_plate", "drill_jig"):
        w = _num_before(text, "wide", "width") or 80.0
        d = _num_before(text, "deep", "depth", "long", "length") or 40.0
        t = _num_before(text, "thick", "thickness") or 5.0
        base.update(
            suggested_object_type=ot, detected_object_type=ot, template_candidate=ot,
            overall_dimensions={"width": w, "depth": d, "thickness": t},
            overall_confidence=0.8,
            interpretation_rationale="Rectangular outline with through-holes indicates a plate.",
        )
        return base

    if ot == "simple_gear_or_pulley":
        teeth = _num_before(text, "teeth", "tooth") or 18.0
        dia = _num_before(text, "diameter", "od") or 60.0
        base.update(
            suggested_object_type="simple_gear_or_pulley", detected_object_type="gear",
            template_candidate="simple_gear_or_pulley",
            overall_dimensions={"outer_diameter_mm": dia, "tooth_count": teeth},
            overall_confidence=0.8,
            interpretation_rationale="Circular body with rim teeth indicates a gear.",
        )
        return base

    if ot == "inline_4_crankshaft":
        base.update(
            suggested_object_type="inline_4_crankshaft",
            detected_object_type="inline_4_crankshaft",
            template_candidate="inline_4_crankshaft",
            overall_dimensions={}, overall_confidence=0.8,
            interpretation_rationale="Multiple journals and webs indicate a crankshaft.",
        )
        return base

    # Recognized as complex/pipe-ish but no supported template -> do NOT guess.
    pipe_ish = any(w in text for w in ("pipe", "flange", "bolt circle", "branch", "spool"))
    base.update(
        suggested_object_type=None,
        detected_object_type="unsupported_complex_pipe_assembly" if pipe_ish else "unknown",
        template_candidate=None,
        overall_dimensions={},
        overall_confidence=0.4,
        unsupported_reason=(
            "This looks like a complex pipe/assembly drawing that doesn't match a "
            "supported template closely enough to build safely."
            if pipe_ish else None
        ),
        clarification_questions=(
            [] if pipe_ish else
            [{"field": "object_type",
              "question": "Which kind of part is this? (bracket, enclosure, flanged "
                          "pipe branch, gear, crankshaft, ...)"}]
        ),
        interpretation_rationale="No supported template matched the description "
        "with enough confidence.",
    )
    return base


def _layout_holes(
    diameters: list[float], width: float, depth: float, finish: str = "simple"
) -> list[dict]:
    """Spread N holes along the X centerline, inset from the edges."""
    if not diameters:
        return []
    n = len(diameters)
    inset = min(width * 0.25, depth * 0.35, 15.0)
    if n == 1:
        xs = [0.0]
    else:
        span = width / 2.0 - inset
        xs = [(-span + 2 * span * i / (n - 1)) for i in range(n)]
    return [_hole(d, x, 0.0, finish) for d, x in zip(diameters, xs)]


def _corner_holes(
    diameters: list[float], width: float, depth: float, finish: str = "simple"
) -> list[dict]:
    """Place holes on a rectangular bolt pattern near the corners.

    Keeps fasteners clear of any center bore and reads as a real mounting plate.
    """
    if not diameters:
        return []
    inset = min(width, depth) * 0.15
    px, py = width / 2 - inset, depth / 2 - inset
    corners = [(px, py), (-px, py), (-px, -py), (px, -py)]
    holes: list[dict] = []
    for idx, d in enumerate(diameters):
        x, y = corners[idx % 4]
        holes.append(_hole(d, x, y, finish))
    return holes


def _hole(d: float, x: float, y: float, finish: str) -> dict:
    hole = {"diameter": d, "x": round(x, 2), "y": round(y, 2), "hole_type": finish}
    if finish == "counterbore":
        hole["counterbore_diameter"] = round(d * 1.8, 2)
        hole["counterbore_depth"] = round(d * 0.6, 2)
    elif finish == "countersink":
        hole["countersink_diameter"] = round(d * 1.9, 2)
    return hole
