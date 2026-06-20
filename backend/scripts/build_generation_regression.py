"""Generate tests/data/generation_regression_prompts.json (>=100 prompts).

Each entry declares the expected route (template | feature_graph | clarification),
expected template, must_have / must_not_have feature hints, should_generate, and
required assumptions. Deterministic.

    python -m scripts.build_generation_regression
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent.parent / "tests" / "data" / "generation_regression_prompts.json"


def e(prompt, route, template=None, should_generate=True, must_have=None,
      must_not_have=None, assumptions=None, export="stl+step"):
    if route == "template":
        route = "precision_template"
    return {
        "prompt": prompt,
        # route: precision_template | feature_graph | scad_generator | clarification
        "route": route,
        "expected_route": route,             # spec-named alias
        "template": template,
        "expected_template": template,
        "should_generate": should_generate,
        "must_have": must_have or [],
        "must_have_features": must_have or [],
        "must_not_have": must_not_have or [],
        "must_not_have_features": must_not_have or [],
        "required_assumptions": assumptions or [],
        "assumptions_expected": bool(assumptions),
        "export_expectation": export,        # "stl+step" | "stl"
    }


def curated() -> list[dict]:
    rows: list[dict] = []
    # --- The current examples + manual failures ---
    rows += [
        e("Drill jig plate 120mm by 80mm, 6mm thick, with 6mm guide holes spaced 25mm and a registration lip.",
          "template", "drill_jig"),
        e("Mounting bracket 80mm wide 40mm deep 5mm thick with two M6 holes.", "template", "rectangular_bracket"),
        e("Electronics enclosure 100mm wide, 60mm deep, 40mm tall with 2.5mm walls and a screw-down lid.",
          "template", "enclosure"),
        e("Pipe clamp for a 25mm pipe, 6mm thick, with two M6 holes.", "template", "pipe_clamp"),
        e("a small bracket with 3 holes", "template", "rectangular_bracket"),
        e("L bracket 60mm legs 5mm thick with M6 holes", "template", "l_bracket"),
        e("M6 standoff spacer 12mm OD 20mm long", "template", "spacer"),
        e("round knob 30mm diameter 25mm tall with 8mm bore", "template", "handle"),
        e("adapter plate 100mm square 6mm thick with a 30mm center bore and four M6 holes",
          "template", "adapter_plate"),
    ]
    # --- Gear / pulley ---
    rows += [
        e("a hexagonal gear with a 10mm shaft", "template", "simple_gear_or_pulley",
          must_have=["hex"], must_not_have=["pulley_groove"]),
        e("a pulley with a 10mm shaft hole and 60mm outer diameter", "template", "simple_gear_or_pulley",
          must_not_have=["teeth"]),
        e("spur gear with 32 teeth and 8mm bore", "template", "simple_gear_or_pulley", must_have=["teeth"]),
        e("a 24 tooth spur gear 80mm diameter", "template", "simple_gear_or_pulley", must_have=["teeth"]),
        e("a timing pulley 40mm diameter with a 6mm bore", "template", "simple_gear_or_pulley"),
        e("a sprocket with 18 teeth and a 12mm bore", "template", "simple_gear_or_pulley", must_have=["teeth"]),
    ]
    # --- Pipe / flange ---
    rows += [
        e("flanged pipe branch, 90mm main pipe, 8 bolts per flange", "template", "flanged_pipe_branch"),
        e("a pipe tee with bolted flanges, 80mm main pipe", "template", "flanged_pipe_branch"),
        e("a 90 degree pipe elbow with circular flanges", "feature_graph"),
        e("a pipe elbow for a 50mm pipe", "feature_graph"),
    ]
    # --- Feature-graph machinist parts ---
    rows += [
        e("a simple bearing housing for a 20mm shaft", "feature_graph"),
        e("a bearing housing for a 25mm shaft with mounting feet", "feature_graph"),
        e("a hexagonal spacer with a 6mm through hole", "feature_graph", must_have=["hex_prism"]),
        e("a hex standoff with an 8mm bore, 25mm long", "feature_graph", must_have=["hex_prism"]),
        e("a rectangular block with a stepped slot and two counterbored holes", "feature_graph"),
    ]
    # --- Crankshaft (long) ---
    rows.append(e(
        "Create a realistic crankshaft for a 4-cylinder inline engine with five "
        "main journals, four rod journals, counterweights, a keyed front snout and "
        "a flywheel flange with six bolts, machined from forged steel and polished.",
        "template", "inline_4_crankshaft", must_have=["journals"]))
    # --- Unsupported / decorative ---
    rows += [
        e("a decorative dragon statue", "clarification", should_generate=False),
        e("a photorealistic human face sculpture", "clarification", should_generate=False),
        e("something cool and artistic", "clarification", should_generate=False),
    ]
    return rows


def systematic() -> list[dict]:
    rows: list[dict] = []
    # Bracket grid.
    for w, d, t in [(60, 40, 4), (80, 50, 5), (100, 60, 6), (120, 80, 8), (90, 45, 5),
                    (70, 35, 4), (140, 70, 6), (50, 50, 3)]:
        rows.append(e(f"mounting bracket {w}mm wide {d}mm deep {t}mm thick with two M6 holes",
                      "template", "rectangular_bracket"))
    # Enclosure grid.
    for w, d, h in [(60, 40, 25), (80, 60, 35), (100, 70, 40), (120, 90, 50),
                    (50, 50, 30), (140, 100, 60), (90, 60, 38)]:
        rows.append(e(f"electronics enclosure {w}mm wide {d}mm deep {h}mm tall with 2.5mm walls",
                      "template", "enclosure"))
    # Drill jig grid.
    for L, w in [(100, 60), (120, 80), (150, 60), (90, 50), (200, 60), (110, 70)]:
        rows.append(e(f"drill jig plate {L}mm by {w}mm 6mm thick with 5mm guide holes spaced 20mm",
                      "template", "drill_jig"))
    # Spacers / gears / pulleys.
    for od in (10, 12, 16):
        rows.append(e(f"a spacer {od}mm OD 20mm long with a 5mm bore", "template", "spacer"))
    for teeth in (16, 24, 40):
        rows.append(e(f"a spur gear with {teeth} teeth and a 10mm bore", "template", "simple_gear_or_pulley",
                      must_have=["teeth"]))
    for od in (40, 60, 80):
        rows.append(e(f"a pulley {od}mm diameter with a 10mm bore", "template", "simple_gear_or_pulley"))
    # Hex parts -> feature graph.
    for af in (12, 16, 20):
        rows.append(e(f"a hexagonal spacer {af}mm across with a 6mm through hole", "feature_graph",
                      must_have=["hex_prism"]))
    # Bearing housings.
    for sd in (12, 16, 20, 25, 30):
        rows.append(e(f"a bearing housing for a {sd}mm shaft", "feature_graph"))
    # Hex standoffs (feature graph).
    for af in (10, 14, 18):
        rows.append(e(f"a hex standoff {af}mm across with a 5mm bore 20mm long", "feature_graph",
                      must_have=["hex_prism"]))
    # Adapter / l-bracket / handle / pipe clamp variety.
    for s in (80, 100, 120, 90, 110):
        rows.append(e(f"adapter plate {s}mm square 6mm thick with a 25mm center bore",
                      "template", "adapter_plate"))
    for legs in (40, 60, 80, 50, 70):
        rows.append(e(f"an L-bracket with {legs}mm legs 5mm thick and M6 holes", "template", "l_bracket"))
    for d in (20, 25, 32, 40):
        rows.append(e(f"a pipe clamp for a {d}mm pipe 6mm thick with two M6 holes",
                      "template", "pipe_clamp"))
    for dia in (25, 30, 40, 35):
        rows.append(e(f"a knob {dia}mm diameter 22mm tall with an 8mm bore", "template", "handle"))
    # Flanged pipe sizes.
    for md in (80, 90, 110):
        rows.append(e(f"a flanged pipe branch with a {md}mm main pipe and 8 bolts per flange",
                      "template", "flanged_pipe_branch"))
    # Stepped-slot blocks (feature graph).
    for w in (60, 80, 100):
        rows.append(e(f"a {w}mm rectangular block with a stepped slot and two counterbored holes",
                      "feature_graph"))
    # Vague-but-buildable.
    rows += [
        e("a bracket", "template", "rectangular_bracket"),
        e("an enclosure for my electronics", "template", "enclosure"),
        e("a drill jig", "template", "drill_jig"),
        e("a gear", "template", "simple_gear_or_pulley"),
        e("a spacer", "template", "spacer"),
        e("a pulley", "template", "simple_gear_or_pulley"),
        e("a mounting plate", "template", "rectangular_bracket"),
        e("an adapter plate", "template", "adapter_plate"),
    ]
    # Flange plates + shaft collars (feature graph).
    for bc in (80, 100, 120):
        for n in (4, 6, 8):
            rows.append(e(f"a flange plate with {n} holes on a {bc}mm bolt circle",
                          "feature_graph"))
    for sd in (8, 10, 12, 16, 20, 25):
        rows.append(e(f"a shaft collar for a {sd}mm shaft with an M6 clamp screw", "feature_graph"))
    # Pipe elbows.
    for d in (40, 50, 60):
        rows.append(e(f"a 90 degree pipe elbow with circular flanges for a {d}mm pipe",
                      "feature_graph"))
    # General mechanical parts via the general planner (compiles to feature graph).
    for s in (25, 30, 40, 50, 60, 70):
        rows.append(e(f"a {s}mm cube with a 10mm hole", "scad_generator"))
        rows.append(e(f"a {s}mm cube", "scad_generator"))
    for od in (20, 30, 40, 50):
        rows.append(e(f"a ring {od}mm outer diameter with a 10mm bore", "scad_generator"))
        rows.append(e(f"a washer {od}mm outer diameter", "scad_generator"))
    for s in (40, 50, 60, 70, 80, 90):
        rows.append(e(f"a rectangular block {s}mm with a hole", "scad_generator"))
    # Crankshaft (long, full).
    rows.append(e(
        "Create a realistic crankshaft for a 4-cylinder inline engine, five main "
        "journals, four rod journals, counterweights, keyed front snout, flywheel "
        "flange with six bolts, machined from forged steel and polished to a fine finish.",
        "precision_template", "inline_4_crankshaft", must_have=["journals"]))
    # More unsupported / decorative.
    rows += [
        e("a tiny model castle", "clarification", should_generate=False),
        e("an abstract art sculpture", "clarification", should_generate=False),
        e("a cute cartoon dragon", "clarification", should_generate=False),
        e("a realistic human face bust", "clarification", should_generate=False),
    ]
    return rows


def main() -> None:
    rows = curated() + systematic()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"prompts": rows}, indent=2))
    print(f"Wrote {len(rows)} regression prompts to {OUT}")


if __name__ == "__main__":
    main()
