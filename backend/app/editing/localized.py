"""Apply a LocalizedModificationSpec to a DesignSpec (deterministic, in mm).

Each operation maps to a constrained edit of the validated spec; geometry is
then rebuilt by the trusted templates. Unsupported combinations raise
``UnsupportedLocalizedEdit`` with a useful explanation rather than guessing.
"""
from __future__ import annotations

import re

from app.cad.features import extract_features
from app.schemas.design_spec import DesignSpec, Hole, HoleType, Units
from app.schemas.editing_spec import (
    LocalizedEditRequest,
    LocalizedEditResult,
    LocalizedModificationSpec,
    LocalizedOperation,
)

_PLATE_TYPES = {"rectangular_bracket", "adapter_plate", "drill_jig"}

# ISO 273 "medium" series metric clearance-hole diameters, mm — used by
# replace_standard so swapping a hole's standard snaps to a real published
# size instead of accepting an arbitrary float (that's what change_hole_diameter
# is for).
_METRIC_CLEARANCE_MM = {
    "M2": 2.4, "M2.5": 2.9, "M3": 3.4, "M4": 4.5, "M5": 5.5,
    "M6": 6.6, "M8": 9.0, "M10": 11.0, "M12": 13.5, "M16": 17.5, "M20": 22.0,
}


def _extract_metric_standard(instruction: str) -> str | None:
    m = re.search(r"\bM\s*(\d+(?:\.\d+)?)\b", instruction, re.IGNORECASE)
    return f"M{m.group(1)}" if m else None


def _extract_fit_class(instruction: str):
    from app.schemas.calibration import FitClass

    t = instruction.lower()
    for fc in FitClass:
        if fc.value in t:
            return fc
    if "interference" in t or "press fit" in t or "press-fit" in t:
        return FitClass.press
    if "slip" in t or "free" in t or "running" in t:
        return FitClass.loose
    return None


class UnsupportedLocalizedEdit(Exception):
    """Raised when the selected operation can't be applied to the selection."""


def _num(instruction: str) -> float | None:
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:mm|millimet\w*)?", instruction)
    return float(m.group(1)) if m else None


def extract_parameters(op: str, instruction: str) -> dict:
    """Best-effort NL -> validated_parameters when the client didn't supply them."""
    value = _num(instruction.lower())
    params: dict[str, float] = {}
    if op == LocalizedOperation.change_hole_diameter.value and value is not None:
        params["diameter"] = value
    elif op in (LocalizedOperation.add_fillet.value, LocalizedOperation.add_chamfer.value):
        params["size"] = value if value is not None else 3.0
    elif op == LocalizedOperation.thicken_wall.value and value is not None:
        params["thickness"] = value
    elif op == LocalizedOperation.add_cutout.value and value is not None:
        params["count"] = value
    elif op == LocalizedOperation.change_bolt_hole_diameter.value and value is not None:
        params["diameter"] = value
    elif op == LocalizedOperation.thicken_flange.value and value is not None:
        params["thickness"] = value
    elif op == LocalizedOperation.resize_hole_group.value and value is not None:
        params["diameter"] = value
    elif op == LocalizedOperation.change_fit_class.value and value is not None:
        params["pin_diameter"] = value
    return params


def _spec_to_mm(spec: DesignSpec) -> tuple[dict, list[Hole]]:
    dims = spec.dims_in_mm()
    holes = []
    for h in spec.holes:
        holes.append(
            Hole(
                diameter=spec.to_mm(h.diameter),
                x=spec.to_mm(h.x),
                y=spec.to_mm(h.y),
                hole_type=h.hole_type,
                counterbore_diameter=spec.to_mm(h.counterbore_diameter) if h.counterbore_diameter else None,
                counterbore_depth=spec.to_mm(h.counterbore_depth) if h.counterbore_depth else None,
                countersink_diameter=spec.to_mm(h.countersink_diameter) if h.countersink_diameter else None,
                countersink_angle=h.countersink_angle,
            )
        )
    return dims, holes


def _rebuild(spec: DesignSpec, dims: dict, holes: list[Hole], **over) -> DesignSpec:
    payload = dict(
        object_type=spec.object_type,
        units=Units.mm,
        manufacturing_method=spec.manufacturing_method,
        material=spec.material,
        dimensions={k: v for k, v in dims.items() if v != 0},
        holes=[h.model_dump() for h in holes],
        fillet_radius=spec.to_mm(spec.fillet_radius) if spec.fillet_radius else None,
        chamfer_size=spec.to_mm(spec.chamfer_size) if spec.chamfer_size else None,
        notes=spec.notes,
    )
    payload.update(over)
    return DesignSpec(**payload)


