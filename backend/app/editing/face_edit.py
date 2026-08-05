"""Phase 4: localized *visual face* edits.

The viewer selects a continuous visual face and sends its geometry context plus a
plain-English instruction. We classify the edit into a constrained operation and
translate it into trusted ``DesignSpec`` changes — geometry is then rebuilt by
the same safe parametric pipeline, so STL/STEP exports stay valid. The
instruction is used only for keyword classification and number extraction; it is
never executed and the LLM never emits geometry.

Deterministic handlers regenerate real CAD for:
  * add_hole  — centered through hole on the top/bottom planar face of a plate
  * add_vent  — evenly spaced ventilation slots on an enclosure wall
  * fillet    — round the part edges
  * chamfer   — bevel the part edges

Anything else returns ``FaceEditReview`` (understood but not supported yet) or
``FaceEditError`` (invalid / unsafe) and the original design is left unchanged.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from app.cad.selectable_faces import extract_selectable_holes, extract_selectable_holes_from_plan
from app.editing.localized import _rebuild, _spec_to_mm
from app.schemas.design_spec import DesignSpec, Hole
from app.schemas.editing_spec import FaceLocalizedEditRequest, FaceSelectionSpec

# Parts whose templates actually drill spec.holes on the +Z face.
_HOLE_PLATE_TYPES = {"rectangular_bracket", "adapter_plate", "drill_jig"}
# Parts whose templates apply the spec fillet/chamfer edge treatment.
_EDGE_TREATMENT_TYPES = {
    "rectangular_bracket", "adapter_plate", "drill_jig", "l_bracket", "enclosure", "handle",
}
# The two planar faces where a Z-axis through hole is meaningful.
_FLAT_FACES = {"face_top", "face_bottom"}

# Quick-action / allowed-operation key -> canonical operation.
_QUICK_ACTION_OPS = {
    # face
    "hole": "add_hole",
    "slot": "add_slot",
    "cutout": "add_cutout",
    "boss": "add_boss",
    "vent": "add_vent",
    "fillet": "fillet",
    "chamfer": "chamfer",
    "pattern": "pattern",
    "add_hole": "add_hole",
    "add_vent": "add_vent",
    # edge
    "fillet_edge": "fillet",
    "chamfer_edge": "chamfer",
    "fillet_edges": "fillet",
    "chamfer_edges": "chamfer",
    "measure": "measure",
    # hole
    "resize_hole": "resize_hole",
    "move_hole": "move_hole",
    "delete_hole": "delete_hole",
    "pattern_hole": "pattern_hole",
    # body / feature (not deterministically supported yet)
    "rename": "rename",
    "material": "material",
    "export_body": "export_body",
    "duplicate": "duplicate",
    "mirror": "mirror",
    "edit_dimensions": "edit_dimensions",
    "suppress": "suppress",
}

SUPPORTED_OPS = {"add_hole", "add_vent", "fillet", "chamfer", "resize_hole", "delete_hole"}


class FaceEditError(Exception):
    """Rejected: invalid or unsafe edit. Original design must stay unchanged."""


class FaceEditReview(Exception):
    """Understood but not supported yet (needs_review). Design stays unchanged.

    Carries an optional ``code`` (for the structured API response) and the
    ``operation`` that was requested. Subclassed by :class:`FaceEditUnsupported`
    for operations we deliberately don't implement yet."""

    code = "needs_selection"

    def __init__(self, message: str, operation: Optional[str] = None):
        super().__init__(message)
        self.operation = operation


class FaceEditUnsupported(FaceEditReview):
    """A recognized operation that isn't implemented for the selected geometry.

    Surfaced to the client as ``code: 'unsupported_operation'`` with
    ``safe_to_retry: true`` — the UI shows a calm limitation, not a failure."""

    code = "unsupported_operation"


# Calm, user-facing message for a recognized-but-unimplemented edit.
UNSUPPORTED_EDIT_MESSAGE = "This edit is not supported yet for this selected geometry."


@dataclass
class FaceEditOutcome:
    op: str
    message: str
    params: dict = field(default_factory=dict)
    local_frame: dict = field(default_factory=dict)


