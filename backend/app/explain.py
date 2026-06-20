"""Generate a plain-English explanation of a generated part from its spec.

Deterministic and template-driven (no LLM) so the explanation always matches the
geometry the user is actually looking at.
"""
from __future__ import annotations

from app.cad.registry import get_template
from app.schemas.design_spec import DesignSpec, HoleType

_TYPE_NOUN = {
    "rectangular_bracket": "flat mounting bracket",
    "l_bracket": "right-angle L-bracket",
    "enclosure": "two-part electronics enclosure (body + lid)",
    "spacer": "cylindrical spacer / standoff",
    "pipe_clamp": "pipe clamp / saddle",
    "drill_jig": "drill jig plate",
    "handle": "knob / handle",
    "adapter_plate": "adapter plate",
    "inline_4_crankshaft": "inline-4 engine crankshaft",
    "flanged_pipe_branch": "flanged pipe branch / tee",
    "simple_gear_or_pulley": "gear / pulley",
    "feature_graph": "custom part (flexible CAD graph)",
}

_METHOD_PHRASE = {
    "fdm_3d_print": "FDM 3D printing",
    "sla_3d_print": "SLA resin printing",
    "cnc_milling": "CNC milling",
    "laser_cut": "laser cutting",
    "sheet_metal": "sheet-metal fabrication",
}


def _fmt(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def explain(spec: DesignSpec) -> str:
    noun = _TYPE_NOUN.get(spec.object_type, "mechanical part")
    dims_mm = spec.dims_in_mm()
    parts: list[str] = []

    # Lead sentence with the headline dimensions.
    headline_keys = [
        k
        for k in ("width", "depth", "length", "height", "outer_diameter", "diameter")
        if k in dims_mm
    ][:3]
    if headline_keys:
        size = " × ".join(f"{_fmt(dims_mm[k])}mm" for k in headline_keys)
        parts.append(f"This is a {noun}, roughly {size}.")
    else:
        parts.append(f"This is a {noun}.")

    for key, label in (
        ("thickness", "plate thickness"),
        ("wall_thickness", "wall thickness"),
        ("lid_thickness", "lid thickness"),
    ):
        if key in dims_mm:
            parts.append(f"The {label} is {_fmt(dims_mm[key])}mm.")

    # Holes.
    if spec.holes:
        finishes = {h.hole_type for h in spec.holes}
        dia = _fmt(spec.to_mm(spec.holes[0].diameter))
        n = len(spec.holes)
        kind = "holes" if n != 1 else "hole"
        finish_words = []
        if HoleType.counterbore in finishes:
            finish_words.append("counterbored")
        if HoleType.countersink in finishes:
            finish_words.append("countersunk")
        finish = (" " + "/".join(finish_words)) if finish_words else ""
        parts.append(
            f"It has {n}{finish} {dia}mm {kind} for fasteners."
        )

    # Edge treatment.
    if spec.fillet_radius:
        parts.append(f"Edges are rounded with a {_fmt(spec.to_mm(spec.fillet_radius))}mm fillet.")
    elif spec.chamfer_size:
        parts.append(f"Edges are chamfered {_fmt(spec.to_mm(spec.chamfer_size))}mm.")

    # Template-specific notes.
    if spec.object_type == "simple_gear_or_pulley":
        if dims_mm.get("hex", 0) > 0.5:
            parts.append("Made as a hexagonal outer profile (assumed from 'hex').")
        elif dims_mm.get("tooth_count", 0) > 0:
            parts.append(f"Spur gear with {int(dims_mm['tooth_count'])} teeth.")
        else:
            parts.append("Made as a grooved pulley (no teeth requested).")
        if dims_mm.get("bore_diameter_mm", 0) > 0:
            parts.append(f"Center bore ø{_fmt(dims_mm['bore_diameter_mm'])}mm for the shaft.")
    if spec.object_type == "enclosure" and dims_mm.get("boss_diameter", 0) > 0:
        parts.append("Internal corner bosses let you screw the lid down onto the body.")
    if spec.object_type == "drill_jig" and dims_mm.get("lip_height", 0) > 0:
        parts.append("A registration lip hooks over the workpiece edge for repeatable drilling.")
    if spec.object_type == "rectangular_bracket" and dims_mm.get("gusset_height", 0) > 0:
        parts.append("A triangular gusset rib stiffens it for wall mounting.")

    # Material + method.
    method = _METHOD_PHRASE.get(spec.manufacturing_method, spec.manufacturing_method)
    parts.append(f"Intended for {method} in {spec.material}.")

    return " ".join(parts)
