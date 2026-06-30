"""Offline, deterministic plain-English → CadPlan planner.

This is the rule-based planner used by the mock provider and the eval suite, so
the whole pipeline runs with NO API key (verify.sh / CI stay offline). It is also
the source of truth for what a "correct" CadPlan looks like for each part family,
which the OpenAI planner is prompted to emit.

It composes PRIMITIVE features (never whole-part templates) and detects the part
family from keywords, most-specific-first, so:
  - "blind flange"      → a circular flange (not a rectangular adapter plate)
  - "straight pipe spool" → pipe + two end flanges (not a tee)
  - "U-shaped bracket"  → base + two side walls (not an enclosure)
  - "bearing block"     → base + boss + bore + holes (never a schema crash)
Unfamiliar mechanical parts fall back to a generic plate/box rather than failing.
"""
from __future__ import annotations

import re

from app.cad.plan.schema import CadPlan, Expected, Feature

# Screw label → clearance hole diameter (mm).
SCREW_CLEARANCE = {
    "m3": 3.4, "m4": 4.5, "m5": 5.5, "m6": 6.6, "m8": 9.0, "m10": 11.0, "m12": 13.5,
}


def _nums(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _near(text: str, *labels: str) -> float | None:
    """First number appearing just before any label, e.g. '120mm long'."""
    for label in labels:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*" + re.escape(label), text)
        if m:
            return float(m.group(1))
    return None


def _screws(text: str) -> tuple[float, str] | None:
    """Return (clearance_diameter, label) for the first M-screw callout."""
    m = re.search(r"\bm(\d+(?:\.\d+)?)\b", text)
    if not m:
        return None
    label = "m" + m.group(1)
    return SCREW_CLEARANCE.get(label, float(m.group(1)) + 0.5), label.upper()


def _count(text: str, *words: str) -> int | None:
    word_to_n = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8}
    for w in words:
        # Bounded gap so a count can't be grabbed from a distant clause
        # ("90 degree V groove and 2 M6 mounting holes" must yield 2, not 90).
        m = re.search(r"(\d+|two|three|four|five|six|eight)\s+(?:[a-z0-9.\- ]{0,20}?)" + w, text)
        if m:
            tok = m.group(1)
            return int(tok) if tok.isdigit() else word_to_n.get(tok)
    return None


def _clarify(name: str, *questions: str) -> CadPlan:
    return CadPlan(object_type=name, clarification_required=True,
                   clarification_questions=list(questions))


# --- family builders --------------------------------------------------------
def _plan_flange(t: str) -> CadPlan:
    od = _near(t, "outer diameter", "od") or _near(t, "mm outer", "diameter")
    thk = _near(t, "thick")
    if not od or not thk:
        return _clarify("blind_flange",
                        "What is the flange outer diameter and thickness (mm)?")
    pcd = _near(t, "pcd", "bolt circle", "bolt-circle")
    bolt_n = _count(t, "holes", "bolts") or 0
    sc = _screws(t)
    bolt_d = sc[0] if sc else 9.0
    bore = 0.0 if "no center bore" in t or "no bore" in t else (
        _near(t, "center bore", "bore") or 0.0)
    assumptions = []
    if sc and bolt_n:
        assumptions.append(f"{bolt_n}× {sc[1]} clearance holes ({bolt_d}mm) on {pcd}mm PCD")
    f = Feature(id="flange_body", kind="circular_flange", description="circular flange body",
                params={"od": od, "thickness": thk, "pcd": pcd or 0,
                        "bolt_count": bolt_n, "bolt_diameter": bolt_d, "bore": bore})
    holes = bolt_n + (1 if bore > 0 else 0)
    return CadPlan(
        object_type="blind_flange" if bore == 0 else "flange",
        name="flange", assumptions=assumptions, features=[f],
        expected=Expected(bbox_mm={"x": od, "y": od, "z": thk},
                          hole_count=holes, through_hole_count=holes),
    )


def _plan_pipe_spool(t: str) -> CadPlan:
    length = _near(t, "long", "length")
    od = _near(t, "pipe outer diameter", "outer diameter", "pipe od", "od")
    bore = _near(t, "bore", "id", "inner diameter")
    if not (length and od):
        return _clarify("pipe_spool",
                        "What is the pipe length and outer diameter (mm)?")
    flange_od = _near(t, "flange is", "flange od") or _near(t, "od") or od + 40
    flange_thk = _near(t, "thick") or 12
    bolt_n = _count(t, "holes") or 8
    sc = _screws(t)
    bolt_d = sc[0] if sc else 9.0
    pcd = _near(t, "pcd", "bolt circle") or max(bore or od - 20, flange_od - 20)
    f = Feature(id="pipe_body", kind="pipe_spool", description="straight pipe with end flanges",
                params={"length": length, "od": od, "id": bore or max(1, od - 16),
                        "flange_od": flange_od, "flange_thickness": flange_thk,
                        "bolt_count": bolt_n, "bolt_diameter": bolt_d, "pcd": pcd})
    total = bolt_n * 2
    return CadPlan(
        object_type="pipe_spool", name="pipe spool",
        assumptions=[f"Straight spool (not a tee); {bolt_n} bolt holes per flange = {total} total"],
        features=[f],
        expected=Expected(bbox_mm={"x": flange_od, "y": flange_od, "z": length},
                          hole_count=total, through_hole_count=total),
    )


