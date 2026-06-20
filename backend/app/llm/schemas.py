"""JSON Schemas for OpenAI Structured Outputs.

Hand-authored (rather than derived from Pydantic) so they satisfy the Responses
API json_schema format. ``dimensions`` is a free-form map of named lengths, so
those schemas can't use strict mode (strict requires a closed property set);
everything is still re-validated by Pydantic afterwards, which is the real
safety boundary.
"""
from __future__ import annotations

_OBJECT_TYPES = [
    "rectangular_bracket",
    "l_bracket",
    "enclosure",
    "spacer",
    "pipe_clamp",
    "drill_jig",
    "handle",
    "adapter_plate",
    "inline_4_crankshaft",
    "flanged_pipe_branch",
    "simple_gear_or_pulley",
]
_UNITS = ["mm", "cm", "inch"]
_METHODS = [
    "fdm_3d_print",
    "sla_3d_print",
    "cnc_milling",
    "laser_cut",
    "sheet_metal",
]

_HOLE_SCHEMA = {
    "type": "object",
    "properties": {
        "diameter": {"type": "number"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "hole_type": {"type": "string", "enum": ["simple", "counterbore", "countersink"]},
        "screw_size": {"type": ["string", "null"]},
        "counterbore_diameter": {"type": ["number", "null"]},
        "counterbore_depth": {"type": ["number", "null"]},
        "countersink_diameter": {"type": ["number", "null"]},
        "countersink_angle": {"type": ["number", "null"]},
    },
    "required": ["diameter", "x", "y"],
}

DESIGN_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "object_type": {"type": "string", "enum": _OBJECT_TYPES},
        "units": {"type": "string", "enum": _UNITS},
        "manufacturing_method": {"type": "string", "enum": _METHODS},
        "material": {"type": "string"},
        "dimensions": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
        "holes": {"type": "array", "items": _HOLE_SCHEMA},
        "fillet_radius": {"type": ["number", "null"]},
        "chamfer_size": {"type": ["number", "null"]},
        "visual_notes": {"type": ["string", "null"]},
        "missing_required": {"type": "array", "items": {"type": "string"}},
        "clarification_question": {"type": ["string", "null"]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["object_type", "dimensions"],
}

_FG_OPS = [
    "box", "cylinder", "hex_prism", "polygon_prism", "cone", "sphere",
    "extrude_profile", "revolve_profile", "cut_hole", "rectangular_cutout",
    "circular_pattern", "linear_pattern", "boolean_union", "boolean_cut",
    "fillet", "chamfer", "translate", "rotate", "mirror",
]

GENERAL_CAD_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "object_name": {"type": "string"},
        "units": {"type": "string", "enum": _UNITS},
        "coordinate_system": {"type": "string"},
        "overall_dimensions": {"type": "object", "additionalProperties": True},
        "primitives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [
                        "box", "cylinder", "tube", "hex_prism", "polygon_prism",
                        "sphere", "cone"]},
                    "id": {"type": ["string", "null"]},
                    "params": {"type": "object", "additionalProperties": True},
                    "at": {"type": "array", "items": {"type": "number"}},
                    "op": {"type": "string", "enum": ["union", "subtract"]},
                },
                "required": ["kind", "params"],
            },
        },
        "holes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "diameter": {"type": "number"},
                    "x": {"type": "number"}, "y": {"type": "number"},
                    "depth": {"type": ["number", "null"]},
                    "kind": {"type": ["string", "null"]},
                },
                "required": ["diameter"],
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "visual_notes": {"type": ["string", "null"]},
        "export_targets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["primitives"],
}

CAD_FEATURE_GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {"type": "string", "enum": _UNITS},
        "result_id": {"type": ["string", "null"]},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": _FG_OPS},
                    "id": {"type": "string"},
                    "params": {"type": "object", "additionalProperties": True},
                    "at": {"type": "array", "items": {"type": "number"}},
                    "target": {"type": ["string", "null"]},
                    "tool": {"type": ["string", "null"]},
                    "source": {"type": ["string", "null"]},
                    "count": {"type": ["integer", "null"]},
                    "axis": {"type": ["string", "null"]},
                    "plane": {"type": ["string", "null"]},
                    "size": {"type": ["number", "null"]},
                },
                "required": ["op", "id"],
            },
        },
    },
    "required": ["operations"],
}