# Canonical ops that are meaningful for each selection kind. Used to keep an
# operation consistent with what the user actually selected (a hole only resizes
# or deletes; an edge only fillets or chamfers), so a stray keyword or a mismatched
# chip can never route a hole edit into "add a new hole".
_HOLE_CANON_OPS = {"resize_hole", "delete_hole"}
_EDGE_CANON_OPS = {"fillet", "chamfer"}


def _selection_kind(sel: Optional[FaceSelectionSpec]) -> str:
    """The kind of geometry selected: hole / edge / body / feature / face.

    Derived from the entity ids / selection_type the viewer sends, so routing is
    driven by WHAT was selected, not by keywords in the free-text instruction."""
    if sel is None:
        return "face"
    st = sel.selection_type
    if st == "backend_hole" or sel.hole_id:
        return "hole"
    if st == "backend_edge" or sel.edge_id:
        return "edge"
    if st == "backend_body" or sel.body_id:
        return "body"
    if st == "backend_feature":
        return "feature"
    return "face"


def classify_edit(
    quick_action: Optional[str],
    instruction: str,
    sel: Optional[FaceSelectionSpec] = None,
) -> str:
    """Map a selection + chip/instruction to a canonical operation.

    SELECTION-AWARE: the selected entity kind has priority over instruction
    keywords, so a selected hole always resolves to resize/delete (never
    add_hole), a selected edge to fillet/chamfer, and a bare cylindrical face or
    body/feature to an unsupported (calm) op. A chip id is honored only when it is
    consistent with the selection kind."""
    kind = _selection_kind(sel)
    t = (instruction or "").lower()
    qop = _QUICK_ACTION_OPS.get(quick_action) if quick_action else None

    # --- hole: resize or delete only -------------------------------------
    if kind == "hole":
        if qop in _HOLE_CANON_OPS:
            return qop
        if "delete" in t or "remove" in t:
            return "delete_hole"
        return "resize_hole"  # "make it 5mm", "hole 8mm", "change diameter", …

    # --- edge: fillet or chamfer only ------------------------------------
    if kind == "edge":
        if qop in _EDGE_CANON_OPS:
            return qop
        if "chamfer" in t or "bevel" in t:
            return "chamfer"
        return "fillet"  # "fillet", "round this edge", default

    # --- body / feature: nothing implemented yet -------------------------
    if kind in ("body", "feature"):
        return "unsupported_selection"

    # --- face -------------------------------------------------------------
    face_kind = getattr(sel, "face_kind", "unknown") if sel is not None else "unknown"
    # A bare cylindrical face (not resolved to a hole) has no safe parametric edit.
    # Real holes arrive as kind == "hole" (the viewer promotes wall clicks), so we
    # never lose hole editing here.
    if face_kind == "cylindrical":
        return "unsupported_selection"

    # Planar / plate face: an explicit chip wins, else keyword routing.
    if qop:
        return qop
    if "vent" in t or "ventilation" in t:
        return "add_vent"
    if "slot" in t:
        return "add_slot"
    if "boss" in t:
        return "add_boss"
    if "chamfer" in t:
        return "chamfer"
    if "fillet" in t or "round" in t:
        return "fillet"
    if "delete" in t or "remove" in t:
        return "delete_hole"
    if "resize" in t or "change" in t and ("hole" in t or "diameter" in t):
        return "resize_hole"
    if "pattern" in t:
        return "pattern"
    if "hole" in t or "drill" in t or "bore" in t:
        return "add_hole"
    if "cutout" in t or "cut out" in t or "opening" in t or "pocket" in t or "cut " in t:
        return "add_cutout"
    return "freeform_local_edit"


def _first_number(instruction: str) -> Optional[float]:
    """First numeric literal in the instruction (mm assumed), else None."""
    m = re.search(r"(-?\d+(?:\.\d+)?)", instruction)
    return float(m.group(1)) if m else None


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def build_local_frame(sel: FaceSelectionSpec) -> dict:
    """Local frame at the selection: origin, Z=normal, stable X/Y on the face."""
    if sel.local_frame is not None:
        lf = sel.local_frame
        return {
            "origin": list(lf.origin),
            "z_axis": list(_normalize(lf.normal)),
            "x_axis": list(_normalize(lf.tangent)),
            "y_axis": list(_normalize(lf.bitangent)),
        }
    n = _normalize(sel.normal)
    ref = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    x = _normalize(_cross(n, ref))
    y = _normalize(_cross(n, x))
    origin = sel.center if any(sel.center) else sel.clicked_point
    return {"origin": list(origin), "z_axis": list(n), "x_axis": list(x), "y_axis": list(y)}