def _plan_pipe_tee(t: str) -> CadPlan:
    """Flanged pipe tee / branch. Honors drawing-derived dimensions (flange OD,
    main length/height, wall + flange thickness, N×Ø bolt callouts) and uses
    PROPORTIONS of what is known — not fixed generic defaults — for the rest."""
    assumptions = ["T topology: vertical main run + horizontal branch"]
    flange_od = _near(t, "flange outer diameter", "flange od", "flange diameter")
    main = _near(t, "main pipe outer diameter", "main pipe", "main")
    branch = _near(t, "branch pipe outer diameter", "branch pipe", "branch")
    if not main:
        main = round(flange_od * 0.6, 1) if flange_od else 75.0
        assumptions.append(f"Main pipe OD {main}mm inferred"
                           + (" from the flange OD" if flange_od else " (unspecified)"))
    if not branch:
        branch = round(main * 2 / 3, 1)
        assumptions.append(f"Branch pipe OD {branch}mm inferred from the main pipe")
    wall = _near(t, "wall thickness", "wall")
    if not wall:
        wall = round(max(3.0, main * 0.07), 1)
        assumptions.append(f"{wall}mm pipe wall thickness assumed")
    fod_main = flange_od or main + 40
    fod_branch = max(branch + (fod_main - main), branch + 20)
    flange_thk = _near(t, "flange thickness", "thick")
    if not flange_thk:
        flange_thk = round(max(8.0, fod_main * 0.1), 1)
        assumptions.append(f"{flange_thk}mm flange thickness assumed")
    main_len = _near(t, "main pipe length", "total height", "overall height",
                     "height", "tall", "long") or round(fod_main * 1.25, 1)
    branch_len = _near(t, "branch length", "branch pipe length") or round(main_len / 2, 1)

    # Bolt holes: "12x 10mm holes (per flange)" callouts carry count + diameter.
    m = re.search(r"(\d+)\s*x\s*ø?\s*(\d+(?:\.\d+)?)\s*mm\s*(?:bolt\s*)?holes", t)
    if m:
        bolt_n, bolt_d = int(m.group(1)), float(m.group(2))
    else:
        bolt_n = _count(t, "holes per flange", "holes each", "bolt holes", "holes") or 4
        sc = _screws(t)
        bolt_d = sc[0] if sc else 9.0
        if not sc:
            assumptions.append(f"{bolt_n}× Ø{bolt_d}mm bolt holes per flange assumed")
    pcd_main = _near(t, "pcd", "bolt circle") or round(fod_main - 2.5 * bolt_d, 1)
    pcd_branch = round(fod_branch - 2.5 * bolt_d, 1)
    assumptions.append(
        f"Bolt circles: {bolt_n}× Ø{bolt_d:g}mm on Ø{pcd_main:g}mm PCD per flange "
        f"({3 * bolt_n} flange holes total)")

    main_bore = max(1.0, main - 2 * wall)
    branch_bore = max(1.0, branch - 2 * wall)
    features = [
        Feature(id="main_pipe", kind="pipe", axis="z", description="main run pipe",
                params={"od": main, "id": main_bore, "length": main_len},
                at=[0, 0, -main_len / 2]),
        Feature(id="branch_pipe", kind="pipe", axis="x", description="side branch pipe",
                params={"od": branch, "id": branch_bore, "length": branch_len}, at=[0, 0, 0]),
        Feature(id="top_flange", kind="circular_flange", axis="z", description="top flange",
                params={"od": fod_main, "thickness": flange_thk, "pcd": pcd_main,
                        "bolt_count": bolt_n, "bolt_diameter": bolt_d, "bore": main_bore},
                at=[0, 0, main_len / 2 - flange_thk]),
        Feature(id="bottom_flange", kind="circular_flange", axis="z", description="bottom flange",
                params={"od": fod_main, "thickness": flange_thk, "pcd": pcd_main,
                        "bolt_count": bolt_n, "bolt_diameter": bolt_d, "bore": main_bore},
                at=[0, 0, -main_len / 2]),
        Feature(id="branch_flange", kind="circular_flange", axis="x", description="branch flange",
                params={"od": fod_branch, "thickness": flange_thk, "pcd": pcd_branch,
                        "bolt_count": bolt_n, "bolt_diameter": bolt_d, "bore": branch_bore},
                at=[branch_len - flange_thk, 0, 0]),
    ]
    # Drawing-derived prompts say "flanged pipe branch"; text prompts say "tee".
    ot = "flanged_pipe_branch" if re.search(r"\bpipe branch\b", t) else "pipe_tee"
    return CadPlan(
        object_type=ot, name="flanged pipe tee / branch",
        assumptions=assumptions, features=features,
        # Tee envelope + exact through-count aren't cleanly specified by the
        # prompt, so we don't pin them — topology (2 pipes + 3 flanges) is the spec.
        expected=Expected(),
    )


def _plan_u_bracket(t: str) -> CadPlan:
    width = _near(t, "wide", "width") or 80
    tall = _near(t, "tall", "high", "height") or 60
    thk = _near(t, "thick") or 6
    depth = _near(t, "deep", "depth") or 40
    sc = _screws(t)
    base_d = sc[0] if sc else 6.6
    pivot_d = _near(t, "pivot hole", "mm pivot") or 8
    wall_x = width / 2 - thk / 2
    features = [
        Feature(id="base_plate", kind="plate", description="base plate",
                params={"width": width, "depth": depth, "thickness": thk}),
        Feature(id="side_wall_left", kind="rectangular_wall", description="left side wall",
                params={"width": thk, "depth": depth, "height": tall}, at=[-wall_x, 0, 0]),
        Feature(id="side_wall_right", kind="rectangular_wall", description="right side wall",
                params={"width": thk, "depth": depth, "height": tall}, at=[wall_x, 0, 0]),
        Feature(id="mounting_hole_1", kind="hole", description="base mounting hole",
                params={"diameter": base_d}, at=[-width / 4, 0, 0]),
        Feature(id="mounting_hole_2", kind="hole", description="base mounting hole",
                params={"diameter": base_d}, at=[width / 4, 0, 0]),
        Feature(id="pivot_hole_left", kind="hole", axis="x", description="left wall pivot hole",
                params={"diameter": pivot_d}, at=[-wall_x, 0, tall / 2]),
        Feature(id="pivot_hole_right", kind="hole", axis="x", description="right wall pivot hole",
                params={"diameter": pivot_d}, at=[wall_x, 0, tall / 2]),
    ]
    return CadPlan(
        object_type="u_bracket", name="U bracket",
        assumptions=[f"Assumed {depth}mm depth (unspecified)",
                     "Base + two side walls; 2 base holes + a pivot hole through each wall"],
        features=features,
        expected=Expected(bbox_mm={"x": width, "y": depth, "z": tall},
                          hole_count=4, through_hole_count=4),
    )


def _spaced(length: float, n: int, start: float) -> list[float]:
    """`n` positions evenly spread along [start, length-start] (clear of ends)."""
    if n <= 0:
        return []
    if n == 1:
        return [length / 2]
    end = length - start
    if end <= start:  # leg too short for the inset — fall back to a centered band
        start, end = length * 0.25, length * 0.75
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def _plan_l_angle_bracket(t: str) -> CadPlan:
    """True L / angle bracket: two equal legs fused at the corner, with mounting
    holes through each face. Corner at the origin; legs grow +x and +z."""
    leg = _near(t, "legs", "leg", "long", "tall") or 60
    thk = _near(t, "thick") or 5
    width = _near(t, "width", "wide") or 20
    sc = _screws(t)
    hd = sc[0] if sc else (_near(t, "mounting hole", "mm hole", "hole") or 6.0)
    per_face = _count(t, "holes", "mounting holes") or 2
    each = bool(re.search(r"each (face|leg|side)|per (face|leg|side)|both (faces|legs|sides)", t))
    n_h = per_face
    n_v = per_face if each else 0  # default: holes on the base face only

    features = [
        Feature(id="horizontal_leg", kind="plate", description="horizontal leg / base face",
                params={"width": leg, "depth": width, "thickness": thk}, at=[leg / 2, 0, 0]),
        Feature(id="vertical_leg", kind="rectangular_wall", description="vertical leg face",
                params={"width": thk, "depth": width, "height": leg}, at=[thk / 2, 0, 0]),
    ]
    inset = thk + 5
    for i, x in enumerate(_spaced(leg, n_h, inset)):
        features.append(Feature(
            id=f"hole_horizontal_{i}", kind="hole", axis="z", through=True,
            description="mounting hole through the horizontal face",
            params={"diameter": hd}, at=[x, 0, 0]))
    for i, z in enumerate(_spaced(leg, n_v, inset)):
        features.append(Feature(
            id=f"hole_vertical_{i}", kind="hole", axis="x", through=True,
            description="mounting hole through the vertical face",
            params={"diameter": hd}, at=[thk / 2, 0, z]))
    total = n_h + n_v
    return CadPlan(
        object_type="l_bracket_fg", name="L bracket",
        assumptions=[
            f"Two equal {leg:g}mm legs, {thk:g}mm thick, {width:g}mm wide, fused at the corner",
            (f"{per_face} × Ø{hd:g}mm holes through each face" if each
             else f"{total} × Ø{hd:g}mm holes through the base face"),
        ],
        features=features,
        expected=Expected(bbox_mm={"x": leg, "y": width, "z": leg},
                          hole_count=total, through_hole_count=total),
    )