_CAD_PLAN_FEATURE_KINDS = [
    "box", "plate", "cylinder", "circular_flange", "pipe", "pipe_spool",
    "pipe_elbow", "rectangular_wall", "boss", "rib", "gusset", "shell",
    "hole", "hole_pattern_rect", "hole_pattern_circle", "slot", "v_groove",
    "rectangular_cut", "countersink", "counterbore", "fillet", "chamfer",
    "mirror", "union", "subtract",
]

CAD_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {"type": "string", "enum": _UNITS},
        "object_type": {"type": "string"},
        "name": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "clarification_required": {"type": "boolean"},
        "clarification_questions": {"type": "array", "items": {"type": "string"}},
        "material": {"type": ["string", "null"]},
        "stock": {"type": ["string", "null"]},
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": _CAD_PLAN_FEATURE_KINDS},
                    "op": {"type": "string", "enum": ["add", "cut"]},
                    "params": {"type": "object", "additionalProperties": {"type": "number"}},
                    "at": {"type": "array", "items": {"type": "number"}},
                    "axis": {"type": "string", "enum": ["x", "y", "z"]},
                    "through": {"type": "boolean"},
                    "target": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                },
                "required": ["id", "kind", "params"],
            },
        },
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["union", "subtract", "mirror"]},
                    "id": {"type": "string"},
                    "target": {"type": "string"},
                    "tool": {"type": ["string", "null"]},
                    "plane": {"type": ["string", "null"]},
                },
                "required": ["op", "id", "target"],
            },
        },
        "expected": {
            "type": "object",
            "properties": {
                "bbox_mm": {"type": ["object", "null"], "additionalProperties": {"type": "number"}},
                "hole_count": {"type": ["integer", "null"]},
                "through_hole_count": {"type": ["integer", "null"]},
                "export_formats": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["object_type", "features"],
}

_DRAWING_DIM = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "value": {"type": ["number", "null"]},
        "units": {"type": "string", "enum": _UNITS},
        "tolerance": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["label"],
}
_DRAWING_HOLE = {
    "type": "object",
    "properties": {
        "diameter": {"type": ["number", "null"]},
        "count": {"type": "integer"},
        "callout": {"type": ["string", "null"]},
        "pattern": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": [],
}
_DRAWING_VIEW = {
    "type": "object",
    "properties": {
        "view_type": {
            "type": "string",
            "enum": ["top", "front", "right", "left", "bottom", "isometric",
                     "section", "detail", "unknown"],
        },
        "description": {"type": ["string", "null"]},
        "dimensions": {"type": "array", "items": _DRAWING_DIM},
        "holes": {"type": "array", "items": _DRAWING_HOLE},
    },
    "required": ["view_type"],
}

DRAWING_INTERPRETATION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "units": {"type": "string", "enum": _UNITS},
        # Free-form: any mechanical type (normalized server-side to a supported
        # type or generic_mechanical_part). NOT limited to whole-part templates.
        "suggested_object_type": {"type": ["string", "null"]},
        "views": {"type": "array", "items": _DRAWING_VIEW},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": ["string", "null"]},
                },
                "required": ["name"],
            },
        },
        "overall_dimensions": {"type": "object", "additionalProperties": {"type": "number"}},
        "holes": {"type": "array", "items": _DRAWING_HOLE},
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "assumption": {"type": "string"}},
                "required": ["field", "assumption"],
            },
        },
        "clarification_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "question": {"type": "string"}},
                "required": ["field", "question"],
            },
        },
        "overall_confidence": {"type": "number"},
        "unsupported_reason": {"type": ["string", "null"]},
    },
    "required": ["views"],
}

DESIGN_MODIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "set_dimensions": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
        "scale_dimensions": {
            "type": "object",
            "additionalProperties": {"type": "number"},
        },
        "set_fillet_radius": {"type": ["number", "null"]},
        "set_chamfer_size": {"type": ["number", "null"]},
        "hole_spread_factor": {"type": ["number", "null"]},
        "set_material": {"type": ["string", "null"]},
        "set_manufacturing_method": {
            "type": ["string", "null"],
            "enum": [*_METHODS, None],
        },
        "clarification_question": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
    },
    "required": [],
}