def _in_plane_extent_mm(bbox: dict | None) -> Optional[float]:
    """Smaller in-plane dimension of a plate, in mm.

    A plate's thickness is its smallest overall dimension, so the two in-plane
    dimensions are the two largest — their min is the *middle* of the three
    sorted bbox extents. This is robust to which axis the thickness lies on."""
    if not bbox:
        return None
    vals = sorted(float(bbox.get(k) or 0.0) for k in ("x", "y", "z"))
    return vals[1] if vals[1] > 0 else None


# --- deterministic handlers ------------------------------------------------


def _handle_add_hole(
    spec: DesignSpec, sel: FaceSelectionSpec, instruction: str, bbox: dict | None
) -> tuple[DesignSpec, str, dict]:
    if spec.object_type not in _HOLE_PLATE_TYPES:
        raise FaceEditReview(
            f"Adding a hole isn't supported on a '{spec.object_type}' yet — this works "
            "on flat bracket / plate parts."
        )
    if sel.face_kind not in ("planar", "unknown"):
        raise FaceEditReview("Select a flat (planar) face to add a through hole.")
    face = sel.feature_id or sel.backend_face_id
    if face is not None and face not in _FLAT_FACES:
        raise FaceEditReview(
            "Through holes are added on the top or bottom face — select that face."
        )
    dia = _first_number(instruction)
    if dia is None:
        dia = 6.0  # sensible default clearance hole when no size was given
    if dia <= 0:
        raise FaceEditError("Hole diameter must be greater than zero.")
    extent = _in_plane_extent_mm(bbox)
    if extent is not None and dia >= extent * 0.95:
        raise FaceEditError(
            f"A Ø{dia:g} mm hole is too large for the ~{extent:g} mm face — it would "
            "remove the part."
        )
    dims, holes = _spec_to_mm(spec)
    holes.append(Hole(diameter=dia, x=0.0, y=0.0))
    new_spec = _rebuild(spec, dims, holes)
    params = {"diameter_mm": dia, "x_mm": 0.0, "y_mm": 0.0}
    return new_spec, f"Added a Ø{dia:g} mm through hole centered on the face", params


def _handle_add_vent(
    spec: DesignSpec, instruction: str
) -> tuple[DesignSpec, str, dict]:
    if spec.object_type != "enclosure":
        raise FaceEditReview("Ventilation slots are supported on enclosures.")
    count = _first_number(instruction)
    n = int(count) if count and count > 0 else 3
    n = max(1, min(n, 40))
    dims, holes = _spec_to_mm(spec)
    dims["vent_count"] = float(n)
    return _rebuild(spec, dims, holes), f"Added {n} ventilation slots to the enclosure wall", {"vent_count": n}


def _handle_edge_treatment(
    spec: DesignSpec, instruction: str, bbox: dict | None, chamfer: bool
) -> tuple[DesignSpec, str, dict]:
    if spec.object_type not in _EDGE_TREATMENT_TYPES:
        kind = "Chamfer" if chamfer else "Fillet"
        raise FaceEditReview(
            f"{kind} edits aren't supported on a '{spec.object_type}' yet."
        )
    size = _first_number(instruction)
    if size is None:
        size = 1.0 if chamfer else 2.0
    if size <= 0:
        raise FaceEditError(f"{'Chamfer' if chamfer else 'Fillet'} size must be greater than zero.")
    # Cap against the in-plane extent (not the thickness): the spec's edge
    # treatment rounds the vertical corner edges, so a radius approaching half
    # the plate footprint would obliterate the part.
    extent = _in_plane_extent_mm(bbox)
    if extent is not None and size >= extent * 0.5:
        raise FaceEditError(
            f"A {size:g} mm {'chamfer' if chamfer else 'fillet'} is too large for the "
            f"~{extent:g} mm part."
        )
    dims, holes = _spec_to_mm(spec)
    if chamfer:
        new_spec = _rebuild(spec, dims, holes, chamfer_size=size, fillet_radius=None)
        return new_spec, f"Chamfered the part edges {size:g} mm", {"chamfer_mm": size}
    new_spec = _rebuild(spec, dims, holes, fillet_radius=size, chamfer_size=None)
    return new_spec, f"Rounded the part edges with a {size:g} mm fillet", {"fillet_mm": size}