def _plan_l_bracket(t: str) -> CadPlan:
    # "legs" / "each face" describes a true angle bracket (equal legs, holes on
    # both faces); a "base plate + support wall" describes a flat base + riser.
    if "leg" in t and not ("base plate" in t or "support wall" in t):
        return _plan_l_angle_bracket(t)

    ns = _nums(t)
    base_w = _near(t, "by") or (ns[0] if ns else 100)
    base_d = 60.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*by\s*(\d+(?:\.\d+)?)", t)
    if m:
        base_w, base_d = float(m.group(1)), float(m.group(2))
    base_thk = _near(t, "thick") or 8
    wall_h = _near(t, "tall", "high") or 60
    wall_thk = 8.0
    holes_n = _count(t, "holes") or 4
    sc = _screws(t)
    hd = sc[0] if sc else 6.6
    hx, hy = base_w / 2 - 12, base_d / 2 - 12
    holes = [
        Feature(id=f"mounting_hole_{i}", kind="hole", description="base mounting hole",
                params={"diameter": hd}, at=[x, y, 0])
        for i, (x, y) in enumerate([(-hx, -hy), (hx, -hy), (-hx, hy), (hx, hy)][:holes_n])
    ]
    features = [
        Feature(id="base_plate", kind="plate", description="base plate",
                params={"width": base_w, "depth": base_d, "thickness": base_thk}),
        Feature(id="vertical_wall", kind="rectangular_wall", description="vertical support wall",
                params={"width": wall_thk, "depth": base_d, "height": wall_h}, at=[0, 0, 0]),
        *holes,
    ]
    return CadPlan(
        object_type="l_bracket_fg", name="L bracket",
        assumptions=["Base plate + vertical support wall centered on the base"],
        features=features,
        expected=Expected(bbox_mm={"x": base_w, "y": base_d, "z": wall_h},
                          hole_count=len(holes), through_hole_count=len(holes)),
    )