def _hole_index(mod: LocalizedModificationSpec, n: int) -> int:
    try:
        i = int(mod.selected_entity_id)
    except (TypeError, ValueError) as exc:
        raise UnsupportedLocalizedEdit(
            "Select a specific hole before changing it."
        ) from exc
    if not (0 <= i < n):
        raise UnsupportedLocalizedEdit(f"Hole {i} does not exist on this part.")
    return i


def apply_localized(
    spec: DesignSpec, mod: LocalizedModificationSpec
) -> tuple[DesignSpec, str]:
    op = mod.allowed_operation
    params = dict(mod.validated_parameters) or extract_parameters(
        op, mod.natural_language_instruction
    )
    dims, holes = _spec_to_mm(spec)
    instr = mod.natural_language_instruction.lower()

    if op == LocalizedOperation.change_hole_diameter.value:
        i = _hole_index(mod, len(holes))
        d = params.get("diameter")
        if not d or d <= 0:
            raise UnsupportedLocalizedEdit("Tell me the new hole diameter, e.g. '8 mm'.")
        holes[i].diameter = d
        return _rebuild(spec, dims, holes), f"Hole {i + 1} set to ø{d:.1f} mm"

    if op in (LocalizedOperation.change_hole_type.value,
              LocalizedOperation.add_counterbore.value,
              LocalizedOperation.add_countersink.value):
        i = _hole_index(mod, len(holes))
        h = holes[i]
        want_cbore = op == LocalizedOperation.add_counterbore.value or "counterbore" in instr
        want_csk = op == LocalizedOperation.add_countersink.value or "countersink" in instr or "countersunk" in instr
        if want_cbore:
            h.hole_type = HoleType.counterbore
            h.counterbore_diameter = params.get("counterbore_diameter") or round(h.diameter * 1.8, 2)
            h.counterbore_depth = params.get("counterbore_depth") or round(h.diameter * 0.6, 2)
            h.countersink_diameter = None
            return _rebuild(spec, dims, holes), f"Hole {i + 1} is now counterbored"
        if want_csk:
            h.hole_type = HoleType.countersink
            h.countersink_diameter = params.get("countersink_diameter") or round(h.diameter * 1.9, 2)
            h.counterbore_diameter = None
            h.counterbore_depth = None
            return _rebuild(spec, dims, holes), f"Hole {i + 1} is now countersunk"
        raise UnsupportedLocalizedEdit(
            "Say whether to make the hole counterbored or countersunk."
        )

    if op == LocalizedOperation.add_fillet.value:
        size = params.get("size") or 3.0
        return _rebuild(spec, dims, holes, fillet_radius=size, chamfer_size=None), (
            f"Rounded the part edges with a {size:.1f} mm fillet"
        )

    if op == LocalizedOperation.add_chamfer.value:
        size = params.get("size") or 1.5
        return _rebuild(spec, dims, holes, chamfer_size=size, fillet_radius=None), (
            f"Chamfered the part edges {size:.1f} mm"
        )

    if op == LocalizedOperation.thicken_wall.value:
        t = params.get("thickness")
        if not t or t <= 0:
            raise UnsupportedLocalizedEdit("Tell me the new wall thickness, e.g. '4 mm'.")
        key = "wall_thickness" if "wall_thickness" in dims else (
            "thickness" if "thickness" in dims else "wall_thickness"
        )
        dims[key] = t
        return _rebuild(spec, dims, holes), f"{key.replace('_', ' ')} set to {t:.1f} mm"

    if op in (LocalizedOperation.move_hole.value, LocalizedOperation.move_feature.value):
        i = _hole_index(mod, len(holes))
        if "x" in params:
            holes[i].x = params["x"]
        if "y" in params:
            holes[i].y = params["y"]
        return _rebuild(spec, dims, holes), f"Moved hole {i + 1}"

    if op == LocalizedOperation.resize_hole_group.value:
        i = _hole_index(mod, len(holes))
        d = params.get("diameter")
        if not d or d <= 0:
            raise UnsupportedLocalizedEdit("Tell me the new hole diameter, e.g. '8 mm'.")
        ref_dia = holes[i].diameter
        changed = 0
        for h in holes:
            if abs(h.diameter - ref_dia) < 1e-6:
                h.diameter = d
                changed += 1
        return _rebuild(spec, dims, holes), (
            f"Resized {changed} hole{'s' if changed != 1 else ''} from "
            f"ø{ref_dia:.1f} mm to ø{d:.1f} mm"
        )

    if op == LocalizedOperation.suppress_feature.value:
        # No suppress/unsuppress flag exists on Hole yet, so this is
        # implemented as removal — reversible via version history/restore
        # (app.services.version_service), not via toggling this op back.
        i = _hole_index(mod, len(holes))
        holes.pop(i)
        return _rebuild(spec, dims, holes), (
            f"Suppressed hole {i + 1} (restore an earlier version to bring it back)"
        )

    if op == LocalizedOperation.replace_standard.value:
        i = _hole_index(mod, len(holes))
        std = _extract_metric_standard(instr)
        if std is None or std not in _METRIC_CLEARANCE_MM:
            raise UnsupportedLocalizedEdit(
                "Tell me the new standard, e.g. 'M8', from: "
                + ", ".join(sorted(_METRIC_CLEARANCE_MM, key=lambda s: _METRIC_CLEARANCE_MM[s]))
            )
        d = _METRIC_CLEARANCE_MM[std]
        holes[i].diameter = d
        return _rebuild(spec, dims, holes), (
            f"Hole {i + 1} now sized for {std} clearance (ø{d:.1f} mm)"
        )

    if op == LocalizedOperation.change_fit_class.value:
        from app.cad.calibration.resolver import resolve_measurement
        from app.schemas.calibration import CalibrationMeasurementType

        i = _hole_index(mod, len(holes))
        fit = _extract_fit_class(instr)
        if fit is None:
            raise UnsupportedLocalizedEdit(
                "Tell me the fit class: press, snug, normal, or loose."
            )
        pin_mm = params.get("pin_diameter")
        if not pin_mm or pin_mm <= 0:
            raise UnsupportedLocalizedEdit(
                "Tell me the shaft/pin diameter this hole needs to fit, e.g. '6 mm shaft'."
            )
        # Uses the generic engineering-estimate clearance table (no per-user
        # calibration profile lookup here — apply_localized is deliberately a
        # pure, DB-free function; a real profile is used during full
        # generation, see app.services.calibration_service.resolve_for_generation).
        resolved = resolve_measurement(
            [], measurement_type=CalibrationMeasurementType.diametral_clearance,
            feature="hole_pin_clearance", fit_class=fit,
        )
        d = round(pin_mm + resolved.value_mm, 2)
        holes[i].diameter = d
        return _rebuild(spec, dims, holes), (
            f"Hole {i + 1} set to a {fit.value} fit for a {pin_mm:.1f} mm pin/shaft "
            f"(ø{d:.2f} mm, engineering estimate)"
        )

    if op == LocalizedOperation.add_gusset.value:
        if spec.object_type != "rectangular_bracket":
            raise UnsupportedLocalizedEdit("Gussets are only supported on rectangular brackets.")
        dims["gusset_height"] = params.get("height") or round(dims.get("depth", 40) * 0.5, 1)
        return _rebuild(spec, dims, holes), "Added a stiffening gusset"

    if op == LocalizedOperation.change_bolt_hole_diameter.value:
        if spec.object_type != "flanged_pipe_branch":
            raise UnsupportedLocalizedEdit("Bolt-hole edits apply to flanged pipe branches.")
        d = params.get("diameter")
        if not d or d <= 0:
            raise UnsupportedLocalizedEdit("Tell me the new bolt-hole diameter, e.g. '8 mm'.")
        dims["bolt_hole_diameter_mm"] = d
        return _rebuild(spec, dims, holes), f"Flange bolt holes set to ø{d:.1f} mm"

    if op == LocalizedOperation.thicken_flange.value:
        from app.cad.registry import get_template
        defaults = get_template(spec.object_type).default_dimensions()
        if "flange_thickness_mm" not in defaults:
            raise UnsupportedLocalizedEdit("This part has no flange to thicken.")
        current = dims.get("flange_thickness_mm", defaults["flange_thickness_mm"])
        t = params.get("thickness") or round(current * 1.4, 1)
        dims["flange_thickness_mm"] = t
        return _rebuild(spec, dims, holes), f"Flange thickness set to {t:.1f} mm"

    if op == LocalizedOperation.add_cutout.value:
        if spec.object_type == "enclosure":
            dims["vent_count"] = float(int(params.get("count") or 3))
            return _rebuild(spec, dims, holes), (
                f"Added {int(dims['vent_count'])} vent slots to the enclosure wall"
            )
        if spec.object_type in _PLATE_TYPES:
            d = params.get("diameter") or 6.0
            x = params.get("x", 0.0)
            y = params.get("y", 0.0)
            holes.append(Hole(diameter=d, x=x, y=y))
            return _rebuild(spec, dims, holes), f"Added a ø{d:.1f} mm hole"
        raise UnsupportedLocalizedEdit(
            f"Cutouts aren't supported on a {spec.object_type} yet."
        )

    raise UnsupportedLocalizedEdit(f"Operation '{op}' is not supported.")


