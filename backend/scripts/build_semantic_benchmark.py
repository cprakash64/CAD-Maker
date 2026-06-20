"""Generate tests/data/semantic_generation_benchmark.json (>=200 prompts).

Each entry: prompt, expected_object_family, required_features,
forbidden_failure_modes, expected_hole_count, expected_bore, expected_route,
should_generate, acceptable_assumptions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent.parent / "tests" / "data" / "semantic_generation_benchmark.json"


def e(prompt, family, route="cadquery_program", should_generate=True, features=None,
      forbidden=None, holes=None, bore=None, assumptions=True):
    return {
        "prompt": prompt,
        "expected_object_family": family,
        "required_features": features or [],
        "forbidden_failure_modes": forbidden or [],
        "expected_hole_count": holes,
        "expected_bore": bore,
        "expected_route": route,
        "should_generate": should_generate,
        "acceptable_assumptions": assumptions,
    }


def rows() -> list[dict]:
    r: list[dict] = []

    # Bearing housings.
    for sd in (8, 10, 12, 15, 16, 20, 25, 30, 35):
        r.append(e(f"a simple bearing housing for a {sd}mm shaft", "bearing_housing",
                   features=["shaft_bore", "base"], forbidden=["random_primitives"], bore=sd))
    # Flange plates (bolt circle).
    for pcd in (60, 80, 100, 120, 140, 160):
        for n in (4, 6, 8, 12):
            r.append(e(f"a flange plate with {n} holes on a {pcd}mm bolt circle", "flange_plate",
                       features=["bolt_circle", "center_bore"],
                       forbidden=["plain_disk", "missing_hole_pattern"], holes=n))
    # Hex gears + spacers.
    for bore in (5, 6, 8, 10, 12, 16):
        r.append(e(f"a hexagonal gear with a {bore}mm shaft", "hexagonal_gear",
                   features=["hex_profile", "center_bore"], forbidden=["plain_disk", "plain_pulley"], bore=bore))
        r.append(e(f"a hexagonal spacer with a {bore}mm through hole", "hex_spacer",
                   features=["hex_profile", "center_bore"], forbidden=["round_outer"], bore=bore))
    # Spur gears.
    for teeth in (12, 16, 18, 20, 24, 28, 32, 40):
        for bore in (5, 6, 8, 10, 12):
            r.append(e(f"a spur gear with {teeth} teeth and a {bore}mm bore", "spur_gear",
                       features=["teeth", "center_bore"], forbidden=["plain_disk"], bore=bore))
    # Pulleys.
    for od in (30, 40, 50, 60, 70, 80, 100):
        for bore in (6, 8, 10, 12, 16):
            r.append(e(f"a pulley with a {bore}mm shaft hole and {od}mm outer diameter", "pulley",
                       features=["vee_groove", "center_bore"], forbidden=["toothed"], bore=bore))
    # Shaft collars.
    for sd in (8, 10, 12, 15, 16, 20, 25, 30):
        r.append(e(f"a shaft collar for a {sd}mm shaft with an M6 clamp screw", "shaft_collar",
                   features=["bore", "clamp_slit", "clamp_screw"], bore=sd))
    # Counterbored blocks.
    for _ in range(8):
        r.append(e("a rectangular block with a stepped slot and two counterbored holes",
                   "block_with_slot", features=["stepped_slot", "counterbore"],
                   forbidden=["disconnected_bodies", "floating_holes"], holes=2))
    # Pipe elbows.
    for d in (32, 40, 50, 60, 80, 100):
        r.append(e(f"a 90 degree pipe elbow with circular flanges for a {d}mm pipe", "pipe_elbow",
                   features=["flanges", "bore"]))
    # Vise jaws.
    for _ in range(6):
        r.append(e("a small vise jaw with two mounting holes and a V groove", "vise_jaw",
                   features=["v_groove", "mounting_holes"], holes=2))
    # NEMA17 plates.
    for _ in range(6):
        r.append(e("a motor mounting plate for a NEMA 17 stepper", "motor_mount_plate",
                   features=["nema17_pattern", "center_bore"]))

    # Template families (precision_template) — should still generate.
    for w, d, t in [(60, 40, 4), (80, 50, 5), (100, 60, 6), (120, 80, 8)]:
        r.append(e(f"a mounting bracket {w}mm wide {d}mm deep {t}mm thick", "rectangular_bracket",
                   route="precision_template"))
    for w, d, h in [(60, 40, 25), (100, 70, 40), (120, 90, 50)]:
        r.append(e(f"an electronics enclosure {w}mm x {d}mm x {h}mm with 2.5mm walls", "enclosure",
                   route="precision_template"))
    for L, w in [(100, 60), (120, 80), (150, 60)]:
        r.append(e(f"a drill jig plate {L}mm by {w}mm 6mm thick with 5mm guide holes", "drill_jig",
                   route="precision_template"))
    for legs in (40, 60, 80):
        r.append(e(f"an L bracket with {legs}mm legs 5mm thick", "l_bracket", route="precision_template"))
    for d in (20, 25, 32):
        r.append(e(f"a pipe clamp for a {d}mm pipe 6mm thick", "pipe_clamp", route="precision_template"))
    for s in (80, 100, 120):
        r.append(e(f"an adapter plate {s}mm square 6mm thick with a 25mm center bore", "adapter_plate",
                   route="precision_template"))

    # Crankshaft (long).
    r.append(e(
        "Create a realistic crankshaft for a 4-cylinder inline engine with five main "
        "journals, four rod journals, counterweights, keyed front snout and a flywheel "
        "flange with six bolts, machined from forged steel.",
        "inline_4_crankshaft", route="precision_template", features=["journals"]))

    # More template variety.
    for dia in (25, 30, 35, 40, 45):
        r.append(e(f"a knob {dia}mm diameter 22mm tall with an 8mm bore", "handle",
                   route="precision_template"))
    for od in (10, 12, 16, 20, 24):
        r.append(e(f"a spacer {od}mm OD 20mm long with a 5mm bore", "spacer",
                   route="precision_template"))
    # General planner parts.
    for s in (25, 30, 40, 50, 60, 70):
        r.append(e(f"a {s}mm cube with a 10mm hole", "block", route="scad_generator"))
        r.append(e(f"a {s}mm cube", "block", route="scad_generator"))

    # Unsupported / decorative.
    for p in ("a decorative dragon statue", "an abstract art sculpture",
              "a cartoon character figurine", "a realistic human face bust"):
        r.append(e(p, "unsupported", route="clarification", should_generate=False, assumptions=False))
    return r


def main() -> None:
    data = rows()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"prompts": data}, indent=2))
    print(f"Wrote {len(data)} semantic benchmark prompts to {OUT}")


if __name__ == "__main__":
    main()