def _hole_index_from_spec(sel: FaceSelectionSpec, spec: DesignSpec) -> int:
    """Resolve the selected hole's index in ``spec.holes`` by matching its
    stable ``hole_id`` (see ``app.cad.selectable_faces``) against the ids
    computed for the current spec, rather than parsing digits out of the id —
    a hash-based id has no positional meaning to parse."""
    raw = sel.hole_id or sel.feature_id or ""
    if raw:
        for h in extract_selectable_holes(spec):
            if h["hole_id"] == raw:
                return h["hole_index"]
    raise FaceEditReview("Select a specific hole to edit.")


def _hole_index_from_plan(sel: FaceSelectionSpec, plan) -> int:
    """Plan-graph analog of :func:`_hole_index_from_spec` — resolves the index
    into ``_plan_hole_features(plan)`` (all hole-kind features, unfiltered)."""
    raw = sel.hole_id or sel.feature_id or ""
    if raw:
        for h in extract_selectable_holes_from_plan(plan):
            if h["hole_id"] == raw:
                return h["feature_index"]
    raise FaceEditReview("Select a specific hole to edit.")


def _handle_resize_hole(
    spec: DesignSpec, sel: FaceSelectionSpec, instruction: str, bbox: dict | None
) -> tuple[DesignSpec, str, dict]:
    dims, holes = _spec_to_mm(spec)
    if not holes:
        raise FaceEditReview("This part has no editable holes.")
    i = _hole_index_from_spec(sel, spec)
    if not (0 <= i < len(holes)):
        raise FaceEditReview(f"Hole {i + 1} does not exist on this part.")
    dia = _first_number(instruction)
    if dia is None:
        raise FaceEditError("Tell me the new hole diameter, e.g. '8 mm'.")
    if dia <= 0:
        raise FaceEditError("Hole diameter must be greater than zero.")
    extent = _in_plane_extent_mm(bbox)
    if extent is not None and dia >= extent * 0.95:
        raise FaceEditError(
            f"A Ø{dia:g} mm hole is too large for the ~{extent:g} mm face."
        )
    holes[i].diameter = dia
    return _rebuild(spec, dims, holes), f"Resized hole {i + 1} to Ø{dia:g} mm", {"hole": i, "diameter_mm": dia}


def _handle_delete_hole(
    spec: DesignSpec, sel: FaceSelectionSpec
) -> tuple[DesignSpec, str, dict]:
    dims, holes = _spec_to_mm(spec)
    if not holes:
        raise FaceEditReview("This part has no holes to delete.")
    i = _hole_index_from_spec(sel, spec)
    if not (0 <= i < len(holes)):
        raise FaceEditReview(f"Hole {i + 1} does not exist on this part.")
    holes.pop(i)
    return _rebuild(spec, dims, holes), f"Deleted hole {i + 1}", {"hole": i}


def apply_face_edit(
    spec: DesignSpec, req: FaceLocalizedEditRequest, bbox: dict | None = None
) -> tuple[DesignSpec, FaceEditOutcome]:
    """Classify and apply a localized face edit.

    Returns ``(new_spec, outcome)`` on success. Raises ``FaceEditError`` for an
    invalid/unsafe edit or ``FaceEditReview`` for an unsupported one; in both
    cases the caller leaves the original design untouched.
    """
    op = classify_edit(req.quick_action, req.instruction, req.selection)
    frame = build_local_frame(req.selection)

    if op == "add_hole":
        new_spec, message, params = _handle_add_hole(spec, req.selection, req.instruction, bbox)
    elif op == "add_vent":
        new_spec, message, params = _handle_add_vent(spec, req.instruction)
    elif op == "fillet":
        new_spec, message, params = _handle_edge_treatment(spec, req.instruction, bbox, chamfer=False)
    elif op == "chamfer":
        new_spec, message, params = _handle_edge_treatment(spec, req.instruction, bbox, chamfer=True)
    elif op == "resize_hole":
        new_spec, message, params = _handle_resize_hole(spec, req.selection, req.instruction, bbox)
    elif op == "delete_hole":
        new_spec, message, params = _handle_delete_hole(spec, req.selection)
    else:  # unimplemented ops, unsupported selections, unclassified text
        raise FaceEditUnsupported(UNSUPPORTED_EDIT_MESSAGE, operation=op)

    return new_spec, FaceEditOutcome(op=op, message=message, params=params, local_frame=frame)


