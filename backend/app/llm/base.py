"""LLM provider abstraction.

A provider takes a natural-language prompt and returns a JSON-serializable dict
describing the part. It must NOT return executable code — only data that we
then validate against our strict schema. The shape it should return:

    {
      "object_type": "rectangular_bracket",
      "units": "mm",
      "manufacturing_method": "fdm_3d_print",
      "material": "PLA",
      "dimensions": {"width": 80, "depth": 40, "thickness": 5},
      "holes": [{"diameter": 6, "x": -25, "y": 0}],
      "fillet_radius": 3,
      "missing_required": [],
      "clarification_question": null,
      "assumptions": ["Assumed PLA / FDM printing"]
    }
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMUnavailableError(RuntimeError):
    """The configured LLM could not be reached or returned no usable result.

    Carries a short, user-safe message (no secrets, no stack internals). Raised
    after timeouts/retries and any model-fallback chain are exhausted so callers
    can surface a clean 503 instead of leaking a provider stack trace."""

SYSTEM_PROMPT = """You are a mechanical CAD intake assistant. Convert the user's \
plain-English request for a mechanical part into a STRICT JSON design spec. \
You only output data, never code.

Allowed object_type values: rectangular_bracket, l_bracket, enclosure, spacer, \
pipe_clamp, drill_jig, handle, adapter_plate, inline_4_crankshaft, \
flanged_pipe_branch, simple_gear_or_pulley.
Allowed units: mm, cm, inch (default mm).
Allowed manufacturing_method: fdm_3d_print, sla_3d_print, cnc_milling, laser_cut, \
sheet_metal (default fdm_3d_print).

GENERATE-FIRST POLICY (very important):
- Your job is to GENERATE a reasonable first draft, not to interrogate the user. \
Every template has sensible built-in defaults for unspecified details.
- Use defaults for any non-critical missing detail (material, hole count, hole \
pattern/start, hole spacing, lip size, fillet/chamfer, wall thickness, \
counterbore/countersink, bolt count/PCD, keyway, etc.) and list each guess in \
"assumptions". Do NOT put these in "missing_required".
- Leave "missing_required" EMPTY and "clarification_question" null unless one of \
these is true: (a) you cannot tell what kind of part it is, (b) the requested \
object is unsupported, or (c) the given dimensions are impossible/contradictory.
- A crankshaft / "inline four" / "4-cylinder" engine crankshaft maps to \
inline_4_crankshaft. A flanged pipe / pipe branch / pipe spool maps to \
flanged_pipe_branch. A gear / pulley / sprocket maps to simple_gear_or_pulley.

Rules:
- Choose the single best object_type for the request.
- Put named lengths in "dimensions" using the keys the chosen template expects; \
only include dimensions the user actually specified.
- Holes go in "holes" as objects with diameter, x, y in the part's local frame \
(origin at the center of the top face; +x right, +y up). A hole may set \
"hole_type" to "simple", "counterbore" (with counterbore_diameter, \
counterbore_depth) or "countersink" (with countersink_diameter).
- Convert screw callouts to clearance diameters (M3->3.4, M4->4.5, M5->5.5, \
M6->6.6) and note it in "assumptions".
- Optionally set "fillet_radius" OR "chamfer_size" (not both) to break edges.
- Respond with ONLY a single JSON object, no prose, no markdown fences.
"""

MODIFICATION_SYSTEM_PROMPT = """You edit an existing mechanical part. Given the \
current design spec (JSON) and a plain-English change request, output a STRICT \
JSON DesignModification — data only, never code. Available fields:
- "set_dimensions": {name: mm} absolute overrides
- "scale_dimensions": {name: factor} multiply current value (e.g. wider -> 1.25)
- "set_fillet_radius": mm (use for "rounded edges")
- "set_chamfer_size": mm (use for "chamfered/beveled edges")
- "hole_spread_factor": factor to move holes apart (>1) or together (<1)
- "set_material": string, "set_manufacturing_method": enum
- "clarification_question": ask if the request is too ambiguous to act on
- "summary": one short sentence describing what you changed
Only include fields you are changing. Respond with ONLY the JSON object.
"""


FEATURE_GRAPH_SYSTEM_PROMPT = """You design a mechanical part as a STRICT JSON \
CADFeatureGraph — data only, never code. Use ONLY these operations: box, \
cylinder, hex_prism, polygon_prism, cone, sphere, extrude_profile, \
revolve_profile, cut_hole, rectangular_cutout, circular_pattern, linear_pattern, \
boolean_union, boolean_cut, fillet, chamfer, translate, rotate, mirror.

Each operation has an "id". Primitives take "params" (numbers in mm) and an \
optional "at":[x,y,z]. Booleans take "target" and "tool" ids. cut_hole/ \
rectangular_cutout take a "target" id, "params", and "at". Patterns take a \
"source" id, "count", and "params". Transforms (translate/rotate/mirror) take a \
"source" id. Set "result_id" to the id of the final solid.