def _infer_operation(entity_type: str, instruction: str) -> str:
    t = instruction.lower()
    if entity_type == "hole":
        if "counterbore" in t:
            return LocalizedOperation.add_counterbore.value
        if "countersink" in t or "countersunk" in t:
            return LocalizedOperation.add_countersink.value
        if "move" in t:
            return LocalizedOperation.move_hole.value
        if "suppress" in t or "remove this hole" in t or "delete this hole" in t:
            return LocalizedOperation.suppress_feature.value
        if "fit" in t and any(w in t for w in ("press", "snug", "loose", "clearance", "shaft", "pin")):
            return LocalizedOperation.change_fit_class.value
        if "standard" in t and _extract_metric_standard(t):
            return LocalizedOperation.replace_standard.value
        if "all" in t and "hole" in t:
            return LocalizedOperation.resize_hole_group.value
        return LocalizedOperation.change_hole_diameter.value
    if entity_type == "bolt_pattern":
        return LocalizedOperation.change_bolt_hole_diameter.value
    if entity_type == "flange":
        return LocalizedOperation.thicken_flange.value
    if entity_type == "edge":
        return (LocalizedOperation.add_chamfer.value if "chamfer" in t or "bevel" in t
                else LocalizedOperation.add_fillet.value)
    if entity_type in ("face", "vent"):
        if "vent" in t:
            return LocalizedOperation.add_cutout.value
        if "thick" in t:
            return LocalizedOperation.thicken_wall.value
        return LocalizedOperation.add_cutout.value
    if entity_type == "boss":
        return LocalizedOperation.add_cutout.value
    if entity_type == "body":
        if "round" in t or "fillet" in t:
            return LocalizedOperation.add_fillet.value
        if "chamfer" in t:
            return LocalizedOperation.add_chamfer.value
        if "gusset" in t:
            return LocalizedOperation.add_gusset.value
    return ""


