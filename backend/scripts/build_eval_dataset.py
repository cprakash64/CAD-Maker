"""Generate the 200+ prompt evaluation dataset (tests/data/eval_prompts.json).

Combines curated, real-world SourceCAD-style prompts with systematic variations
across the templates, sizes, units, hole finishes and manufacturing methods,
plus a deliberate set of dangerous / invalid prompts. Deterministic output.

    python -m scripts.build_eval_dataset
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent.parent / "tests" / "data" / "eval_prompts.json"


def _e(prompt, category, expect="model", expect_type=None, dangerous=False):
    entry = {"prompt": prompt, "category": category, "expect": expect, "dangerous": dangerous}
    if expect_type:
        entry["expect_type"] = expect_type
    return entry


def curated() -> list[dict]:
    return [
        # --- Real-world mounting brackets ---
        _e("Wall-mounted bracket for a shelf, 120mm wide, 80mm deep, 6mm thick, four M5 holes", "bracket", expect_type="rectangular_bracket"),
        _e("Right-angle shelf bracket with a gusset, 100mm tall, 80mm deep, 5mm thick", "bracket"),
        _e("Bracket to mount a 40mm fan, 50mm square, 4mm thick, four M3 holes", "bracket"),
        _e("Heavy-duty steel bracket 150x90x8mm with two 10mm bolt holes, rounded corners", "bracket", expect_type="rectangular_bracket"),
        _e("Camera mounting plate 60x60x5mm with a 1/4 inch center hole", "bracket"),
        _e("Solar panel corner bracket 70x40x4mm with countersunk M4 holes", "bracket"),
        _e("Monitor arm mounting plate, 100mm square, 6mm thick, VESA 75 holes", "bracket"),
        _e("Bracket 80mm wide 40mm deep 5mm thick with two M6 counterbored holes for cap screws", "bracket", expect_type="rectangular_bracket"),
        # --- Enclosures ---
        _e("Electronics enclosure for an Arduino Uno, 80mm x 60mm x 30mm, 2.5mm walls", "enclosure", expect_type="enclosure"),
        _e("Waterproof project box 120x80x55mm with 3mm walls and a screw-down lid", "enclosure"),
        _e("Small sensor housing 40mm x 30mm x 20mm with 2mm walls", "enclosure", expect_type="enclosure"),
        _e("Raspberry Pi case 95mm x 65mm x 30mm with 2.5mm walls and M3 corner screws", "enclosure"),
        _e("Battery enclosure 100x70x40mm, 3mm walls, rounded corners", "enclosure", expect_type="enclosure"),
        _e("Junction box 150mm wide 100mm deep 60mm tall with a lid", "enclosure"),
        _e("Handheld remote case 70x40x18mm, 2mm walls", "enclosure", expect_type="enclosure"),
        # --- Drill jigs ---
        _e("Drill jig for evenly spaced 5mm holes on a 200mm rail, 6mm thick", "drill_jig", expect_type="drill_jig"),
        _e("Drilling template 120x80x6mm with 8mm guide holes spaced 25mm and a registration lip", "drill_jig", expect_type="drill_jig"),
        _e("Shelf pin drilling jig, 100x40x6mm, 5mm holes spaced 32mm", "drill_jig"),
        _e("Dowel hole jig 90x50x8mm with 8mm guide holes and an edge fence", "drill_jig", expect_type="drill_jig"),
        _e("Cabinet handle drilling template 160x40x6mm, two 5mm holes", "drill_jig"),
        # --- Adapter plates ---
        _e("Adapter plate from NEMA 17 to NEMA 23, 100mm square, 6mm thick, center bore 30mm", "adapter", expect_type="adapter_plate"),
        _e("Motor adapter plate 120mm square 8mm thick with a 40mm center bore and four M6 holes", "adapter", expect_type="adapter_plate"),
        _e("Flange adapter 90mm square, 5mm thick, 25mm center hole", "adapter"),
        _e("Transition plate 110x110x6mm bridging two bolt patterns", "adapter", expect_type="adapter_plate"),
        _e("CNC aluminum adapter plate 130mm square 10mm thick, 50mm bore", "adapter"),
        # --- Other templates ---
        _e("Pipe clamp for a 25mm pipe, 25mm wide, 6mm thick, two M6 holes", "pipe_clamp", expect_type="pipe_clamp"),
        _e("Hose saddle clamp for 32mm tube, 30mm wide", "pipe_clamp", expect_type="pipe_clamp"),
        _e("M6 standoff spacer, 12mm outer diameter, 20mm long", "spacer", expect_type="spacer"),
        _e("Nylon spacer 10mm OD, 15mm long, 5mm bore", "spacer", expect_type="spacer"),
        _e("L-bracket 60x40x60mm, 5mm thick, with M6 mounting holes", "l_bracket", expect_type="l_bracket"),
        _e("Angle bracket 80mm legs, 50mm wide, 6mm thick", "l_bracket", expect_type="l_bracket"),
        _e("Round knob 30mm diameter, 25mm tall, 8mm shaft bore", "handle", expect_type="handle"),
        _e("Control knob 25mm diameter 20mm tall with a 6mm D-shaft bore", "handle", expect_type="handle"),
        # --- Vague ---
        _e("I need a bracket", "vague", expect="either"),
        _e("make me an enclosure", "vague", expect="either", expect_type="enclosure"),
        _e("a plate with some holes", "vague", expect="either"),
        _e("something to mount a circuit board", "vague", expect="either"),
        _e("a drill guide", "vague", expect="either", expect_type="drill_jig"),
        _e("a box for electronics", "vague", expect="either", expect_type="enclosure"),
        _e("a spacer", "vague", expect="either", expect_type="spacer"),
        _e("a knob", "vague", expect="either", expect_type="handle"),
        # --- Ambiguous ---
        _e("a mount", "ambiguous", expect="either"),
        _e("a clamp", "ambiguous", expect="either"),
        _e("a holder", "ambiguous", expect="either"),
        _e("a thing to hold a pipe", "ambiguous", expect="either"),
        _e("part for my 3d printer", "ambiguous", expect="either"),
        # --- Dangerous / invalid ---
        _e("bracket 80mm wide, -5mm thick", "invalid", expect="clarification", dangerous=True),
        _e("bracket 80mm wide 0mm thick", "invalid", expect="either", dangerous=True),
        _e("enclosure 99999mm wide", "invalid", expect="clarification", dangerous=True),
        _e("bracket 50000mm x 40mm x 5mm", "invalid", expect="clarification", dangerous=True),
        _e("adapter plate 50mm square 5mm thick with an 80mm center bore", "invalid", expect="clarification", dangerous=True),
        _e("enclosure 30mm wide 30mm deep 20mm tall with 20mm walls", "invalid", expect="clarification", dangerous=True),
        _e("spacer 5mm outer diameter with a 10mm bore", "invalid", expect="clarification", dangerous=True),
        _e("pipe clamp, 6mm thick", "missing_info", expect="clarification", expect_type="pipe_clamp"),
        _e("bracket -100 x -50 x -5", "invalid", expect="clarification", dangerous=True),
        _e("enclosure with negative wall thickness -2mm", "invalid", expect="either", dangerous=True),
        # --- More real-world maker prompts ---
        _e("GoPro mounting bracket 50mm wide 30mm deep 4mm thick with two M3 holes", "bracket"),
        _e("Bracket to hang a router on the wall, 90mm wide 60mm deep 5mm thick, four M5 holes", "bracket"),
        _e("Speaker mounting plate 80mm square 5mm thick with four M4 countersunk holes", "bracket"),
        _e("LED strip channel bracket 100x25x4mm with two M3 holes", "bracket"),
        _e("Enclosure for a buck converter, 55mm x 35mm x 22mm, 2mm walls", "enclosure", expect_type="enclosure"),
        _e("Outdoor sensor enclosure 90x70x45mm, 3mm walls, rounded corners", "enclosure"),
        _e("3-gang enclosure 180mm x 120mm x 50mm with a screw lid", "enclosure"),
        _e("Drill jig for a 19mm hole pattern, 100x60x6mm, four 5mm guide holes", "drill_jig"),
        _e("European hinge boring jig 100x50x8mm with 35mm and 8mm guide holes", "drill_jig", expect_type="drill_jig"),
        _e("Adapter plate to mount a stepper to extrusion, 80mm square 6mm thick, 22mm bore", "adapter", expect_type="adapter_plate"),
        _e("Hub adapter plate 100mm square 8mm thick with a 50mm center bore", "adapter"),
        _e("Standoff 6mm OD 30mm long with a 3mm bore", "spacer", expect_type="spacer"),
        _e("Clamp for a 50mm exhaust pipe, 35mm wide, 8mm thick, two M8 holes", "pipe_clamp", expect_type="pipe_clamp"),
        _e("L-bracket 100mm x 70mm legs, 60mm wide, 8mm thick", "l_bracket", expect_type="l_bracket"),
        _e("Knurled knob 40mm diameter 30mm tall with a 10mm bore", "handle", expect_type="handle"),
        _e("Tablet stand bracket 120mm wide 80mm deep 6mm thick with rounded corners", "bracket"),
    ]


def systematic() -> list[dict]:
    out: list[dict] = []
    bracket_sizes = [(60, 40, 4), (80, 50, 5), (100, 60, 6), (120, 80, 8),
                     (50, 50, 3), (90, 45, 5), (140, 70, 6)]
    finishes = ["", "with two M6 holes", "with four M4 countersunk holes",
                "with two M5 counterbored holes", "with rounded corners",
                "with a gusset and two M6 holes"]
    for w, d, t in bracket_sizes:
        for f in finishes:
            out.append(_e(f"Mounting bracket {w}mm wide {d}mm deep {t}mm thick {f}".strip(),
                          "bracket_grid", expect_type="rectangular_bracket"))

    enc_sizes = [(60, 40, 25), (80, 60, 35), (100, 70, 40), (120, 90, 50),
                 (50, 50, 30), (140, 100, 60)]
    walls = ["2mm walls", "2.5mm walls", "3mm walls", "2mm walls and M3 corner screws"]
    for w, d, h in enc_sizes:
        for wl in walls:
            out.append(_e(f"Electronics enclosure {w}mm wide {d}mm deep {h}mm tall with {wl}",
                          "enclosure_grid", expect_type="enclosure"))

    jig_sizes = [(80, 50, 6), (100, 60, 6), (120, 80, 8), (150, 60, 6),
                 (90, 50, 8), (200, 60, 6)]
    jig_holes = ["5mm guide holes spaced 20mm", "6mm guide holes spaced 25mm",
                 "8mm holes spaced 30mm and a lip", "4mm guide holes spaced 15mm"]
    for L, w, t in jig_sizes:
        for hh in jig_holes:
            out.append(_e(f"Drill jig plate {L}mm by {w}mm {t}mm thick with {hh}",
                          "drill_jig_grid", expect_type="drill_jig"))

    adapter_sizes = [(80, 5), (100, 6), (120, 8), (90, 6), (110, 8)]
    bores = ["25mm center bore", "30mm center bore",
             "40mm center bore and four M6 holes", "no center bore and four M5 holes"]
    for s, t in adapter_sizes:
        for b in bores:
            out.append(_e(f"Adapter plate {s}mm square {t}mm thick with a {b}",
                          "adapter_grid", expect_type="adapter_plate"))

    spacer_specs = [(10, 15), (12, 20), (8, 12), (16, 25)]
    for od, ln in spacer_specs:
        out.append(_e(f"M6 standoff spacer {od}mm outer diameter {ln}mm long",
                      "spacer_grid", expect_type="spacer"))

    for pd in (20, 25, 32, 40):
        out.append(_e(f"Pipe clamp for a {pd}mm pipe, 25mm wide, two M6 holes",
                      "pipe_clamp_grid", expect_type="pipe_clamp"))

    for legs in (40, 60, 80):
        out.append(_e(f"L-bracket {legs}mm legs, 40mm wide, 5mm thick with M6 holes",
                      "l_bracket_grid", expect_type="l_bracket"))

    for dia in (25, 30, 35):
        out.append(_e(f"Round knob {dia}mm diameter 22mm tall with an 8mm bore",
                      "handle_grid", expect_type="handle"))

    # Units + manufacturing variety.
    for unit_prompt, cat in [
        ("Aluminum CNC bracket 4 inch wide 2 inch deep 0.25 inch thick", "manufacturing"),
        ("Laser cut plate 100mm x 100mm 3mm thick with four M4 holes", "manufacturing"),
        ("Sheet metal bracket 80mm x 40mm with two M5 holes", "manufacturing"),
        ("Resin printed adapter plate 60mm square 5mm thick", "manufacturing"),
        ("3D printed enclosure 90mm x 60mm x 40mm with 2mm walls", "manufacturing"),
        ("Machined aluminum adapter plate 5 inch square 0.5 inch thick", "manufacturing"),
        ("CNC milled mounting bracket 3 inch wide 2 inch deep 6mm thick", "manufacturing"),
        ("Laser cut drill jig 120mm x 80mm 4mm thick with 5mm holes spaced 20mm", "manufacturing"),
    ]:
        out.append(_e(unit_prompt, cat))

    return out


def modifications() -> list[dict]:
    return [
        {"prompt": "make it wider", "base": "bracket 80x40x5mm with two M6 holes", "expect": "model"},
        {"prompt": "make it 120mm wide", "base": "bracket 80x40x5mm with two M6 holes", "expect": "model"},
        {"prompt": "make it taller", "base": "enclosure 100x60x40mm", "expect": "model"},
        {"prompt": "move the holes farther apart", "base": "bracket 80x40x5mm with two M6 holes", "expect": "model"},
        {"prompt": "make the wall thickness 4 mm", "base": "enclosure 100x60x40mm with 2.5mm walls", "expect": "model"},
        {"prompt": "add rounded edges", "base": "bracket 80x40x5mm", "expect": "model"},
        {"prompt": "chamfer the edges 2mm", "base": "adapter plate 100mm square 6mm thick", "expect": "model"},
        {"prompt": "make it bigger", "base": "bracket 80x40x5mm", "expect": "model"},
        {"prompt": "switch to aluminum", "base": "bracket 80x40x5mm", "expect": "model"},
        {"prompt": "make it fly to the moon", "base": "bracket 80x40x5mm", "expect": "clarification"},
    ]


def main() -> None:
    creation = curated() + systematic()
    data = {
        "description": "SourceCAD eval dataset (>=200 creation prompts).",
        "creation": creation,
        "modification": modifications(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2))
    print(f"Wrote {len(creation)} creation + {len(data['modification'])} modification "
          f"prompts to {OUT}")


if __name__ == "__main__":
    main()