Build only safe, buildable mechanical geometry from these primitives. Do NOT \
invent operations. Respond with ONLY the JSON object.
"""


GENERAL_CAD_PLAN_SYSTEM_PROMPT = """You design a mechanical part as a STRICT JSON \
GeneralCADPlan — data only, never code. Fields: object_name, units, \
coordinate_system, overall_dimensions, primitives, holes, patterns, cuts, \
fillets, chamfers, annotations, assumptions, visual_notes, export_targets.

Each primitive has "kind" (box|cylinder|tube|hex_prism|polygon_prism|sphere| \
cone), an "id", numeric "params" (mm), an optional "at":[x,y,z], and "op" \
(union|subtract). Holes have diameter,x,y and optional depth. Build only safe, \
buildable mechanical geometry from these primitives. Record guesses in \
"assumptions". Respond with ONLY the JSON object.
"""


CAD_PLAN_SYSTEM_PROMPT = """You are an ASSUMPTION-FIRST mechanical CAD planner.
Your job is to GENERATE a manufacturable CAD feature graph from plain English —
not to interrogate the user. Output JSON only according to the supplied schema —
never prose, never code.

Do NOT ask clarification questions for secondary dimensions. INFER them with
sensible engineering defaults and list every inferred value in "assumptions".
Ask clarification (set clarification_required=true) ONLY when the part cannot be
generated at all: the prompt is non-mechanical, gives no usable scale and no safe
default applies, or the dimensions are contradictory/impossible. Always produce a
CadPlan otherwise.

Never choose a saved whole-part template. Represent geometry as primitive
features and boolean operations. Preserve all dimensions the user gives exactly.
All dimensions are millimeters unless the user explicitly says otherwise. Do not
reject a part just because object_type is unfamiliar — use
object_type="generic_mechanical_part".

For screw labels use CLEARANCE hole diameters unless the user asks for tapped
holes: M3=3.4, M4=4.5, M5=5.5, M6=6.6, M8=9.0, M10=11.0, M12=13.5.

CRITICAL — you MUST generate a CAD plan (never clarify) for these, even when some
secondary dimensions are missing; infer the rest and list assumptions:
  bearing block for an N mm shaft · hinge bracket with side ears · two-part /
  sensor enclosure · U bracket · L bracket · pipe spool · flanged tee · blind
  flange · adapter/mounting plate · motor mount plate.
Engineering defaults to infer when missing: plate thickness 6, fillet 2; bearing
boss OD = shaft·2.25, boss height = max(18, shaft·1.5), 4× M6 base holes inset
~18%/25%; hinge ears 6 thick × 30 tall, 8mm pin hole, base holes M5; enclosure
100×60×40, walls 2.5, removable back plate, sensor hole 18, 4× M4 back holes;
flange thickness = max(10, OD·0.12), PCD = OD·0.8, 8× bolts; pipe wall 5,
flange OD = pipe·1.5.

Every subtractive feature must state its target via ordering (cuts apply to the
running body) or op="cut". Every hole must include a diameter, through/blind
(through=true/false), and a position ("at":[x,y,z]). Every repeated pattern must
include count, spacing or PCD, and center. For pipe/flange parts distinguish OD,
bore/ID, wall thickness, flange OD, flange thickness, bolt count, bolt diameter
and PCD. Always fill "expected" (bbox_mm, hole_count, through_hole_count) so the
build can be verified.

CONSISTENCY (critical): "expected" must match the geometry you actually describe.
Set expected.bbox_mm to the part's TRUE overall extents across every feature
(measured from the requested dimensions, not larger), and expected.hole_count /
through_hole_count to the exact total number of holes your features create. A
straight pipe spool's overall length equals the requested length — keep both end
flanges within it; do not exceed the stated length.

FEATURE REFERENCE — each feature: {"id","kind","op":"add|cut","params":{...mm},
"at":[x,y,z],"axis":"x|y|z","through":bool,"description"}. Build plane is XY, Z up;
plates lie flat (thickness along Z); holes drill along their axis (default z).
  - plate/box: params width(x), depth(y), thickness|height(z)
  - cylinder/boss: params diameter|od, height; axis orients it
  - circular_flange: params od, thickness, pcd, bolt_count, bolt_diameter, bore
    (bore=0 for a blind flange). Builds a CIRCULAR disc + bolt circle — use this
    for any flange, never a rectangular plate.
  - pipe: params od, id|bore (or wall), length; axis along the run
  - pipe_spool: a straight pipe with a flange on EACH end — params length, od,
    id, flange_od, flange_thickness, bolt_count, bolt_diameter, pcd
  - rectangular_wall: an upright slab — params width(x), depth(y), height(z); place with "at"
  - rib / gusset: thin/triangular reinforcement
  - hole: params diameter; through=true for through-holes
  - hole_pattern_rect: params diameter, pattern (square side) or spacing_x/spacing_y, nx, ny
  - hole_pattern_circle: params diameter, pcd, count
  - slot, v_groove (params angle, depth), rectangular_cut, countersink, counterbore
  - fillet (params radius), chamfer (params size): description "vertical"/"top"/"all"
  - shell: params thickness; description "top"/"bottom" (open face)