def _plan_hinge_bracket(t: str) -> CadPlan:
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*by\s*(\d+(?:\.\d+)?)\s*mm\s*by\s*(\d+(?:\.\d+)?)", t)
    bw, bd, bt = (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (70, 40, 6)
    ear_h = _near(t, "tall", "high") or 30
    ear_thk = 6.0
    pin_d = _near(t, "pin hole", "mm pin") or 8
    ear_x = bw / 2 - ear_thk / 2
    # Ears stand ON TOP of the base: total height = base thickness + ear height.
    pin_z = bt + ear_h * 0.6
    features = [
        Feature(id="base_plate", kind="plate", description="base plate",
                params={"width": bw, "depth": bd, "thickness": bt}),
        Feature(id="hinge_ear_left", kind="rectangular_wall", description="left hinge ear",
                params={"width": ear_thk, "depth": bd, "height": ear_h}, at=[-ear_x, 0, bt]),
        Feature(id="hinge_ear_right", kind="rectangular_wall", description="right hinge ear",
                params={"width": ear_thk, "depth": bd, "height": ear_h}, at=[ear_x, 0, bt]),
        Feature(id="pin_hole", kind="hole", axis="x",
                description="coaxial pin hole through both ears",
                params={"diameter": pin_d}, at=[0, 0, pin_z]),
    ]
    return CadPlan(
        object_type="hinge_bracket", name="hinge bracket",
        assumptions=["Base + two side ears on top with a single coaxial pin hole through both",
                     f"Total height {bt + ear_h}mm = {bt}mm base + {ear_h}mm ears"],
        features=features,
        expected=Expected(bbox_mm={"x": bw, "y": bd, "z": bt + ear_h},
                          hole_count=1, through_hole_count=1),
    )


def _plan_bearing_block(t: str) -> CadPlan:
    m = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*by\s*(\d+(?:\.\d+)?)\s*mm\s*by\s*(\d+(?:\.\d+)?)", t)
    bw, bd, bt = (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else (90, 45, 12)
    bore = _near(t, "through bore", "bore", "shaft") or 20
    boss_od = _near(t, "boss", "od") or 45
    boss_h = _near(t, "tall", "high") or 30
    holes_n = _count(t, "holes", "mounting holes") or 4
    sc = _screws(t)
    hd = sc[0] if sc else 6.6
    hx, hy = bw / 2 - 12, bd / 2 - 10
    mount = [
        Feature(id=f"mounting_hole_{i}", kind="hole", description="mounting hole",
                params={"diameter": hd}, at=[x, y, 0])
        for i, (x, y) in enumerate([(-hx, -hy), (hx, -hy), (-hx, hy), (hx, hy)][:holes_n])
    ]
    features = [
        Feature(id="base_plate", kind="plate", description="base plate",
                params={"width": bw, "depth": bd, "thickness": bt}),
        Feature(id="bearing_boss", kind="boss", description="raised cylindrical bearing boss",
                params={"od": boss_od, "height": boss_h}, at=[0, 0, bt]),
        Feature(id="shaft_bore", kind="hole", description="through bore for the shaft",
                params={"diameter": bore}, at=[0, 0, 0]),
        *mount,
    ]
    return CadPlan(
        object_type="bearing_block", name="bearing block",
        assumptions=[
            f"Boss Ø{boss_od}×{boss_h}mm (boss height inferred), Ø{bore}mm through bore",
            f"4× M6 ({hd}mm) mounting holes inset ~18%/25% from the base edges",
        ],
        features=features,
        expected=Expected(bbox_mm={"x": bw, "y": bd, "z": bt + boss_h},
                          hole_count=1 + len(mount), through_hole_count=1 + len(mount)),
    )


def _plan_vise_jaw(t: str) -> CadPlan:
    length = _near(t, "long", "length") or 100
    tall = _near(t, "tall", "high") or 30
    thick = _near(t, "thick", "deep") or 20
    angle = _near(t, "degree", "deg") or 90
    sc = _screws(t)
    hd = sc[0] if sc else 6.6
    holes_n = _count(t, "holes", "mounting holes") or 2
    holes = [
        Feature(id=f"mounting_hole_{i}", kind="hole", axis="y", description="mounting hole",
                params={"diameter": hd}, at=[x, 0, tall / 2])
        for i, x in enumerate([-length / 3, length / 3][:holes_n])
    ]
    features = [
        Feature(id="jaw_body", kind="box", description="jaw body",
                params={"width": length, "depth": thick, "height": tall}),
        *holes,
        Feature(id="v_groove", kind="v_groove", description=f"{int(angle)}° V groove along the top",
                params={"angle": angle, "depth": min(tall * 0.4, 8)}),
    ]
    return CadPlan(
        object_type="vise_jaw", name="vise jaw",
        assumptions=[f"{int(angle)}° V groove along the top; {holes_n} mounting holes"],
        features=features,
        expected=Expected(bbox_mm={"x": length, "y": thick, "z": tall},
                          hole_count=holes_n, through_hole_count=holes_n),
    )


def _plan_clamp_block(t: str) -> CadPlan:
    """Split clamp block for a round tube/rod: flat mounting base + clamp body
    with a horizontal tube bore, a vertical split (clamp gap) above the bore, and
    tightening bolt holes crossing the gap. Never a plain block on a plate."""
    tube = _near(t, "round tube", "round bar", "tube", "pipe", "rod", "bar", "shaft") or 25.0
    sc = _screws(t)
    bolt_d, bolt_label = (sc[0], sc[1]) if sc else (6.6, "M6")
    bolt_n = max(2, _count(t, "bolts", "bolt holes", "screws") or 2)

    # Inferred proportions (all recorded as assumptions).
    body_w = round(max(2.0 * tube, tube + 24), 1)   # along the tube axis (x)
    body_d = round(max(1.8 * tube, tube + 20), 1)   # across the tube (y)
    body_h = round(max(2.2 * tube, tube + 28), 1)   # vertical
    base_t = 10.0
    base_w = body_w + 36.0                          # room for base mounting holes
    base_d = max(body_d, 50.0)
    gap_w = 3.0
    bore_z = base_t + body_h / 2                    # bore center height
    top_z = base_t + body_h
    bolt_z = (bore_z + tube / 2 + top_z) / 2        # between bore top and body top
    bolt_xs = [-body_w / 4, body_w / 4]
    mount_d = 6.6
    mx, my = base_w / 2 - 9, base_d / 2 - 9

    features = [
        Feature(id="base_plate", kind="plate", description="flat mounting base",
                params={"width": base_w, "depth": base_d, "thickness": base_t}),
        Feature(id="clamp_body", kind="box", description="clamp body above the base",
                params={"width": body_w, "depth": body_d, "height": body_h},
                at=[0, 0, base_t]),
        Feature(id="tube_bore", kind="hole", axis="x",
                description=f"Ø{tube}mm horizontal tube bore through the clamp body",
                params={"diameter": tube}, at=[0, 0, bore_z]),
        Feature(id="clamp_gap", kind="box", op="cut",
                description="vertical split / clamp gap from the bore to the top",
                params={"width": body_w + 2, "depth": gap_w,
                        "height": top_z - bore_z + 1}, at=[0, 0, bore_z]),
    ]
    for i, bx in enumerate(bolt_xs[:bolt_n], start=1):
        features.append(Feature(
            id=f"tightening_bolt_{i}", kind="hole", axis="y",
            description="tightening bolt clearance hole crossing the clamp gap",
            params={"diameter": bolt_d}, at=[bx, 0, bolt_z]))
    for i, (x, y) in enumerate([(-mx, -my), (mx, -my), (-mx, my), (mx, my)]):
        features.append(Feature(
            id=f"mounting_hole_{i}", kind="hole",
            description="base mounting hole",
            params={"diameter": mount_d}, at=[x, y, 0]))

    n_holes = 1 + bolt_n + 4
    return CadPlan(
        object_type="tube_clamp_block", name="tube clamp block",
        assumptions=[
            f"Clamp body {body_w}×{body_d}×{body_h}mm sized from the Ø{tube}mm tube",
            f"Base {base_w}×{base_d}×{base_t}mm flat mounting base with 4× M6 (6.6mm) holes",
            f"{bolt_n}× {bolt_label} ({bolt_d}mm) tightening bolt holes crossing a {gap_w}mm clamp gap",
            "Tube bore horizontal (along X), centered in the clamp body",
        ],
        features=features,
        expected=Expected(bbox_mm={"x": base_w, "y": max(base_d, body_d), "z": top_z},
                          hole_count=n_holes, through_hole_count=n_holes),
    )


def _plan_robotic_arm_base_bracket(t: str) -> CadPlan:
    """Robotic-arm base mount: a circular OR rectangular base plate, a vertical
    support tower (rectangular wall), two side gussets bracing the tower to the
    base, base mounting holes on a bolt circle/grid, and an optional bearing
    pocket (counterbored recess) on top of the tower. Never collapses to a flat
    plate when a tower / gussets / bearing pocket are requested."""
    circular = bool(re.search(r"\bcircular\b|\bround\b", t))
    base_dia = _near(t, "base diameter", "diameter", "od") or 120.0
    base_w = _near(t, "base width", "wide", "width") or base_dia
    base_d = _near(t, "base depth", "deep", "depth") or base_dia
    base_t = _near(t, "base thick", "base thickness", "thick") or 10.0
    tower_h = _near(t, "tower", "tall", "high", "vertical support") or 90.0
    tower_w = _near(t, "tower width") or max(40.0, (base_dia if circular else min(base_w, base_d)) * 0.5)
    tower_thk = 12.0
    sc = _screws(t)
    bolt_d, bolt_label = (sc[0], sc[1]) if sc else (6.6, "M6")
    n_base_holes = _count(t, "holes", "mounting holes", "bolt holes") or 4
    bearing = bool(re.search(r"\bbearing\b", t))
    bearing_dia = _near(t, "bearing pocket", "bearing bore", "bearing") or 52.0

    features: list[Feature] = []
    if circular:
        features.append(Feature(
            id="base_plate", kind="cylinder", description="circular base plate",
            params={"diameter": base_dia, "height": base_t}))
        envelope_x = envelope_y = base_dia
    else:
        features.append(Feature(
            id="base_plate", kind="plate", description="rectangular base plate",
            params={"width": base_w, "depth": base_d, "thickness": base_t}))
        envelope_x, envelope_y = base_w, base_d

    # Vertical support tower standing on the base, centered.
    features.append(Feature(
        id="support_tower", kind="rectangular_wall", description="vertical support tower",
        params={"width": tower_w, "depth": tower_thk, "height": tower_h},
        at=[0, 0, base_t]))
    # Two triangular gussets bracing the tower front/back to the base.
    gus_len = min(tower_h * 0.5, (base_d if not circular else base_dia) * 0.35)
    gus_h = min(tower_h * 0.6, gus_len * 1.4)
    features.append(Feature(
        id="gusset_front", kind="gusset", description="front bracing gusset",
        params={"length": gus_len, "height": gus_h, "thickness": 8.0},
        at=[0, tower_thk / 2, base_t]))
    features.append(Feature(
        id="gusset_back", kind="gusset", description="back bracing gusset",
        params={"length": gus_len, "height": gus_h, "thickness": 8.0},
        at=[0, -tower_thk / 2 - 8.0, base_t]))

    # Base mounting holes on a bolt circle (circular) or corner grid (rectangular).
    import math as _math
    if circular:
        r = base_dia / 2 - max(10.0, bolt_d * 1.5)
        for i in range(n_base_holes):
            ang = 2 * _math.pi * i / max(1, n_base_holes)
            features.append(Feature(
                id=f"base_hole_{i}", kind="hole", description="base mounting hole",
                params={"diameter": bolt_d},
                at=[round(r * _math.cos(ang), 2), round(r * _math.sin(ang), 2), 0]))
    else:
        hx, hy = base_w / 2 - 12, base_d / 2 - 12
        corners = [(-hx, -hy), (hx, -hy), (-hx, hy), (hx, hy)]
        for i, (x, y) in enumerate(corners[:n_base_holes]):
            features.append(Feature(
                id=f"base_hole_{i}", kind="hole", description="base mounting hole",
                params={"diameter": bolt_d}, at=[x, y, 0]))

    n_holes = n_base_holes
    if bearing:
        # Counterbored bearing pocket recessed into the top of the tower.
        features.append(Feature(
            id="bearing_pocket", kind="counterbore", axis="y",
            description="bearing pocket recess on the support tower",
            params={"diameter": max(8.0, bearing_dia * 0.5),
                    "counterbore_diameter": bearing_dia,
                    "counterbore_depth": min(10.0, tower_thk * 0.6)},
            at=[0, tower_thk / 2, base_t + tower_h * 0.7]))
        n_holes += 1

    assumptions = [
        ("Circular" if circular else "Rectangular")
        + f" base, vertical support tower {tower_w:g}×{tower_h:g}mm, two side gussets",
        f"{n_base_holes}× {bolt_label} ({bolt_d:g}mm) base mounting holes",
    ]
    if bearing:
        assumptions.append(f"Ø{bearing_dia:g}mm bearing pocket recessed into the tower")
    env_z = base_t + tower_h
    return CadPlan(
        object_type="robotic_arm_base_bracket", name="robotic arm base bracket",
        assumptions=assumptions, features=features,
        expected=Expected(bbox_mm={"x": envelope_x, "y": max(envelope_y, tower_thk + 8),
                                   "z": env_z},
                          hole_count=n_holes, through_hole_count=n_base_holes),
    )


def _plan_screwdriver(t: str) -> CadPlan:
    """A single FUSED screwdriver along +X: cylindrical handle -> coaxial metal
    shaft -> tip (flat blade box, or an approximate point for Phillips/unspecified).
    Adjacent parts overlap so the union is one connected body. Built with the
    `at=[0, 0, x_start]` convention so an axis='x' cylinder spans X exactly."""
    phillips = bool(re.search(r"phillip|philips|pozidriv|cross[- ]?head|\bcross\b", t))
    flat = bool(re.search(r"flat|slotted|flathead|flat[- ]?blade|flat[- ]?head", t))

    handle_dia = _near(t, "handle diameter", "handle dia", "mm handle") or 30.0
    shaft_dia = _near(t, "shaft diameter", "shaft dia")
    shaft_len = _near(t, "shaft length", "metal shaft", "blade length")
    bare_shaft = _near(t, "shaft")
    if bare_shaft is not None:
        if bare_shaft <= 20 and shaft_dia is None:
            shaft_dia = bare_shaft
        elif bare_shaft > 20 and shaft_len is None:
            shaft_len = bare_shaft
    shaft_dia = shaft_dia or 6.0
    handle_len = _near(t, "handle length", "long handle", "handle long")
    tip_len = _near(t, "tip length") or 12.0
    tip_w = (_near(t, "tip width", "wide flat tip", "wide tip", "flat tip", "mm tip")
             or (max(shaft_dia, 8.0) if flat else shaft_dia))
    total = _near(t, "long", "length", "overall")
    # Don't mistake "100mm long handle" for the overall length.
    if total is not None and handle_len is not None and abs(total - handle_len) < 1e-6:
        total = None

    if total:
        if shaft_len and not handle_len:
            handle_len = total - shaft_len - tip_len
        elif handle_len and not shaft_len:
            shaft_len = total - handle_len - tip_len
        elif not handle_len and not shaft_len:
            handle_len = round(total * 0.5, 1)
            shaft_len = total - handle_len - tip_len
    handle_len = max(20.0, handle_len or 100.0)
    shaft_len = max(10.0, shaft_len or 90.0)

    emb = min(handle_len * 0.2, 20.0)        # shaft embeds into the handle
    s0, s1 = handle_len - emb, handle_len + shaft_len   # shaft X interval
    t0 = s1 - 2.0                            # tip overlaps the shaft by 2mm
    total_len = handle_len + shaft_len + tip_len

    features = [
        Feature(id="handle", kind="cylinder", axis="x", description="cylindrical handle",
                params={"diameter": handle_dia, "height": handle_len}, at=[0, 0, 0.0]),
        Feature(id="shaft", kind="cylinder", axis="x", description="metal shaft",
                params={"diameter": shaft_dia, "height": s1 - s0}, at=[0, 0, s0]),
    ]
    if flat:
        blade_thk = max(2.0, round(shaft_dia * 0.5, 1))
        features.append(Feature(
            id="tip", kind="box", description="flat blade tip",
            params={"width": tip_len + 2.0, "depth": tip_w, "height": blade_thk},
            at=[(t0 + s1 + tip_len) / 2, 0, -blade_thk / 2]))
        z_env = max(handle_dia, blade_thk, shaft_dia)
        tip_label = "flat blade"
    else:
        features.append(Feature(
            id="tip", kind="cylinder", axis="x", description="screwdriver tip",
            params={"diameter": max(2.0, round(shaft_dia * 0.9, 1)), "height": tip_len + 2.0},
            at=[0, 0, t0]))
        z_env = max(handle_dia, shaft_dia)
        tip_label = "Phillips" if phillips else "tip"

    assumptions = [
        f"Single fused tool along X: handle Ø{handle_dia:g}×{handle_len:g}mm, "
        f"shaft Ø{shaft_dia:g}×{shaft_len:g}mm, {tip_label} tip {tip_len:g}mm "
        f"(overall ~{total_len:g}mm).",
        "Concept tool — not a manufacturing-certified screwdriver.",
    ]
    if phillips:
        assumptions.append(
            "Phillips/cross tip is approximate (modeled as a tapered point, not a "
            "true cruciform).")
    elif not flat:
        assumptions.append(
            "Tip style unspecified; modeled as a simple point — say 'flat blade' or "
            "'Phillips' to refine.")

    return CadPlan(
        object_type="screwdriver", name="screwdriver",
        assumptions=assumptions, features=features,
        expected=Expected(
            bbox_mm={"x": round(total_len, 1), "y": round(max(handle_dia, tip_w), 1),
                     "z": round(z_env, 1)},
            hole_count=0, through_hole_count=0),
    )


def _plan_motor_plate(t: str) -> CadPlan:
    sq = _near(t, "square") or _near(t, "mm square")
    width = depth = sq or 70
    thk = _near(t, "thick") or 5
    bore = _near(t, "center bore", "bore") or 22
    pat = _near(t, "square bolt pattern", "bolt pattern", "square pattern") or 31
    sc = _screws(t)
    hd = sc[0] if sc else 3.4
    holes_n = _count(t, "holes") or 4
    features = [
        Feature(id="base_plate", kind="plate", description="motor mounting plate",
                params={"width": width, "depth": depth, "thickness": thk}),
        Feature(id="center_bore", kind="hole", description="center bore",
                params={"diameter": bore}, at=[0, 0, 0]),
        Feature(id="mounting_holes", kind="hole_pattern_rect", description="NEMA bolt pattern",
                params={"diameter": hd, "pattern": pat, "nx": 2, "ny": 2}),
    ]
    return CadPlan(
        object_type="motor_mount_plate", name="NEMA motor plate",
        assumptions=[f"Ø{bore}mm center bore; {holes_n}× clearance holes on a {pat}mm square pattern"],
        features=features,
        expected=Expected(bbox_mm={"x": width, "y": depth, "z": thk},
                          hole_count=1 + holes_n, through_hole_count=1 + holes_n),
    )


# Raspberry Pi board footprints (mm) → preset OUTER enclosure box so a "Raspberry
# Pi N enclosure" with no explicit box size still builds deterministically (board +
# wall + clearance + standoff height). Both Pi 4 and Pi 5 share the 85×56mm HAT
# footprint. REVIEW-grade assumption, surfaced to the user.
_RPI_ENCLOSURE_PRESET = {
    "raspberry pi 5": {"width": 95, "depth": 66, "height": 30},
    "raspberry pi 4": {"width": 95, "depth": 66, "height": 30},
    "raspberry pi": {"width": 95, "depth": 66, "height": 30},
}


def _rpi_preset(t: str) -> tuple[str, dict] | None:
    for key in ("raspberry pi 5", "raspberry pi 4", "raspberry pi"):
        if key in t or t.replace("rpi", "raspberry pi").find(key) >= 0:
            return key, _RPI_ENCLOSURE_PRESET[key]
    if "rpi" in t:
        return "raspberry pi", _RPI_ENCLOSURE_PRESET["raspberry pi"]
    return None


def _plan_enclosure(t: str) -> CadPlan:
    from app.cad.plan.defaults import CAD_DEFAULTS

    d = CAD_DEFAULTS["enclosure"]
    rpi = _rpi_preset(t)
    base = dict(rpi[1]) if rpi else d
    w = _near(t, "wide", "width") or base["width"]
    depth = _near(t, "deep", "depth") or base["depth"]
    h = _near(t, "tall", "high", "height") or base["height"]
    wall = _near(t, "wall thickness", "wall", "walls")
    # Contradictory walls (thicker than the cavity) are FATAL — can't be built.
    if wall and 2 * wall >= min(w, depth, h):
        return _clarify("electronics_enclosure",
                        f"The {wall}mm walls are thicker than the {min(w, depth, h)}mm "
                        "cavity — please give a larger box or thinner walls.")
    wall = wall or d["wall_thickness"]
    sensor = _near(t, "sensor hole", "sensor") or (d["sensor_hole_diameter"] if "sensor" in t else 0)
    is_sensor = "sensor" in t or sensor > 0
    mount_d = d["mounting_hole_diameter"]
    mx, mz = w / 2 - 12, h / 2 - 12
    features = [
        Feature(id="enclosure_body", kind="box", description="enclosure body",
                params={"width": w, "depth": depth, "height": h}),
        Feature(id="shell_cavity", kind="shell", description="top (removable plate/lid)",
                params={"thickness": wall}),
    ]
    assumptions = [
        f"Enclosure {int(w)}×{int(depth)}×{int(h)}mm, {wall}mm walls, removable back plate/lid",
    ]
    if rpi:
        assumptions.insert(0, f"{rpi[0].title()} enclosure: outer box sized from the "
                              "board footprint + wall + clearance (REVIEW the fit).")
    # Secondary cosmetic features named in the prompt are NOT modeled by the
    # deterministic enclosure (they must never block fast generation). Flag them so
    # the part ships REVIEW with honest assumptions rather than a silent omission.
    for feat, note in (
        ("snap-fit", "Snap-fit lid not modeled — a removable plate/lid is provided instead."),
        ("snap fit", "Snap-fit lid not modeled — a removable plate/lid is provided instead."),
        ("vent", "Ventilation slots not modeled (cosmetic) — add them in CAD if required."),
        ("logo", "Logo emboss not modeled — a flat top is provided as the logo placement area."),
        ("emboss", "Logo emboss not modeled — a flat top is provided as the logo placement area."),
    ):
        if feat in t and note not in assumptions:
            assumptions.append(note)
    if is_sensor:
        features.append(Feature(id="sensor_hole", kind="hole", axis="y",
                                description="front-face sensor hole",
                                params={"diameter": sensor or d["sensor_hole_diameter"]},
                                at=[0, 0, h / 2]))
        assumptions.append(f"Ø{int(sensor or d['sensor_hole_diameter'])}mm sensor hole centered on the front face")
    for i, (x, z) in enumerate([(-mx, mz * 0.6), (mx, mz * 0.6), (-mx, -mz * 0.6 + h / 2), (mx, -mz * 0.6 + h / 2)][:4]):
        features.append(Feature(id=f"mounting_hole_{i}", kind="hole", axis="y",
                                description="back mounting hole",
                                params={"diameter": mount_d}, at=[x, 0, max(8, z if z > 0 else h * 0.3)]))
    assumptions.append(f"4× M4 clearance ({mount_d}mm) mounting holes on the back")
    return CadPlan(
        object_type="sensor_enclosure" if is_sensor else "electronics_enclosure",
        name="sensor enclosure" if is_sensor else "electronics enclosure",
        assumptions=assumptions, features=features,
        expected=Expected(bbox_mm={"x": w, "y": depth, "z": h}),
    )


def _plan_plate(t: str) -> CadPlan:
    length = _near(t, "long", "length")
    width = _near(t, "wide", "width")
    thk = _near(t, "thick")
    square = _near(t, "square")
    if square:  # "100mm square" -> equal sides
        length = width = square
    if not (length and width and thk):
        ns = _nums(t)
        if length and not width and len(ns) >= 2:
            width = length
        if not (length and width and thk) and len(ns) >= 3:
            length, width, thk = length or ns[0], width or ns[1], thk or ns[2]
        if not (length and width and thk):
            return _clarify("plate",
                            "What are the plate length, width and thickness (mm)?")
    sc = _screws(t)
    hd = sc[0] if sc else 6.6
    holes_n = _count(t, "holes", "corner") or 0
    fillet = _near(t, "rounded", "fillet", "radius") if ("round" in t or "fillet" in t) else None
    bore = _near(t, "center bore", "centre bore", "bore")
    features = [Feature(id="base_plate", kind="plate", description="rectangular plate",
                        params={"width": length, "depth": width, "thickness": thk})]
    if fillet:
        features.append(Feature(id="edge_fillet", kind="fillet", description="rounded vertical edges",
                                params={"radius": fillet}))
    n_holes = 0
    if bore:
        features.append(Feature(id="center_bore", kind="hole", description="center bore",
                                params={"diameter": bore}, at=[0, 0, 0]))
        n_holes += 1
    inset = 10.0
    corners = [(-(length / 2 - inset), -(width / 2 - inset)),
               (length / 2 - inset, -(width / 2 - inset)),
               (-(length / 2 - inset), width / 2 - inset),
               (length / 2 - inset, width / 2 - inset)]
    for i, (x, y) in enumerate(corners[:holes_n]):
        features.append(Feature(id=f"mounting_hole_{i}", kind="hole", description="corner mounting hole",
                                params={"diameter": hd}, at=[x, y, 0]))
        n_holes += 1
    assumptions = []
    if sc and holes_n:
        assumptions.append(f"{holes_n}× {sc[1]} clearance holes ({hd}mm), {int(inset)}mm from each corner")
    if fillet:
        assumptions.append(f"{fillet}mm rounded vertical edges")
    if bore:
        assumptions.append(f"Ø{bore}mm center bore")
    return CadPlan(
        object_type="mounting_plate", name="rectangular mounting plate",
        assumptions=assumptions, features=features,
        expected=Expected(bbox_mm={"x": length, "y": width, "z": thk},
                          hole_count=n_holes, through_hole_count=n_holes),
    )


# --- everyday concept-fallback families ------------------------------------
# These produce a SINGLE connected concept solid (overlapping primitives) for
# common objects, so a casual prompt yields safe, valid, clearly-labelled concept
# CAD instead of broken free-form geometry. Default dimensions are documented in
# app.cad.standards.defaults.CONCEPT_DEFAULTS; each builder records the assumed
# dimensions and a "concept, not manufacturing-certified" note. Holes/bbox intent
# is reconciled from the features by normalize_cad_plan, so we don't hand-set them.

_CONCEPT_NOTE = "Concept CAD — simplified model, not manufacturing-certified."


def _concept_defaults(key: str) -> dict:
    from app.cad.standards.defaults import CONCEPT_DEFAULTS
    return CONCEPT_DEFAULTS[key]


def _concept_plan(object_type: str, name: str, features: list[Feature],
                  assumptions: list[str]) -> CadPlan:
    return CadPlan(
        object_type=object_type, name=name,
        assumptions=[*assumptions, _CONCEPT_NOTE], features=features,
        expected=Expected(),  # bbox/holes filled from features by normalize
    )


def _plan_hammer(t: str) -> CadPlan:
    d = _concept_defaults("hammer")
    hd = _near(t, "handle diameter", "handle dia") or d["handle_diameter"]
    hl = _near(t, "handle length", "long handle") or _near(t, "long", "length") or d["handle_length"]
    head_l, head_w, head_h = d["head_length"], d["head_width"], d["head_height"]
    features = [
        Feature(id="handle", kind="cylinder", axis="z", description="hammer handle",
                params={"diameter": hd, "height": hl}, at=[0, 0, 0]),
        Feature(id="head", kind="box", description="hammer head (overlaps handle top)",
                params={"width": head_l, "depth": head_w, "height": head_h},
                at=[0, 0, hl - head_h / 2]),
    ]
    return _concept_plan(
        "hammer", "hammer", features,
        [f"Concept hammer: Ø{hd:g}×{hl:g}mm handle with a {head_l:g}×{head_w:g}×"
         f"{head_h:g}mm head fused on top (single connected body)."])


def _plan_wrench(t: str) -> CadPlan:
    d = _concept_defaults("wrench")
    length = _near(t, "long", "length") or d["handle_length"]
    width, thk = d["handle_width"], d["thickness"]
    head_d, jaw = d["head_diameter"], d["jaw_opening"]
    features = [
        Feature(id="handle", kind="box", description="wrench handle bar",
                params={"width": length, "depth": width, "height": thk}, at=[0, 0, 0]),
        Feature(id="head", kind="boss", description="ring head (overlaps handle end)",
                params={"diameter": head_d, "height": thk}, at=[length / 2, 0, 0]),
        Feature(id="jaw", kind="hole", description="jaw opening",
                params={"diameter": jaw}, at=[length / 2, 0, 0]),
    ]
    return _concept_plan(
        "wrench", "wrench", features,
        [f"Concept ring-end wrench: {length:g}mm handle, Ø{head_d:g}mm head with a "
         f"Ø{jaw:g}mm opening (single connected body)."])


def _plan_pliers(t: str) -> CadPlan:
    d = _concept_defaults("pliers")
    piv, thk = d["pivot_diameter"], d["thickness"]
    jl, hl, aw = d["jaw_length"], d["handle_length"], d["arm_width"]
    off = aw / 2 + 0.5
    features = [
        Feature(id="pivot", kind="boss", description="pivot boss",
                params={"diameter": piv, "height": thk}, at=[0, 0, 0]),
        Feature(id="jaw_upper", kind="box", description="upper jaw",
                params={"width": jl, "depth": aw, "height": thk - 2}, at=[jl / 2 - 4, off, 1]),
        Feature(id="jaw_lower", kind="box", description="lower jaw",
                params={"width": jl, "depth": aw, "height": thk - 2}, at=[jl / 2 - 4, -off, 1]),
        Feature(id="handle_upper", kind="box", description="upper handle",
                params={"width": hl, "depth": aw, "height": thk - 2}, at=[-hl / 2 + 4, off, 1]),
        Feature(id="handle_lower", kind="box", description="lower handle",
                params={"width": hl, "depth": aw, "height": thk - 2}, at=[-hl / 2 + 4, -off, 1]),
        Feature(id="pin_hole", kind="hole", description="pivot pin hole",
                params={"diameter": d["pivot_hole"]}, at=[0, 0, 0]),
    ]
    return _concept_plan(
        "pliers", "pliers", features,
        [f"Concept pliers: two jaws + two handles fused at a Ø{piv:g}mm pivot "
         "(single connected body; modeled in the open position)."])


def _plan_wheel(t: str) -> CadPlan:
    d = _concept_defaults("wheel")
    od = _near(t, "diameter", "dia", "wide") or d["diameter"]
    thk = _near(t, "thick", "wide") or d["thickness"]
    bore = _near(t, "bore", "hub bore") or d["bore"]
    features = [
        Feature(id="rim", kind="cylinder", axis="z", description="wheel disc / rim",
                params={"diameter": od, "height": thk}, at=[0, 0, 0]),
        Feature(id="hub", kind="boss", description="raised hub",
                params={"diameter": d["hub_diameter"], "height": d["hub_height"]}, at=[0, 0, 0]),
        Feature(id="bore", kind="hole", description="axle bore",
                params={"diameter": bore}, at=[0, 0, 0]),
        Feature(id="lightening_holes", kind="hole_pattern_circle",
                description="lightening / spoke holes",
                params={"count": d["spoke_holes"], "diameter": d["spoke_hole_diameter"],
                        "pcd": (od + d["hub_diameter"]) / 2}, at=[0, 0, 0]),
    ]
    return _concept_plan(
        "wheel", "wheel", features,
        [f"Concept wheel: Ø{od:g}×{thk:g}mm disc, raised hub, Ø{bore:g}mm axle bore "
         f"and {int(d['spoke_holes'])} lightening holes (single connected body)."])


def _plan_fan_blade(t: str) -> CadPlan:
    d = _concept_defaults("fan_blade")
    bl, bw, bt = d["blade_length"], d["blade_width"], d["blade_thickness"]
    hub_d, hub_h = d["hub_diameter"], d["hub_height"]
    off = bl / 2 + hub_d / 4
    z = (hub_h - bt) / 2
    features = [
        Feature(id="hub", kind="boss", description="fan hub",
                params={"diameter": hub_d, "height": hub_h}, at=[0, 0, 0]),
        Feature(id="blade_px", kind="box", description="blade +X",
                params={"width": bl, "depth": bw, "height": bt}, at=[off, 0, z]),
        Feature(id="blade_nx", kind="box", description="blade -X",
                params={"width": bl, "depth": bw, "height": bt}, at=[-off, 0, z]),
        Feature(id="blade_py", kind="box", description="blade +Y",
                params={"width": bw, "depth": bl, "height": bt}, at=[0, off, z]),
        Feature(id="blade_ny", kind="box", description="blade -Y",
                params={"width": bw, "depth": bl, "height": bt}, at=[0, -off, z]),
        Feature(id="bore", kind="hole", description="shaft bore",
                params={"diameter": d["bore"]}, at=[0, 0, 0]),
    ]
    return _concept_plan(
        "fan_blade", "fan blade", features,
        [f"Concept 4-blade fan: Ø{hub_d:g}mm hub with four {bl:g}×{bw:g}mm flat "
         "blades fused around it (single connected body).",
         "Blades are flat (no aerodynamic twist) — concept geometry only."])


def _plan_hook(t: str) -> CadPlan:
    d = _concept_defaults("hook")
    pw, pt, ph = d["plate_width"], d["plate_thickness"], d["plate_height"]
    arm, sz, tip = d["arm_length"], d["arm_size"], d["tip_height"]
    mh = d["mount_hole"]
    arm_z = ph - sz - 6
    features = [
        Feature(id="back_plate", kind="box", description="wall mounting plate",
                params={"width": pw, "depth": pt, "height": ph}, at=[0, 0, 0]),
        Feature(id="mount_top", kind="hole", axis="y", description="upper mounting hole",
                params={"diameter": mh}, at=[0, 0, ph - 15]),
        Feature(id="mount_bot", kind="hole", axis="y", description="lower mounting hole",
                params={"diameter": mh}, at=[0, 0, 15]),
        Feature(id="arm", kind="box", description="hook arm (out from plate)",
                params={"width": sz, "depth": arm, "height": sz},
                at=[0, pt / 2 + arm / 2 - 3, arm_z]),
        Feature(id="tip", kind="box", description="upturned hook tip",
                params={"width": sz, "depth": sz, "height": tip},
                at=[0, pt / 2 + arm - sz / 2 - 3, arm_z]),
    ]
    return _concept_plan(
        "hook", "hook", features,
        [f"Concept wall hook: {pw:g}×{ph:g}mm back plate with two Ø{mh:g}mm mounting "
         f"holes and an {arm:g}mm arm with an upturned tip (single connected body)."])


def _plan_handle_grip(t: str) -> CadPlan:
    d = _concept_defaults("handle")
    gl, gw, gh = d["grip_length"], d["grip_width"], d["grip_height"]
    leg, stand = d["leg_diameter"], d["standoff"]
    mh = d["mount_hole"]
    legx = gl / 2 - leg
    grip_z = stand
    features = [
        Feature(id="grip", kind="box", description="grip bar",
                params={"width": gl, "depth": gw, "height": gh}, at=[0, 0, grip_z]),
        Feature(id="leg_left", kind="cylinder", axis="z", description="left standoff",
                params={"diameter": leg, "height": stand + 4}, at=[-legx, 0, 0]),
        Feature(id="leg_right", kind="cylinder", axis="z", description="right standoff",
                params={"diameter": leg, "height": stand + 4}, at=[legx, 0, 0]),
        Feature(id="mount_left", kind="hole", description="left mounting hole",
                params={"diameter": mh}, at=[-legx, 0, 0]),
        Feature(id="mount_right", kind="hole", description="right mounting hole",
                params={"diameter": mh}, at=[legx, 0, 0]),
    ]
    return _concept_plan(
        "handle_grip", "handle / pull grip", features,
        [f"Concept pull handle: {gl:g}mm grip bar on two Ø{leg:g}mm standoffs with "
         f"Ø{mh:g}mm mounting holes (single connected body)."])


def _plan_tool_holder(t: str) -> CadPlan:
    d = _concept_defaults("tool_holder")
    w, depth, thk = d["width"], d["depth"], d["thickness"]
    bh, bt = d["back_height"], d["back_thickness"]
    n = int(d["tool_holes"])
    hd = d["tool_hole_diameter"]
    features = [
        Feature(id="base", kind="plate", description="tool holder base",
                params={"width": w, "depth": depth, "thickness": thk}, at=[0, 0, 0]),
        Feature(id="back_wall", kind="box", description="back wall (L profile)",
                params={"width": w, "depth": bt, "height": bh}, at=[0, -depth / 2 + bt / 2, 0]),
    ]
    span = w - 60
    for i in range(n):
        x = -span / 2 + (span * i / (n - 1) if n > 1 else 0)
        features.append(Feature(id=f"tool_hole_{i}", kind="hole",
                                description="tool slot", params={"diameter": hd},
                                at=[x, depth * 0.15, 0]))
    return _concept_plan(
        "tool_holder", "tool holder", features,
        [f"Concept tool holder: {w:g}×{depth:g}mm base with a {bh:g}mm back wall and "
         f"{n}× Ø{hd:g}mm tool slots (single connected body)."])


def _plan_stand(t: str) -> CadPlan:
    d = _concept_defaults("stand")
    tw, td, tt = d["top_width"], d["top_depth"], d["top_thickness"]
    h, leg = d["height"], d["leg_diameter"]
    lx, ly = tw / 2 - leg, td / 2 - leg
    features = [
        Feature(id="top", kind="plate", description="stand top platform",
                params={"width": tw, "depth": td, "thickness": tt}, at=[0, 0, h]),
    ]
    for i, (sx, sy) in enumerate([(-lx, -ly), (lx, -ly), (-lx, ly), (lx, ly)]):
        features.append(Feature(id=f"leg_{i}", kind="cylinder", axis="z",
                                description="leg",
                                params={"diameter": leg, "height": h + 4}, at=[sx, sy, 0]))
    return _concept_plan(
        "stand", "stand", features,
        [f"Concept stand: {tw:g}×{td:g}mm top on four Ø{leg:g}mm legs, {h:g}mm tall "
         "(single connected body)."])


def _plan_casing(t: str) -> CadPlan:
    d = _concept_defaults("casing")
    w = _near(t, "wide", "width") or d["width"]
    depth = _near(t, "deep", "depth") or d["depth"]
    h = _near(t, "tall", "high", "height") or d["height"]
    wall = _near(t, "wall thickness", "wall", "walls") or d["wall"]
    if 2 * wall >= min(w, depth, h):
        wall = d["wall"]
    features = [
        Feature(id="casing_body", kind="box", description="casing body",
                params={"width": w, "depth": depth, "height": h}, at=[0, 0, 0]),
        Feature(id="casing_shell", kind="shell", description="hollowed cavity (open top)",
                params={"thickness": wall}),
    ]
    return _concept_plan(
        "casing", "simple casing", features,
        [f"Concept casing: {w:g}×{depth:g}×{h:g}mm box shelled to {wall:g}mm walls "
         "with an open top (single connected body)."])


# Ordered most-specific-first: the first matcher whose predicate fires wins.
# NB: use word boundaries — naive substrings misfire ("ear" in "clEARance"/
# "bEARing"; "t pipe" in "straighT PIPE").
_FAMILIES = [
    (lambda t: "screwdriver" in t or "screw driver" in t, _plan_screwdriver),
    (lambda t: bool(re.search(r"\btee\b|\bt[- ]?pipe\b", t)) or ("branch" in t and "pipe" in t), _plan_pipe_tee),
    (lambda t: "spool" in t or ("pipe" in t and "flange" in t and "both ends" in t), _plan_pipe_spool),
    (lambda t: "flange" in t and "pipe" not in t, _plan_flange),
    (lambda t: "vise" in t or "v groove" in t or "v-groove" in t, _plan_vise_jaw),
    (lambda t: "clamp" in t and bool(re.search(r"\btube\b|\bpipe\b|\brod\b|\bbar\b|\bshaft\b", t)), _plan_clamp_block),
    (lambda t: bool(re.search(r"\brobot(?:ic)?\b", t)) and "arm" in t
               and bool(re.search(r"\bbase\b|\bbracket\b|\btower\b|\bgusset\b|\bbearing\b", t)),
     _plan_robotic_arm_base_bracket),
    (lambda t: bool(re.search(r"\bu[- ]?(shaped|bracket)\b", t)), _plan_u_bracket),
    (lambda t: bool(re.search(r"\bhinge\b|\bears?\b", t)), _plan_hinge_bracket),
    (lambda t: "bearing" in t and ("block" in t or "housing" in t), _plan_bearing_block),
    (lambda t: "enclosure" in t or ("case" in t and "sensor" in t), _plan_enclosure),
    (lambda t: bool(re.search(r"\bl[- ]?bracket\b", t)) or ("vertical" in t and "wall" in t and "bracket" in t), _plan_l_bracket),
    (lambda t: ("nema" in t or "stepper" in t or "motor mount" in t or "motor mounting" in t), _plan_motor_plate),
    # Everyday concept-fallback families (single connected concept solids). Placed
    # after the precision families so a specific mechanical part is never stolen by
    # a loose everyday word.
    (lambda t: bool(re.search(r"\bhammer\b|\bmallet\b", t)), _plan_hammer),
    (lambda t: bool(re.search(r"\bwrench(?:es)?\b|\bspanner\b", t)), _plan_wrench),
    (lambda t: bool(re.search(r"\bpliers?\b", t)), _plan_pliers),
    (lambda t: bool(re.search(r"\bfan\b|\bimpeller\b", t)), _plan_fan_blade),
    (lambda t: bool(re.search(r"\bwheel\b", t)), _plan_wheel),
    (lambda t: bool(re.search(r"\bhook\b", t)), _plan_hook),
    (lambda t: bool(re.search(r"\btool[- ]?(holder|rack|organi[sz]er)\b", t)), _plan_tool_holder),
    (lambda t: bool(re.search(r"\bcasing\b", t)) or "simple case" in t, _plan_casing),
    (lambda t: bool(re.search(r"\bstand\b", t)), _plan_stand),
    (lambda t: (bool(re.search(r"\bhandle\b|\bgrip\b", t)) or "drawer pull" in t
                or "door pull" in t) and "screwdriver" not in t, _plan_handle_grip),
    (lambda t: "plate" in t, _plan_plate),
]


def plan(prompt: str) -> CadPlan | None:
    """Deterministic prompt → CadPlan, or None when no specific family is
    recognized (the caller then falls back to the legacy pipeline).

    Composes primitives; never selects a whole-part template. For a recognized
    family that's missing a critical dimension it returns a clarification CadPlan
    (so we ask instead of mis-routing); for unrecognized prompts it returns None.
    The mock provider is intentionally limited this way — the OpenAI planner
    handles arbitrary parts via the LLM.
    """
    t = prompt.lower()
    for predicate, builder in _FAMILIES:
        if predicate(t):
            return builder(t)
    return None