# --- feature-graph (CadPlan) face edits ------------------------------------
#
# CadPlan-built parts have no DesignSpec; they carry a stored parametric feature
# graph instead (semantic_json['cad_plan']). These handlers edit that graph and
# the part is recompiled by the same safe parametric pipeline — real CAD, never a
# mesh edit. Restricted to flat plate families where a Z-through hole / edge
# treatment is unambiguous and safe.

# CadPlan object_types that are clean flat plates (single plate/box base): a
# centered Z-through hole and vertical-edge treatment are well-defined on these.
# Kept as canonical names; free-form labels are matched via _plan_is_plate_like.
_PLAN_PLATE_TYPES = {
    "mounting_plate", "rectangular_bracket", "adapter_plate", "drill_jig", "plate",
}
# Feature kinds that can serve as the flat base of a plate.
_PLAN_BASE_KINDS = {"plate", "box", "extruded_profile"}
# Additive feature kinds that keep a body genuinely flat (safe to drill through).
# Anything else (pipe/cylinder/flange/boss/wall/shell/…) makes it not a plain plate.
_PLAN_FLAT_BODY_KINDS = {"plate", "box", "extruded_profile"}
# Normalized family tokens that must NEVER be treated as a drillable flat plate,
# even if a plate-shaped body sneaks into the graph (round/curved/threaded/tube/
# assembly parts). Substring match on the normalized family.
_NON_PLATE_TOKENS = (
    "tire", "wheel", "rim", "pipe", "tube", "flange", "elbow", "spool", "bolt",
    "nut", "screw", "stud", "rod", "gear", "pulley", "coupler", "standoff",
    "spacer", "bearing", "shaft", "crankshaft", "hinge", "enclosure", "casing",
    "housing", "cylinder", "sphere", "cone", "dome", "assembly", "boss", "hammer",
    "wrench", "handle", "knob", "hook",
)
# Normalized families explicitly recognised as plate-like (exact match).
_PLATE_LIKE_FAMILIES = {
    "mounting_plate", "adapter_plate", "adapter_mounting_plate", "flat_plate",
    "plate", "base_plate", "cover_plate", "top_plate", "bottom_plate",
    "rectangular_bracket", "bracket", "mounting_bracket", "flat_bracket",
    "drill_jig", "jig_plate", "gusset_plate", "spacer_plate",
}