def apply_localized_request(
    spec: DesignSpec, req: LocalizedEditRequest, bbox: dict | None = None
) -> tuple[DesignSpec | None, LocalizedEditResult]:
    """Circle-to-edit entry point. Validates the selected feature id against the
    model's features, infers the operation if needed, and applies it."""
    valid_ids = {f.id for f in extract_features(spec, bbox)}
    entity_id = req.selected.entity_id
    entity_type = req.selected.entity_type

    if entity_id not in valid_ids:
        return None, LocalizedEditResult(
            applied=False,
            message=f"'{entity_id}' is not a feature of this model. "
            f"Select one of: {', '.join(sorted(valid_ids))[:200]}",
        )

    op = req.operation or _infer_operation(entity_type, req.instruction)
    if not op:
        return None, LocalizedEditResult(
            applied=False,
            message="I couldn't tell what change to make to that selection.",
        )

    # Translate the feature id to the legacy selected_entity_id where needed.
    legacy_id = entity_id
    if entity_type == "hole" and entity_id.startswith("hole_"):
        legacy_id = entity_id.split("_", 1)[1]  # "hole_2" -> "2"

    mod = LocalizedModificationSpec(
        selected_entity_type=entity_type,
        selected_entity_id=legacy_id,
        allowed_operation=op,
        natural_language_instruction=req.instruction,
        validated_parameters=req.validated_parameters,
    )
    try:
        new_spec, message = apply_localized(spec, mod)
    except UnsupportedLocalizedEdit as exc:
        return None, LocalizedEditResult(applied=False, message=str(exc), operation=op,
                                         selected_entity_id=entity_id)
    return new_spec, LocalizedEditResult(
        applied=True, message=message, operation=op, selected_entity_id=entity_id
    )
