"""Strict CadPlan schema — the contract between the (untrusted) LLM and the
(trusted) CadQuery compiler.

Design rules that fix the bugs in the old template-first system:

* Machine fields are SHORT enums (``kind``, ``op``, ``axis``). Human-readable
  text lives in a SEPARATE ``description`` field with a generous limit, so a long
  natural-language string can never overflow an enum-like field (the bug that
  crashed "bearing block").
* No whole-part template is ever selected. A part is composed from primitive
  features and boolean operations.
* ``object_type`` is a free string (default ``generic_mechanical_part``) — an
  unfamiliar part is never rejected just because the type is unknown.

Everything is re-validated here (Pydantic) before any geometry is built; the
compiler only reads numeric params from a fixed whitelist of ``kind`` values.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.coerce import coerce_float_map, to_float

_MAX_DIM = 5000.0  # mm — generous upper bound; rejects absurd/poisoned values


class FeatureKind(str, Enum):
    # --- additive primitives ---
    box = "box"
    plate = "plate"
    cylinder = "cylinder"
    circular_flange = "circular_flange"  # disc + bolt circle (+ optional bore)
    pipe = "pipe"                        # hollow tube
    pipe_spool = "pipe_spool"            # pipe + a flange on each end
    pipe_elbow = "pipe_elbow"            # two pipes joined at an angle
    rectangular_wall = "rectangular_wall"
    boss = "boss"                        # raised cylindrical pad
    rib = "rib"                          # thin reinforcing web
    gusset = "gusset"                    # triangular reinforcing web
    shell = "shell"                      # hollow out a body (enclosure)
    # --- subtractive features ---
    hole = "hole"
    hole_pattern_rect = "hole_pattern_rect"
    hole_pattern_circle = "hole_pattern_circle"
    slot = "slot"
    v_groove = "v_groove"
    rectangular_cut = "rectangular_cut"
    countersink = "countersink"
    counterbore = "counterbore"
    # --- modifiers / booleans ---
    fillet = "fillet"
    chamfer = "chamfer"
    mirror = "mirror"
    union = "union"
    subtract = "subtract"


# Features that remove material from the running solid (also when op == "cut").
SUBTRACTIVE_KINDS = {
    FeatureKind.hole, FeatureKind.hole_pattern_rect, FeatureKind.hole_pattern_circle,
    FeatureKind.slot, FeatureKind.v_groove, FeatureKind.rectangular_cut,
    FeatureKind.countersink, FeatureKind.counterbore, FeatureKind.subtract,
}
# Features that count toward hole_count / through_hole_count.
HOLE_KINDS = {
    FeatureKind.hole, FeatureKind.hole_pattern_rect, FeatureKind.hole_pattern_circle,
    FeatureKind.countersink, FeatureKind.counterbore,
}
MODIFIER_KINDS = {FeatureKind.fillet, FeatureKind.chamfer, FeatureKind.mirror}


class Feature(BaseModel):
    """One node in the feature graph.

    ``params`` is an open numeric map (width/depth/height/diameter/od/id/pcd/
    bolt_count/count/spacing/angle/length…) so the schema flexes across part
    families without a brittle per-kind union; the compiler reads only the
    numeric keys each ``kind`` understands. Stringy numbers ("Ø12", "approx 40")
    are coerced; unparseable values are dropped (never fatal).
    """

    id: str = Field(max_length=48)
    kind: FeatureKind
    op: str = Field(default="add")  # "add" | "cut"
    params: dict[str, float] = Field(default_factory=dict)
    at: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    axis: str = Field(default="z", max_length=4)  # z | x | y (primary axis)
    through: bool = True  # holes default to through-holes unless told otherwise
    target: Optional[str] = Field(default=None, max_length=48)  # for cut/mirror refs
    description: str = Field(default="", max_length=200)  # human text only

    @field_validator("params", mode="before")
    @classmethod
    def _coerce_params(cls, v):
        return coerce_float_map(v)

    @field_validator("at", mode="before")
    @classmethod
    def _coerce_at(cls, v):
        if not isinstance(v, (list, tuple)):
            return [0.0, 0.0, 0.0]
        out = [to_float(x) or 0.0 for x in v][:3]
        while len(out) < 3:
            out.append(0.0)
        return out

    @field_validator("op", mode="before")
    @classmethod
    def _norm_op(cls, v):
        s = str(v or "add").strip().lower()
        return "cut" if s in ("cut", "subtract", "remove") else "add"

    @field_validator("axis", mode="before")
    @classmethod
    def _norm_axis(cls, v):
        s = str(v or "z").strip().lower()
        return s if s in ("x", "y", "z") else "z"

    def p(self, key: str, default: float = 0.0, *aliases: str) -> float:
        """Read a numeric param by primary key or any alias; clamped to range."""
        for k in (key, *aliases):
            if k in self.params:
                val = float(self.params[k])
                if val != val:  # NaN
                    continue
                return max(-_MAX_DIM, min(_MAX_DIM, val))
        return default

    @property
    def is_subtractive(self) -> bool:
        return self.op == "cut" or self.kind in SUBTRACTIVE_KINDS


class Operation(BaseModel):
    """Explicit boolean combine of two named solids (optional — most plans use
    per-feature add/cut ordering instead)."""

    op: str = Field(max_length=12)  # union | subtract | mirror
    id: str = Field(max_length=48)
    target: str = Field(max_length=48)
    tool: Optional[str] = Field(default=None, max_length=48)
    plane: Optional[str] = Field(default=None, max_length=4)  # for mirror

    @field_validator("op", mode="before")
    @classmethod
    def _norm(cls, v):
        s = str(v or "union").strip().lower()
        return s if s in ("union", "subtract", "mirror") else "union"


class Expected(BaseModel):
    """What the compiled model should satisfy — used by validation + repair.

    These are USER-INTENT counts (what the part is meant to have), tracked
    separately from raw mesh topology. They are advisory: a mismatch is a
    warning, never an export blocker (a coaxial pin through two ears is one
    intent ``pin_hole`` but two physical openings, etc.).
    """

    bbox_mm: Optional[dict[str, float]] = None  # {x, y, z}
    hole_count: Optional[int] = None
    through_hole_count: Optional[int] = None
    # Intent counts (advisory metadata, not topology).
    mounting_hole_count: Optional[int] = None
    pin_hole_count: Optional[int] = None
    flange_count: Optional[int] = None
    boss_count: Optional[int] = None
    export_formats: list[str] = Field(default_factory=lambda: ["step", "stl"])

    @field_validator("bbox_mm", mode="before")
    @classmethod
    def _coerce_bbox(cls, v):
        if v in (None, {}):
            return None
        m = coerce_float_map(v)
        return m or None


class CadPlan(BaseModel):
    """A complete, strict, parametric description of one mechanical part."""

    units: str = Field(default="mm", max_length=8)
    object_type: str = Field(default="generic_mechanical_part", max_length=64)
    name: str = Field(default="part", max_length=120)
    assumptions: list[str] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    material: Optional[str] = Field(default=None, max_length=64)
    stock: Optional[str] = Field(default=None, max_length=120)
    features: list[Feature] = Field(default_factory=list, max_length=200)
    operations: list[Operation] = Field(default_factory=list, max_length=200)
    expected: Expected = Field(default_factory=Expected)

    def is_buildable(self) -> bool:
        return bool(self.features) and not self.clarification_required