def _normalize_family(name) -> str:
    """Lower-case a free-form family/label and unify separators (``/``, ``-``,
    spaces) to ``_`` so 'adapter/mounting plate', 'Adapter Plate' and
    'adapter_plate' all normalize to the same comparable token."""
    s = (name or "").strip().lower()
    for ch in ("/", "\\", "-", " ", ".", ","):
        s = s.replace(ch, "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


def _plan_base_plate(plan):
    """The additive flat-plate base feature of a plan, or None."""
    for f in plan.features:
        if not f.is_subtractive and f.kind.value in _PLAN_BASE_KINDS:
            return f
    return None


def _plan_all_bodies_flat(plan) -> bool:
    """True when every additive *body* in the graph is a flat plate/box/profile
    (modifiers like fillet/chamfer/mirror and subtractive cuts/holes are ignored),
    i.e. the part is a genuine flat plate — no walls, pipes, bosses or curved
    bodies that would make a Z-through hole unsafe or meaningless."""
    saw_body = False
    for f in plan.features:
        if f.is_subtractive:
            continue
        kind = f.kind.value
        if kind in ("fillet", "chamfer", "mirror", "union", "subtract"):
            continue  # modifiers don't add a body
        if kind not in _PLAN_FLAT_BODY_KINDS:
            return False
        saw_body = True
    return saw_body


def _plan_is_plate_like(plan) -> bool:
    """Whether this CadPlan part is a flat plate we can safely drill / edge-treat.

    Prefers a STRUCTURAL check over the display name: a genuine flat-plate feature
    graph (a plate/box base, all bodies flat) qualifies even when the LLM labelled
    it oddly (e.g. 'adapter/mounting plate'). A recognised plate family name also
    qualifies. Round/curved/threaded/tube/assembly families are always rejected,
    so this never opens hole-adding up to tires, pipes, bolts, etc."""
    fam = _normalize_family(getattr(plan, "object_type", ""))
    if any(tok in fam for tok in _NON_PLATE_TOKENS):
        return False
    if fam in _PLAN_PLATE_TYPES or fam in _PLATE_LIKE_FAMILIES:
        return True
    if "plate" in fam:  # any *_plate label (cover_plate, adapter_mounting_plate…)
        return True
    # Structural fallback: an unrecognised name but a genuinely flat plate graph.
    return _plan_base_plate(plan) is not None and _plan_all_bodies_flat(plan)


def _plan_hole_features(plan) -> list:
    """The plan's circular-hole features, in declaration order."""
    return [f for f in plan.features if f.kind.value == "hole"]


def _plan_unique_id(base: str, plan) -> str:
    existing = {f.id for f in plan.features}
    if base not in existing:
        return base
    i = 1
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def _plan_add_hole(plan, sel: FaceSelectionSpec, instruction: str, bbox: dict | None):
    from app.cad.plan.schema import Feature

    if not _plan_is_plate_like(plan):
        raise FaceEditReview(
            f"Adding a hole isn't supported on a '{plan.object_type}' yet — this "
            "works on flat plate / mounting-plate parts."
        )
    if sel.face_kind not in ("planar", "unknown"):
        raise FaceEditReview("Select a flat (planar) face to add a through hole.")
    face = sel.feature_id or sel.backend_face_id
    if face is not None and face not in _FLAT_FACES:
        raise FaceEditReview(
            "Through holes are added on the top or bottom face — select that face."
        )
    base = _plan_base_plate(plan)
    if base is None:
        raise FaceEditReview("Couldn't find a flat plate to drill on this part.")
    dia = _first_number(instruction)
    if dia is None:
        dia = 6.0
    if dia <= 0:
        raise FaceEditError("Hole diameter must be greater than zero.")
    extent = _in_plane_extent_mm(bbox)
    if extent is not None and dia >= extent * 0.95:
        raise FaceEditError(
            f"A Ø{dia:g} mm hole is too large for the ~{extent:g} mm face — it would "
            "remove the part."
        )
    bx, by = float(base.at[0]), float(base.at[1])  # centre of the plate footprint
    new_plan = plan.model_copy(deep=True)
    hid = _plan_unique_id("face_hole", new_plan)
    new_plan.features.append(
        Feature(
            id=hid, kind="hole", op="cut", through=True, axis="z",
            params={"diameter": dia}, at=[bx, by, 0.0],
            description="through hole added via face edit",
        )
    )
    params = {"diameter_mm": dia, "x_mm": bx, "y_mm": by}
    return new_plan, f"Added a Ø{dia:g} mm through hole centered on the face", params


def _plan_resize_hole(plan, sel: FaceSelectionSpec, instruction: str, bbox: dict | None):
    holes = _plan_hole_features(plan)
    if not holes:
        raise FaceEditReview("This part has no editable holes.")
    i = _hole_index_from_plan(sel, plan)
    if not (0 <= i < len(holes)):
        raise FaceEditReview(f"Hole {i + 1} does not exist on this part.")
    dia = _first_number(instruction)
    if dia is None:
        raise FaceEditError("Tell me the new hole diameter, e.g. '8 mm'.")
    if dia <= 0:
        raise FaceEditError("Hole diameter must be greater than zero.")
    extent = _in_plane_extent_mm(bbox)
    if extent is not None and dia >= extent * 0.95:
        raise FaceEditError(f"A Ø{dia:g} mm hole is too large for the ~{extent:g} mm face.")
    new_plan = plan.model_copy(deep=True)
    target = _plan_hole_features(new_plan)[i]
    target.params["diameter"] = dia
    return new_plan, f"Resized hole {i + 1} to Ø{dia:g} mm", {"hole": i, "diameter_mm": dia}


def _plan_delete_hole(plan, sel: FaceSelectionSpec):
    holes = _plan_hole_features(plan)
    if not holes:
        raise FaceEditReview("This part has no holes to delete.")
    i = _hole_index_from_plan(sel, plan)
    if not (0 <= i < len(holes)):
        raise FaceEditReview(f"Hole {i + 1} does not exist on this part.")
    target_id = holes[i].id
    new_plan = plan.model_copy(deep=True)
    new_plan.features = [f for f in new_plan.features if f.id != target_id]
    return new_plan, f"Deleted hole {i + 1}", {"hole": i}


def _plan_edge_treatment(plan, instruction: str, bbox: dict | None, chamfer: bool):
    from app.cad.plan.schema import Feature

    if not _plan_is_plate_like(plan):
        kind = "Chamfer" if chamfer else "Fillet"
        raise FaceEditReview(f"{kind} edits aren't supported on a '{plan.object_type}' yet.")
    size = _first_number(instruction)
    if size is None:
        size = 1.0 if chamfer else 2.0
    if size <= 0:
        raise FaceEditError(f"{'Chamfer' if chamfer else 'Fillet'} size must be greater than zero.")
    extent = _in_plane_extent_mm(bbox)
    if extent is not None and size >= extent * 0.5:
        raise FaceEditError(
            f"A {size:g} mm {'chamfer' if chamfer else 'fillet'} is too large for the "
            f"~{extent:g} mm part."
        )
    new_plan = plan.model_copy(deep=True)
    # One edge treatment at a time — drop any existing fillet/chamfer, mirroring
    # the DesignSpec pipeline (a spec carries a single fillet_radius/chamfer_size).
    new_plan.features = [f for f in new_plan.features if f.kind.value not in ("fillet", "chamfer")]
    if chamfer:
        cid = _plan_unique_id("edge_chamfer", new_plan)
        new_plan.features.append(
            Feature(id=cid, kind="chamfer", description="vertical edge chamfer",
                    params={"size": size})
        )
        return new_plan, f"Chamfered the part edges {size:g} mm", {"chamfer_mm": size}
    fid = _plan_unique_id("edge_fillet", new_plan)
    new_plan.features.append(
        Feature(id=fid, kind="fillet", description="rounded vertical edges",
                params={"radius": size})
    )
    return new_plan, f"Rounded the part edges with a {size:g} mm fillet", {"fillet_mm": size}


def apply_face_edit_to_plan(
    plan, req: FaceLocalizedEditRequest, bbox: dict | None = None
):
    """Classify and apply a localized face edit to a CadPlan feature graph.

    Mirrors :func:`apply_face_edit` for CadPlan-built (feature-graph) parts that
    have no DesignSpec. Returns ``(new_plan, FaceEditOutcome)`` on success; raises
    ``FaceEditError`` (invalid/unsafe) or ``FaceEditReview`` (unsupported) with the
    original plan left untouched."""
    op = classify_edit(req.quick_action, req.instruction, req.selection)
    frame = build_local_frame(req.selection)

    if op == "add_hole":
        new_plan, message, params = _plan_add_hole(plan, req.selection, req.instruction, bbox)
    elif op == "fillet":
        new_plan, message, params = _plan_edge_treatment(plan, req.instruction, bbox, chamfer=False)
    elif op == "chamfer":
        new_plan, message, params = _plan_edge_treatment(plan, req.instruction, bbox, chamfer=True)
    elif op == "resize_hole":
        new_plan, message, params = _plan_resize_hole(plan, req.selection, req.instruction, bbox)
    elif op == "delete_hole":
        new_plan, message, params = _plan_delete_hole(plan, req.selection)
    elif op == "add_vent":
        raise FaceEditUnsupported(
            "Ventilation slots are supported on enclosures, not flat plates.",
            operation=op,
        )
    else:  # unimplemented ops, unsupported selections, unclassified text
        raise FaceEditUnsupported(UNSUPPORTED_EDIT_MESSAGE, operation=op)

    return new_plan, FaceEditOutcome(op=op, message=message, params=params, local_frame=frame)
