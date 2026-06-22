"""CAD capability and family registry.

This is the *honest, central catalog* of which CAD part/assembly families
SourceCAD can generate, how mature each one is, what each needs from the user,
and what it deliberately does NOT promise. It is a metadata layer — it never
generates geometry itself — so the rest of the system (prompt classification,
the capability endpoint, the benchmark, docs) has one source of truth instead of
keyword tables scattered across the codebase.

Design goals:
  * Extensible — adding a family is one ``CADFamily(...)`` entry, not a rewrite.
  * Measurable — every production-ready family must have a golden prompt + tests.
  * Honest — ``maturity`` and ``known_limitations`` make no fake claims. A
    "concept" assembly is labelled concept; an approximate gear says so.

Nothing here executes user code. Families only point at the *names* of trusted,
deterministic generators that already exist in the codebase (templates, the
CadPlan feature-graph compiler, or the concept-assembly builders).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DesignMode(str, Enum):
    single_part = "single_part"
    assembly = "assembly"


class Maturity(str, Enum):
    production_ready = "production_ready"  # validated, dimension-checked, exportable
    beta = "beta"                          # generates real CAD; less coverage / fewer checks
    concept = "concept"                    # plausible concept geometry, not certified
    unsupported = "unsupported"            # routed to guidance/decomposition, no geometry


class GenerationStrategy(str, Enum):
    """How a prompt in this family is actually built (mirrors the real router)."""
    cadplan = "cadplan"                            # plain English -> CadPlan feature graph
    deterministic_template = "deterministic_template"  # strong parametric template match
    assembly_generator = "assembly_generator"      # concept-assembly builder (chassis)
    needs_clarification = "needs_clarification"    # missing critical info / ambiguous
    needs_decomposition = "needs_decomposition"    # large multi-part assembly -> split up
    unsupported = "unsupported"                    # cannot map to safe geometry


# Which export formats a family can honestly produce. Concept assemblies and
# templates/feature-graph parts all currently export STEP + STL; decomposition
# guidance exports nothing.
EXPORT_PART = ["step", "stl"]
EXPORT_NONE: list[str] = []


@dataclass(frozen=True)
class CADFamily:
    family_id: str
    display_name: str
    design_mode: DesignMode
    maturity: Maturity
    # Plain-English keywords / phrases that point at this family. Used by the
    # classifier as a coarse signal (the real router still decides geometry).
    keywords: tuple[str, ...]
    # Internal generator object_type ids this family covers (reverse-lookup for
    # the classifier). Empty for guidance-only families.
    object_types: tuple[str, ...]
    required_dimensions: tuple[str, ...]
    optional_dimensions: tuple[str, ...]
    default_assumptions: tuple[str, ...]
    # Name of the generator route this family resolves to (documentation only).
    generator: str
    generation_strategy: GenerationStrategy
    validation_profile: str
    export_policy: list[str]
    known_limitations: tuple[str, ...]
    example_prompts: tuple[str, ...]
    # Whether a 2D drawing / image can meaningfully drive this family today.
    supports_drawing_input: bool = False

    def to_dict(self) -> dict:
        return {
            "family_id": self.family_id,
            "display_name": self.display_name,
            "design_mode": self.design_mode.value,
            "maturity": self.maturity.value,
            "keywords": list(self.keywords),
            "object_types": list(self.object_types),
            "required_dimensions": list(self.required_dimensions),
            "optional_dimensions": list(self.optional_dimensions),
            "default_assumptions": list(self.default_assumptions),
            "generator": self.generator,
            "generation_strategy": self.generation_strategy.value,
            "validation_profile": self.validation_profile,
            "export_policy": list(self.export_policy),
            "known_limitations": list(self.known_limitations),
            "example_prompts": list(self.example_prompts),
            "supports_drawing_input": self.supports_drawing_input,
        }


# Generic fallbacks. These are real families (parts still build) but cover the
# "no strong family matched" case so the classifier always returns something.
GENERIC_PART_FAMILY = "generic_feature_graph_part"
GENERIC_ASSEMBLY_FAMILY = "generic_assembly_decomposition"


_FAMILIES: tuple[CADFamily, ...] = (
    # ---- Production-ready single parts -----------------------------------
    CADFamily(
        family_id="mounting_plate",
        display_name="Mounting / adapter plate",
        design_mode=DesignMode.single_part,
        maturity=Maturity.production_ready,
        keywords=("mounting plate", "mounting bracket", "base plate", "motor plate",
                  "nema 17", "nema17", "plate with holes"),
        object_types=("rectangular_bracket",),
        required_dimensions=("width", "depth/height", "thickness"),
        optional_dimensions=("hole diameter", "hole pattern", "fillet radius",
                             "counterbore/countersink"),
        default_assumptions=("Through holes unless a depth is given",
                             "Square corners unless a fillet is requested"),
        generator="rectangular_bracket / drill_jig template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Flat plate geometry; not for curved or organic shells.",
            "Hole patterns are rectangular/circular grids, not arbitrary CAM paths.",
        ),
        example_prompts=(
            "A rectangular mounting plate 80mm x 40mm x 5mm with two 6mm holes",
            "A NEMA 17 motor plate 60mm square, 6mm thick, with a 22mm center bore "
            "and four M3 holes on a 31mm square pattern",
        ),
        supports_drawing_input=True,
    ),
    CADFamily(
        family_id="spacer",
        display_name="Spacer / standoff / bushing",
        design_mode=DesignMode.single_part,
        maturity=Maturity.production_ready,
        keywords=("spacer", "standoff", "stand-off", "bushing", "bush", "sleeve"),
        object_types=("spacer",),
        required_dimensions=("outer diameter", "length/height"),
        optional_dimensions=("bore diameter", "hex across-flats"),
        default_assumptions=("Through bore unless specified solid",
                             "Round body unless 'hex' is requested"),
        generator="spacer template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Plain round or hex bodies only — no threaded standoffs (threads are "
            "not modelled).",
        ),
        example_prompts=(
            "A spacer 10mm OD, 5mm bore, 12mm long",
            "A hex standoff 8mm across flats, 20mm long, with an M4 through bore",
        ),
    ),
    CADFamily(
        family_id="l_bracket",
        display_name="L bracket / angle bracket",
        design_mode=DesignMode.single_part,
        maturity=Maturity.production_ready,
        keywords=("l bracket", "l-bracket", "angle bracket", "right angle bracket",
                  "right-angle bracket", "corner bracket"),
        object_types=("l_bracket",),
        required_dimensions=("leg length", "thickness", "width"),
        optional_dimensions=("holes per face", "hole diameter", "fillet radius",
                             "gusset"),
        default_assumptions=("Equal legs unless two lengths are given",
                             "One hole per face unless a count is given"),
        generator="l_bracket template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Two-flange right-angle geometry only; not multi-bend sheet-metal "
            "with arbitrary flanges.",
        ),
        example_prompts=(
            "An L bracket with 60mm legs, 5mm thick, 20mm wide, two 6mm holes per face",
            "A right angle bracket 40x40mm legs, 4mm thick with a corner gusset",
        ),
        supports_drawing_input=True,
    ),
    # ---- Beta single parts ----------------------------------------------
    CADFamily(
        family_id="flange",
        display_name="Flange / adapter / transition plate",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("flange", "blind flange", "adapter plate", "transition plate",
                  "bolt circle", "circular flange"),
        object_types=("adapter_plate",),
        required_dimensions=("outer diameter / size", "thickness"),
        optional_dimensions=("center bore", "bolt circle diameter", "bolt count",
                             "bolt hole diameter"),
        default_assumptions=("Bolt holes equally spaced on the bolt circle",
                             "Blind (no bore) unless a center bore is given"),
        generator="adapter_plate template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Raised faces, gasket grooves and pipe-standard (ANSI/DIN) tables are "
            "not encoded — dimensions come from the prompt, not a flange standard.",
        ),
        example_prompts=(
            "A blind flange 100mm OD, 10mm thick, with six 9mm bolt holes on an 80mm "
            "bolt circle",
            "An adapter plate 120mm diameter, 8mm thick, 40mm center bore, four M6 "
            "holes on a 90mm circle",
        ),
    ),
    CADFamily(
        family_id="enclosure",
        display_name="Enclosure / project box",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("enclosure", "project box", "electronics box", "case", "housing",
                  "lid", "shell box"),
        object_types=("enclosure",),
        required_dimensions=("outer width", "outer depth", "outer height"),
        optional_dimensions=("wall thickness", "lid", "screw bosses", "fillet radius"),
        default_assumptions=("Open-top shelled box with default wall thickness",
                             "Rounded vertical edges"),
        generator="enclosure template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Single-cavity shelled box; no internal ribs, snap-fits, connectors, "
            "or vents beyond simple bosses.",
        ),
        example_prompts=(
            "An electronics enclosure 100x60x30mm with 2mm walls and four corner "
            "screw bosses",
            "A project box 80x50x25mm with a recessed lid",
        ),
    ),
    CADFamily(
        family_id="pipe_fitting",
        display_name="Pipe fitting / spool / clamp",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("pipe spool", "pipe tee", "pipe branch", "flanged pipe", "pipe fitting",
                  "branch pipe", "pipe clamp", "tube clamp", "hose clamp", "saddle clamp"),
        object_types=("flanged_pipe_branch", "pipe_clamp"),
        required_dimensions=("pipe outer diameter", "length"),
        optional_dimensions=("wall thickness / bore", "branch size", "flange size",
                             "clamp width"),
        default_assumptions=("Straight run unless a tee/branch is requested",
                             "Concentric bore"),
        generator="flanged_pipe_branch / pipe_clamp template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Geometry only — no pressure rating, schedule lookup, or thread/NPT "
            "modelling. Elbows beyond simple branches route to the feature graph.",
        ),
        example_prompts=(
            "A straight pipe spool 50mm OD, 3mm wall, 200mm long with flanges both ends",
            "A pipe clamp / saddle for 32mm OD tube, 20mm wide, with two M5 holes",
        ),
    ),
    CADFamily(
        family_id="drill_jig",
        display_name="Drill jig / drilling template",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("drill jig", "drilling template", "drill guide", "jig"),
        object_types=("drill_jig",),
        required_dimensions=("width", "depth", "thickness"),
        optional_dimensions=("guide hole diameter", "hole pattern"),
        default_assumptions=("Through guide holes",),
        generator="drill_jig template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "No hardened drill bushings modelled — guide holes are plain bores.",
        ),
        example_prompts=(
            "A drill jig 120x40x8mm with five 5mm guide holes spaced 20mm apart",
        ),
    ),
    CADFamily(
        family_id="handle_knob",
        display_name="Handle / knob / grip",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("handle", "knob", "grip"),
        object_types=("handle",),
        required_dimensions=("overall length / diameter",),
        optional_dimensions=("bore", "mounting holes"),
        default_assumptions=("Solid grip with a mounting bore",),
        generator="handle template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Simple revolved/extruded grips — not ergonomic free-form surfaces.",
        ),
        example_prompts=(
            "A cylindrical knob 30mm diameter, 25mm tall, with an M6 center bore",
        ),
    ),
    # ---- Concept / approximate single parts -----------------------------
    CADFamily(
        family_id="gear_blank",
        display_name="Gear blank / pulley (approximate)",
        design_mode=DesignMode.single_part,
        maturity=Maturity.concept,
        keywords=("gear", "pulley", "sprocket", "cog", "timing pulley"),
        object_types=("simple_gear_or_pulley",),
        required_dimensions=("outer/pitch diameter", "thickness"),
        optional_dimensions=("tooth count", "bore", "hub"),
        default_assumptions=("Center bore",),
        generator="simple_gear_or_pulley template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_relaxed",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Tooth geometry is APPROXIMATE — not a true involute profile. Use as a "
            "blank/visual concept, not a meshing power-transmission gear.",
            "No module/pressure-angle standard is enforced.",
        ),
        example_prompts=(
            "A spur gear blank 40mm pitch diameter, 8mm thick, 20 teeth, 6mm bore",
        ),
    ),
    CADFamily(
        family_id="crankshaft",
        display_name="Inline-4 crankshaft (advanced template)",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("crankshaft", "crank shaft", "inline-4", "inline 4", "four cylinder",
                  "four-cylinder", "4-cylinder"),
        object_types=("inline_4_crankshaft",),
        required_dimensions=("journal diameter", "stroke", "overall length"),
        optional_dimensions=("counterweights", "web thickness"),
        default_assumptions=("Standard inline-4 throw spacing",),
        generator="inline_4_crankshaft template (via CadPlan)",
        generation_strategy=GenerationStrategy.deterministic_template,
        validation_profile="single_part_relaxed",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Geometric model only — no balancing, fillet-stress, or oil-gallery "
            "detail. Not an engineering-validated crankshaft.",
        ),
        example_prompts=(
            "An inline-4 crankshaft, 50mm main journal, 80mm stroke, 400mm long",
        ),
    ),
    # ---- Dedicated medium single-part families (feature graph) ----------
    CADFamily(
        family_id="u_bracket",
        display_name="U bracket / channel bracket",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("u bracket", "u-bracket", "u shaped bracket", "u-shaped bracket",
                  "channel bracket"),
        object_types=("u_bracket",),
        required_dimensions=("width", "height", "thickness"),
        optional_dimensions=("depth", "base holes", "pivot hole"),
        default_assumptions=("Base plate + two upright side walls (a true U channel)",
                             "Pivot hole through each side wall"),
        generator="u_bracket feature graph (deterministic plan)",
        generation_strategy=GenerationStrategy.cadplan,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Right-angle U channel (base + two walls); not multi-bend sheet metal.",
        ),
        example_prompts=(
            "A U bracket 80mm wide, 60mm tall, 6mm thick with two M6 base holes and "
            "an 8mm pivot hole through each side wall",
        ),
        supports_drawing_input=True,
    ),
    CADFamily(
        family_id="hinge_bracket",
        display_name="Hinge bracket",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("hinge bracket", "hinge", "knuckle bracket"),
        object_types=("hinge_bracket",),
        required_dimensions=("base width", "base depth", "base thickness"),
        optional_dimensions=("ear height", "ear thickness", "pin hole diameter"),
        default_assumptions=("Base plate + two side ears on top",
                             "One coaxial pin hole through both ears"),
        generator="hinge_bracket feature graph (deterministic plan)",
        generation_strategy=GenerationStrategy.cadplan,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Two-ear knuckle with a single pin axis; no leaf/barrel hinge detail.",
        ),
        example_prompts=(
            "A hinge bracket with a 70x40x6mm base and two side ears 30mm tall, 6mm "
            "thick, with an 8mm pin hole through both ears",
        ),
    ),
    CADFamily(
        family_id="clamp_block",
        display_name="Tube / pipe clamp block",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("clamp block", "split clamp", "tube clamp block", "pipe clamp block",
                  "shaft clamp"),
        object_types=("tube_clamp_block",),
        required_dimensions=("tube/bore diameter",),
        optional_dimensions=("bolt count", "bolt size", "base mounting holes"),
        default_assumptions=("Clamp body sized from the tube bore + flat mounting base",
                             "Vertical split with tightening bolts crossing the gap"),
        generator="tube_clamp_block feature graph (deterministic plan)",
        generation_strategy=GenerationStrategy.cadplan,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Single horizontal bore split clamp; no threaded bore or V-block.",
        ),
        example_prompts=(
            "A clamp block for a 25mm round tube with two M6 tightening bolts and "
            "four base mounting holes",
        ),
    ),
    CADFamily(
        family_id="robotic_arm_base_bracket",
        display_name="Robotic arm base bracket",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=("robotic arm base", "robot arm base", "arm base bracket",
                  "robotic arm bracket", "robot base bracket"),
        object_types=("robotic_arm_base_bracket",),
        required_dimensions=("base size/diameter", "tower height"),
        optional_dimensions=("base holes", "bearing pocket", "tower width"),
        default_assumptions=("Circular or rectangular base + vertical support tower",
                             "Two side gussets bracing the tower to the base"),
        generator="robotic_arm_base_bracket feature graph (deterministic plan)",
        generation_strategy=GenerationStrategy.cadplan,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Base + tower + gussets (+ optional bearing pocket); not a full "
            "articulated joint or gearbox housing.",
            "Bearing pocket is a plain counterbored recess, not a toleranced seat.",
        ),
        example_prompts=(
            "A robotic arm base bracket with a 140mm circular base, a 100mm vertical "
            "support tower, two side gussets, six M6 base holes and a 52mm bearing pocket",
        ),
    ),
    # ---- Generic single-part fallback (feature graph) -------------------
    CADFamily(
        family_id=GENERIC_PART_FAMILY,
        display_name="General mechanical part (feature graph)",
        design_mode=DesignMode.single_part,
        maturity=Maturity.beta,
        keywords=(),  # matched only as a fallback
        object_types=("feature_graph",),
        required_dimensions=("overall size",),
        optional_dimensions=("holes", "bores", "slots", "fillets", "chamfers"),
        default_assumptions=("Composed from safe parametric primitives",),
        generator="CadPlan feature-graph compiler",
        generation_strategy=GenerationStrategy.cadplan,
        validation_profile="single_part_strict",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Built from box/cylinder/tube/boss/rib/hole primitives and booleans. "
            "Shapes outside this primitive set may be approximated or ask for "
            "clarification.",
            "No threads, gear teeth, splines or free-form surfaces.",
        ),
        example_prompts=(
            "A bearing block 60x40x30mm with a 20mm horizontal bore and two M5 "
            "mounting holes",
            "A shaft collar 30mm OD, 12mm bore, 10mm wide with an M4 set screw hole",
        ),
    ),
    # ---- Concept structural-frame & vehicle assemblies ------------------
    CADFamily(
        family_id="machine_frame",
        display_name="Welded machine frame (concept)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.concept,
        keywords=("machine frame", "equipment frame", "workbench frame",
                  "welded steel frame"),
        object_types=("machine_frame",),
        required_dimensions=("length", "width", "height"),
        optional_dimensions=("square tube size", "mounting plates", "panels",
                             "diagonal braces"),
        default_assumptions=("Four vertical legs + top & bottom rectangular frames",
                             "Square tubing exported as solid beams; wall is metadata"),
        generator="cad.assembly.frames.build_machine_frame",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="structural_frame_assembly",
        export_policy=EXPORT_PART,
        known_limitations=(
            "CONCEPT CAD — not FEA analyzed, not structurally certified; requires "
            "engineering review before fabrication.",
            "Square tubing exported as solid beams; wall thickness is cut-list metadata.",
            "Joints are idealized — no weld prep or load-driven sizing.",
        ),
        example_prompts=(
            "A welded steel machine frame 1200mm long, 800mm wide, 900mm tall using "
            "40mm square tubing with four legs, top and bottom frames, diagonal "
            "braces, leveling foot plates, a motor mounting plate and an electronics panel",
        ),
    ),
    CADFamily(
        family_id="engine_test_stand",
        display_name="Engine test stand (concept)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.concept,
        keywords=("engine test stand", "test stand", "engine stand"),
        object_types=("engine_test_stand",),
        required_dimensions=("length", "width", "height"),
        optional_dimensions=("square tube size", "engine plates", "crossbar",
                             "radiator mount", "caster plates", "fuel tank tray"),
        default_assumptions=("Square-tube frame with engine mount plates + caster plates",
                             "Adjustable crossbar position is a concept placeholder"),
        generator="cad.assembly.frames.build_engine_test_stand",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="structural_frame_assembly",
        export_policy=EXPORT_PART,
        known_limitations=(
            "CONCEPT CAD — not FEA analyzed or load-rated; requires engineering "
            "review before fabrication.",
            "Square tubing exported as solid beams; wall thickness is cut-list metadata.",
        ),
        example_prompts=(
            "A compact engine test stand 1000mm long, 700mm wide, 800mm tall using "
            "40mm square steel tubing with engine mounting plates, an adjustable "
            "crossbar, radiator mount, caster wheel plates, fuel tank tray and braces",
        ),
    ),
    CADFamily(
        family_id="drone_frame",
        display_name="Quadcopter drone frame (concept)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.concept,
        keywords=("drone frame", "quadcopter", "quadrotor", "quad copter", "drone"),
        object_types=("drone_frame",),
        required_dimensions=("motor-to-motor diagonal",),
        optional_dimensions=("arm section", "central plate size", "battery plate"),
        default_assumptions=("Four arms in an X layout with a central plate",
                             "Motor hole pattern at each arm end"),
        generator="cad.assembly.frames.build_drone_frame",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="drone_frame",
        export_policy=EXPORT_PART,
        known_limitations=(
            "CONCEPT CAD — not flight-tested, stress-analyzed, or certified.",
            "Carbon-fiber arms represented as solid beams (no layup/section detail).",
        ),
        example_prompts=(
            "A quadcopter drone frame with 450mm motor-to-motor diagonal, four "
            "carbon-fiber arms, a central electronics plate, battery plate, landing "
            "feet and motor mounting holes on each arm",
        ),
    ),
    CADFamily(
        family_id="motorcycle_subframe",
        display_name="Motorcycle rear subframe (concept)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.concept,
        keywords=("motorcycle rear subframe", "motorcycle subframe", "rear subframe"),
        object_types=("motorcycle_subframe",),
        required_dimensions=("length", "width", "height"),
        optional_dimensions=("tube OD", "seat rails", "shock tabs", "battery tray",
                             "side-panel tabs"),
        default_assumptions=("Two tapered tube rails + seat rails + triangulated bracing",
                             "Round tube exported as solid cylinders; wall is metadata"),
        generator="cad.assembly.frames.build_motorcycle_subframe",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="motorcycle_subframe",
        export_policy=EXPORT_PART,
        known_limitations=(
            "CONCEPT CAD — not FEA analyzed or homologated; requires engineering "
            "review before fabrication.",
            "Round tube exported as solid cylinders; wall thickness is cut-list metadata.",
        ),
        example_prompts=(
            "A motorcycle rear subframe concept 850mm long, 350mm wide, 450mm high "
            "using 25mm steel tubes with seat rails, rear shock mount tabs, a "
            "tail-light bracket, battery tray, side-panel tabs and triangulated bracing",
        ),
    ),
    CADFamily(
        family_id="skateboard_motor_mount",
        display_name="E-skateboard motor mount bracket (concept)",
        design_mode=DesignMode.single_part,
        maturity=Maturity.concept,
        keywords=("electric skateboard", "skateboard motor mount", "skateboard",
                  "longboard"),
        object_types=("skateboard_motor_mount",),
        required_dimensions=("hanger diameter",),
        optional_dimensions=("motor bolt pattern", "base mounting holes"),
        default_assumptions=("Primary component (motor mount bracket) of a larger "
                             "deck assembly request",),
        generator="cad.assembly.frames.build_skateboard_motor_mount",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="motor_mount_component",
        export_policy=EXPORT_PART,
        known_limitations=(
            "Generates the PRIMARY motor mount bracket only — the full deck/truck "
            "assembly is decomposed into separate components.",
            "CONCEPT CAD — not load-tested or certified.",
        ),
        example_prompts=(
            "An electric skateboard motor mount bracket for a 12mm truck hanger with "
            "a 4-hole motor pattern (generated as the primary component of a deck assembly)",
        ),
    ),
    # ---- Concept tubular-chassis assemblies -----------------------------
    CADFamily(
        family_id="tube_chassis",
        display_name="Tubular chassis / space frame (concept)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.concept,
        keywords=("tubular chassis", "tube chassis", "space frame", "spaceframe",
                  "chassis", "tubular frame", "welded tube frame"),
        object_types=("tubular_chassis_assembly",),
        required_dimensions=("overall length", "width", "height"),
        optional_dimensions=("tube outer diameter", "wall thickness", "wheelbase"),
        default_assumptions=("Round tubes exported as solid cylinders",
                             "Wall thickness carried as cut-list metadata"),
        generator="cad.assembly.chassis.build_chassis (detailed layout)",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="assembly_concept",
        export_policy=EXPORT_PART,
        known_limitations=(
            "CONCEPT CAD — not FEA-certified or structurally validated.",
            "Tubes export as solid cylinders; wall thickness is metadata only.",
            "Node joints are idealized; no weld prep, gussets-by-load, or "
            "triangulation optimization.",
        ),
        example_prompts=(
            "A tubular chassis 2000mm long, 1200mm wide, 1000mm tall",
            "A welded tube space frame for a small single-seater",
        ),
    ),
    CADFamily(
        family_id="reference_buggy_tubular_chassis",
        display_name="Reference buggy / sports-car tubular chassis (concept)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.concept,
        keywords=("buggy", "sports car chassis", "roll cage", "rollcage",
                  "reference chassis", "detailed tubular chassis", "welded steel tubular"),
        object_types=("tubular_chassis_assembly",),
        required_dimensions=("overall length", "width", "height"),
        optional_dimensions=("tube outer diameter", "wall thickness", "wheelbase",
                             "roll cage", "suspension mounts"),
        default_assumptions=("Reference-grade buggy zone layout (front / engine bay / "
                             "cabin / roll cage / rear)",),
        generator="cad.assembly.chassis.build_chassis (reference buggy layout)",
        generation_strategy=GenerationStrategy.assembly_generator,
        validation_profile="assembly_concept",
        export_policy=EXPORT_PART,
        known_limitations=(
            "CONCEPT CAD — a hand-authored reference layout, NOT a load-validated "
            "or homologated chassis.",
            "Tubes export as solid cylinders; wall thickness is metadata only.",
            "Not FEA-analyzed; node positions are representative, not optimized.",
        ),
        example_prompts=(
            "A detailed welded steel tubular buggy chassis with roll cage and "
            "suspension mounts, 2600mm long",
            "A reference sports-car space-frame chassis with side-impact protection",
        ),
    ),
    # ---- Guidance-only family (no geometry) -----------------------------
    CADFamily(
        family_id=GENERIC_ASSEMBLY_FAMILY,
        display_name="Large multi-part assembly (decompose)",
        design_mode=DesignMode.assembly,
        maturity=Maturity.unsupported,
        keywords=("full vehicle", "complete vehicle", "whole car", "airframe",
                  "fuselage", "drivetrain", "engine bay", "transmission", "subsystem"),
        object_types=(),
        required_dimensions=(),
        optional_dimensions=(),
        default_assumptions=(),
        generator="complexity gate -> decomposition guidance (no geometry)",
        generation_strategy=GenerationStrategy.needs_decomposition,
        validation_profile="none",
        export_policy=EXPORT_NONE,
        known_limitations=(
            "Whole machines / multi-subsystem assemblies are NOT generated as one "
            "part. The system returns a decomposition plan so you build and "
            "assemble single components instead.",
        ),
        example_prompts=(
            "Design a complete car with engine, suspension, transmission and body",
            "A full quadcopter drone with motors, arms, flight controller and frame",
        ),
    ),
)


# Indexes ------------------------------------------------------------------
_BY_ID: dict[str, CADFamily] = {f.family_id: f for f in _FAMILIES}

# object_type -> family_id. First family that lists an object_type wins; the
# more specific production family is registered before the generic fallback so
# this ordering is intentional.
_BY_OBJECT_TYPE: dict[str, str] = {}
for _f in _FAMILIES:
    for _ot in _f.object_types:
        _BY_OBJECT_TYPE.setdefault(_ot, _f.family_id)


def all_families() -> tuple[CADFamily, ...]:
    return _FAMILIES


def get_family(family_id: str) -> CADFamily | None:
    return _BY_ID.get(family_id)


def family_for_object_type(object_type: str | None) -> CADFamily | None:
    if not object_type:
        return None
    fid = _BY_OBJECT_TYPE.get(object_type)
    return _BY_ID.get(fid) if fid else None


def families_by_maturity(maturity: Maturity | str) -> list[CADFamily]:
    m = maturity.value if isinstance(maturity, Maturity) else str(maturity)
    return [f for f in _FAMILIES if f.maturity.value == m]


def production_ready_families() -> list[CADFamily]:
    return families_by_maturity(Maturity.production_ready)


def match_family_by_keywords(prompt: str) -> CADFamily | None:
    """Coarse keyword match. Returns the family with the longest matching
    keyword (so 'mounting plate' beats a bare 'plate' style match). Never
    matches the keyword-less generic fallbacks."""
    text = (prompt or "").lower()
    best: tuple[int, CADFamily] | None = None
    for fam in _FAMILIES:
        for kw in fam.keywords:
            if kw in text and (best is None or len(kw) > best[0]):
                best = (len(kw), fam)
    return best[1] if best else None