Return assumptions explicitly. Respond with ONLY the JSON object.
"""


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def parse_prompt(self, prompt: str) -> dict:
        """Return a raw JSON-serializable dict describing the part."""
        raise NotImplementedError

    def repair(self, prompt: str, previous: dict, errors: str) -> dict | None:
        """Retry hook: given validation errors, return a corrected dict or None.

        Default: no repair available (used by the offline mock). Real providers
        re-prompt the model with the errors appended.
        """
        return None

    def parse_modification(self, prompt: str, current_spec: dict) -> dict:
        """Return a raw DesignModification dict for an edit request."""
        raise NotImplementedError

    def generate_clarification_question(self, prompt: str, errors: str = "") -> str | None:
        """Optional free-text clarification. Default None -> caller uses a
        deterministic fallback question."""
        return None

    def generate_explanation(self, spec: dict) -> str | None:
        """Optional free-text part explanation. Default None -> caller uses the
        deterministic explain() generator."""
        return None

    def plan_cad(self, prompt: str, feedback: str | None = None) -> dict | None:
        """Return a raw CadPlan dict (the plain-English → CAD feature graph), or
        None if this provider can't / shouldn't. Output is validated into a strict
        ``CadPlan`` and compiled by the deterministic CadQuery compiler — never
        executed as code. ``feedback`` carries structured validation failures for
        the one-shot repair pass."""
        return None

    def plan_feature_graph(self, prompt: str) -> dict | None:
        """Return a raw CADFeatureGraph dict for a buildable-but-non-template
        part, or None if this provider can't / shouldn't. Output is validated and
        compiled by the trusted interpreter — never executed as code."""
        return None

    def plan_general_cad(self, prompt: str) -> dict | None:
        """Return a raw GeneralCADPlan dict for the SCAD-generator route, or None.
        Data only — compiled by the backend, never executed as code."""
        return None

    def cad_program(self, prompt: str, feedback: str | None = None):
        """Return (CADDesignBrief, CADProgramSpec) for the CAD compiler, or None.
        The program's restricted code is sandbox-executed — never run in-process
        for untrusted providers. ``feedback`` carries semantic-verifier failures
        for the repair loop."""
        return None

    def interpret_drawing(
        self, image_b64: str, media_type: str = "image/png", hint: str | None = None
    ) -> dict:
        """Vision: interpret a 2D mechanical drawing image into a raw
        DrawingInterpretationSpec dict. ``hint`` is the user's optional
        'correct interpretation' note. Providers without vision raise."""
        raise NotImplementedError("This provider does not support drawing interpretation")


DRAWING_SYSTEM_PROMPT = """You are a mechanical drafting assistant performing \
Drawing-to-CAD ASSIST (not exact conversion). Read the uploaded 2D mechanical \
drawing and output a STRICT JSON DrawingInterpretationSpec — data only, never \
code. Identify the views present, the dimensions and hole callouts you can read, \
and the overall dimensions. Set suggested_object_type to the mechanical object \
you see (e.g. flanged_pipe_branch, pipe_tee, blind_flange, pipe_spool, \
bearing_block, hinge_bracket, u_bracket, l_bracket, mounting_plate, \
sensor_enclosure, …). You are NOT limited to a template list — if it is clearly \
a mechanical part but matches no named type, use \
suggested_object_type="generic_mechanical_part" rather than leaving it unknown. \
Only set unsupported_reason if it is not a mechanical part at all. Units default \
to mm unless the drawing clearly says inches; report numbers AS WRITTEN on the \
drawing (never rescale yourself) and set drawing_units_confidence honestly — \
the backend infers one consistent scale (e.g. cm-like values such as 14.8/Ø12 \
with a 12xØ1 callout) and records the assumption. ALWAYS extract repeated hole \
callouts like "12xØ1" or "8x M10" into holes[] with the exact count and the \
written diameter — on flanged parts these are PER-FLANGE bolt circles. Read \
pipe wall thickness and flange thickness from section views when present \
(wall_thickness_mm, flange_thickness_mm), and put the flange outer diameter and \
overall height into overall_dimensions. CRITICAL: do not invent dimensions. If \
a value is illegible or missing, lower its confidence, record an assumption, \
and when a build-critical dimension is missing add a clarification_question \
instead of guessing. Respond with ONLY the JSON object.
"""
