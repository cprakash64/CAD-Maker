"""Deterministic CadQuery program generator (offline stand-in for the LLM coder).

Each family returns a (CADDesignBrief, CADProgramSpec) where the program's
`generated_code` is restricted CadQuery that assigns `result` and `meta`. The
code is a constant body prefixed with a numeric parameter header (built from the
prompt) — no brace-escaping, easy to audit. A real LLM produces equivalent code,
which is sandboxed the same way.
"""
from __future__ import annotations

import re

from app.schemas.brief import BriefHole, CADDesignBrief, CADGenerationMode, CADProgramSpec


def _num(text: str, *labels: str, default: float | None = None) -> float | None:
    for label in labels:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm|cm)?\s*" + re.escape(label), text)
        if m:
            return float(m.group(1))
    for label in labels:
        m = re.search(re.escape(label) + r"\D{0,4}(\d+(?:\.\d+)?)", text)
        if m:
            return float(m.group(1))
    return default


def _shaft(text: str, default=10.0) -> float:
    return _num(text, "shaft hole", "shaft", "bore", "for a", default=default) or default


def _screw_clear(text: str) -> float:
    m = re.search(r"m(\d+(?:\.\d+)?)", text)
    if m:
        return {3: 3.4, 4: 4.5, 5: 5.5, 6: 6.6, 8: 9.0, 10: 11.0}.get(int(float(m.group(1))), 6.6)
    return 6.6


# --- constant program bodies (reference names set by the numeric header) ----
_BEARING = """
boss_r = bore * 0.9 + 6.0
H = bore * 1.4 + 6.0
flange_r = boss_r + 12.0
base_h = 8.0
body = cq.Workplane('XY').circle(boss_r).extrude(H + base_h)
flange = cq.Workplane('XY').circle(flange_r).extrude(base_h)
part = body.union(flange)
part = part.cut(cq.Workplane('XY').circle(bore/2).extrude(H + base_h + 2).translate((0,0,-1)))
mr = (boss_r + flange_r) / 2.0
tools = cq.Workplane('XY').circle(3.3).extrude(base_h + 2).translate((mr, 0, -1))
for i in range(1, 4):
    a = math.radians(90.0 * i)
    tools = tools.union(cq.Workplane('XY').circle(3.3).extrude(base_h+2).translate((mr*math.cos(a), mr*math.sin(a), -1)))
part = part.cut(tools)
result = part
meta = {'object_type':'bearing_housing','features':['base','boss','shaft_bore','mounting_holes'],
        'feature_counts':{'holes':4},'holes':4,'bores':[bore]}
"""

_FLANGE_PLATE = """
od = pcd * 1.45
th = 12.0
center = pcd * 0.4
part = cq.Workplane('XY').circle(od/2).extrude(th)
part = part.cut(cq.Workplane('XY').circle(center/2).extrude(th+2).translate((0,0,-1)))
tools = cq.Workplane('XY')
for i in range(n):
    a = math.radians(360.0 * i / n)
    tools = tools.union(cq.Workplane('XY').circle(bd/2).extrude(th+2).translate((pcd/2*math.cos(a), pcd/2*math.sin(a), -1)))
part = part.cut(tools)
result = part
meta = {'object_type':'flange_plate','features':['plate','center_bore','bolt_circle'],
        'feature_counts':{'holes':n},'holes':n,'bores':[center],'pcd':pcd}
"""

_HEX = """
part = cq.Workplane('XY').polygon(6, across).extrude(th)
part = part.cut(cq.Workplane('XY').circle(bore/2).extrude(th+2).translate((0,0,-1)))
result = part
meta = {'object_type':'hexagonal_part','profile':'hex','features':['hex_profile','center_bore'],
        'feature_counts':{'holes':1},'holes':1,'bores':[bore]}
"""

_SPUR_GEAR = """
n = int(teeth)
rt = od/2.0
rr = od/2.0 - tooth_h
pts = []
for k in range(4*n):
    seg = k % 4
    r = rt if seg in (1, 2) else rr
    a = 2*math.pi*k/(4*n)
    pts.append((r*math.cos(a), r*math.sin(a)))
part = cq.Workplane('XY').polyline(pts).close().extrude(th)
part = part.cut(cq.Workplane('XY').circle(bore/2).extrude(th+2).translate((0,0,-1)))
result = part
meta = {'object_type':'spur_gear','profile':'toothed','features':['teeth','center_bore'],
        'feature_counts':{'holes':1,'teeth':int(teeth)},'holes':1,'bores':[bore]}
"""

_PULLEY = """
part = cq.Workplane('XY').circle(od/2).extrude(th)
groove = cq.Workplane('XY').circle(od/2 + 1).circle(od/2 - 4).extrude(th/3.0).translate((0,0,th/3.0))
part = part.cut(groove)
part = part.cut(cq.Workplane('XY').circle(bore/2).extrude(th+2).translate((0,0,-1)))
result = part
meta = {'object_type':'pulley','profile':'grooved','features':['disc','vee_groove','center_bore'],
        'feature_counts':{'holes':1},'holes':1,'bores':[bore]}
"""

_CBORE_BLOCK = """
W = 90.0; D = 50.0; H = 25.0
part = cq.Workplane('XY').box(W, D, H, centered=(True, True, False))
part = part.cut(cq.Workplane('XY').box(W*0.45, D+2, H*0.4, centered=(True,True,False)).translate((0,0,H*0.6)))
part = part.cut(cq.Workplane('XY').box(W*0.22, D+2, H, centered=(True,True,False)).translate((0,0,H*0.5)))
for sx in (-1, 1):
    cx = sx * W*0.32
    part = part.cut(cq.Workplane('XY').circle(3.3).extrude(H+2).translate((cx, D*0.28, -1)))
    part = part.cut(cq.Workplane('XY').circle(6.0).extrude(5.0).translate((cx, D*0.28, H-5.0)))
result = part
meta = {'object_type':'block_with_slot','features':['block','stepped_slot','counterbore'],
        'feature_counts':{'holes':2,'counterbores':2,'slots':1},'holes':2,'bores':[6.6]}
"""

_SHAFT_COLLAR = """
od = bore * 2.0
w = max(8.0, bore * 0.8)
part = cq.Workplane('XY').circle(od/2).extrude(w)
part = part.cut(cq.Workplane('XY').circle(bore/2).extrude(w+2).translate((0,0,-1)))
part = part.cut(cq.Workplane('XY').box(od, 2.5, w+2, centered=(True,True,False)).translate((od/2, 0, -1)))
screw = cq.Workplane('XY').circle(screw_d/2).extrude(od+6).rotate((0,0,0),(1,0,0),-90).translate((od*0.52, od/2+3, w/2))
part = part.cut(screw)
result = part
meta = {'object_type':'shaft_collar','features':['ring','clamp_slit','clamp_screw','bore'],
        'feature_counts':{'holes':1},'holes':1,'bores':[bore]}
"""

_PIPE_ELBOW = """
r = pipe_d/2; ir = max(1.0, r - 5.0); leg = pipe_d*1.6; ft = 12.0; fr = pipe_d*0.9
leg1 = cq.Workplane('XY').circle(r).extrude(leg)
leg2 = cq.Workplane('XY').circle(r).extrude(leg).rotate((0,0,0),(1,0,0),-90)
part = leg1.union(leg2)
f1 = cq.Workplane('XY').circle(fr).extrude(ft).translate((0,0,leg-ft))
f2 = cq.Workplane('XY').circle(fr).extrude(ft).rotate((0,0,0),(1,0,0),-90).translate((0, leg-ft, 0))
part = part.union(f1).union(f2)
part = part.cut(cq.Workplane('XY').circle(ir).extrude(leg+2).translate((0,0,-1)))
part = part.cut(cq.Workplane('XY').circle(ir).extrude(leg+2).rotate((0,0,0),(1,0,0),-90).translate((0,1,0)))
result = part
meta = {'object_type':'pipe_elbow','features':['pipe','elbow','flanges','bore'],
        'feature_counts':{'flanges':2},'bores':[pipe_d-10.0]}
"""

_VISE_JAW = """
W = 80.0; D = 30.0; H = 35.0
part = cq.Workplane('XY').box(W, D, H, centered=(True, True, False))
v = cq.Workplane('XZ').workplane(offset=-D/2).moveTo(0, H*0.6).polyline([(-8,H*0.6),(0,H*0.6-10),(8,H*0.6)]).close().extrude(D)
part = part.cut(v)
for sx in (-1, 1):
    part = part.cut(cq.Workplane('XY').circle(3.3).extrude(H+2).translate((sx*W*0.32, 0, -1)))
result = part
meta = {'object_type':'vise_jaw','features':['jaw','v_groove','mounting_holes'],
        'feature_counts':{'holes':2,'grooves':1},'holes':2}
"""

_NEMA17 = """
W = 60.0; th = 6.0; pcd_sq = 31.0; center = 22.0
part = cq.Workplane('XY').box(W, W, th, centered=(True, True, False))
part = part.cut(cq.Workplane('XY').circle(center/2).extrude(th+2).translate((0,0,-1)))
for sx in (-1, 1):
    for sy in (-1, 1):
        part = part.cut(cq.Workplane('XY').circle(1.6).extrude(th+2).translate((sx*pcd_sq/2, sy*pcd_sq/2, -1)))
for sx in (-1, 1):
    for sy in (-1, 1):
        part = part.cut(cq.Workplane('XY').circle(2.6).extrude(th+2).translate((sx*W*0.4, sy*W*0.4, -1)))
result = part
meta = {'object_type':'motor_mount_plate','features':['plate','center_bore','nema17_pattern','mounting_holes'],
        'feature_counts':{'holes':8},'holes':8,'bores':[center]}
"""


def generate_program(prompt: str):
    """Return (CADDesignBrief, CADProgramSpec) for a recognized family, else None."""
    t = prompt.lower()

    def prog(header, body, brief, expected_features, exports=("stl", "step")):
        return brief, CADProgramSpec(
            generation_mode=CADGenerationMode.cadquery_program, kernel="cadquery",
            generated_code=header + body, expected_features=expected_features,
            expected_exports=list(exports), assumptions=brief.assumptions,
            operations_summary=[f"cadquery program for {brief.object_family}"],
        )

    if "bearing housing" in t or ("bearing" in t and "housing" in t):
        bore = _shaft(t, 20.0)
        b = CADDesignBrief(object_type="bearing_housing", object_family="bearing_housing",
                           mechanical_function="support a rotating shaft via a bearing bore",
                           bores=[bore], holes=[BriefHole(purpose="mounting", count=4, diameter_mm=3.4)],
                           required_features=["shaft_bore", "base", "boss"],
                           assumptions=[f"{bore:g}mm shaft bore; 4 mounting holes (assumed)"])
        return prog(f"bore={bore}\n", _BEARING, b, ["shaft_bore", "base", "boss", "mounting_holes"])

    if "flange plate" in t or ("flange" in t and ("bolt circle" in t or "pcd" in t)) or (
        "plate" in t and "bolt circle" in t):
        n = int(_num(t, "holes", "bolts", "bolt", default=8) or 8)
        pcd = _num(t, "bolt circle", "pcd", "circle", default=100.0) or 100.0
        bd = _screw_clear(t)
        b = CADDesignBrief(object_type="flange_plate", object_family="flange_plate",
                           mechanical_function="bolted pipe/shaft flange",
                           bores=[pcd * 0.4],
                           holes=[BriefHole(purpose="bolt", count=n, pattern="bolt_circle",
                                            bolt_circle_diameter_mm=pcd, diameter_mm=bd)],
                           required_features=["plate", "bolt_circle", "center_bore"],
                           assumptions=[f"{n} holes on {pcd:g}mm PCD; center bore assumed"])
        return prog(f"n={n}\npcd={pcd}\nbd={bd}\n", _FLANGE_PLATE, b, ["bolt_circle", "center_bore"])

    if ("hex" in t or "hexagon" in t) and "gear" in t:
        bore = _shaft(t, 10.0)
        across = _num(t, "across", "outer", "od", "diameter", default=60.0) or 60.0
        b = CADDesignBrief(object_type="hexagonal_gear", object_family="hexagonal_gear",
                           mechanical_function="hex-profile gear/drive part",
                           bores=[bore], required_features=["hex_profile", "center_bore"],
                           assumptions=[f"hexagonal outer profile; {bore:g}mm bore; {across:g}mm across"])
        return prog(f"across={across}\nth=12.0\nbore={bore}\n", _HEX, b, ["hex_profile", "center_bore"])

    if "spur gear" in t or ("gear" in t and ("teeth" in t or "tooth" in t)):
        bore = _num(t, "bore", "shaft", default=10.0) or 10.0
        teeth = _num(t, "teeth", "tooth", default=24) or 24
        od = _num(t, "diameter", "od", default=80.0) or 80.0
        b = CADDesignBrief(object_type="spur_gear", object_family="spur_gear",
                           mechanical_function="toothed gear", bores=[bore],
                           required_features=["teeth", "center_bore"],
                           assumptions=[f"{int(teeth)} teeth; {bore:g}mm bore"])
        return prog(f"od={od}\nth=12.0\nbore={bore}\nteeth={teeth}\ntooth_h=4.0\n",
                    _SPUR_GEAR, b, ["teeth", "center_bore"])

    if "pulley" in t:
        bore = _shaft(t, 10.0)
        od = _num(t, "outer diameter", "od", "diameter", default=60.0) or 60.0
        b = CADDesignBrief(object_type="pulley", object_family="pulley",
                           mechanical_function="belt pulley", bores=[bore],
                           required_features=["vee_groove", "center_bore"],
                           assumptions=[f"{od:g}mm OD; {bore:g}mm bore; vee groove"])
        return prog(f"od={od}\nth=12.0\nbore={bore}\n", _PULLEY, b, ["vee_groove", "center_bore"])

    if ("hex" in t or "hexagon" in t) and ("spacer" in t or "standoff" in t or "nut" in t):
        bore = _num(t, "through hole", "bore", "hole", "shaft", default=6.0) or 6.0
        across = max(_num(t, "across", "width", "size", default=16.0) or 16.0, (bore + 4) / 0.866)
        th = _num(t, "long", "tall", "thick", default=20.0) or 20.0
        b = CADDesignBrief(object_type="hex_spacer", object_family="hex_spacer",
                           bores=[bore], required_features=["hex_profile", "center_bore"],
                           assumptions=[f"hex across {across:g}mm; {bore:g}mm through hole"])
        return prog(f"across={across}\nth={th}\nbore={bore}\n", _HEX, b, ["hex_profile", "center_bore"])

    if "shaft collar" in t or ("collar" in t and "shaft" in t):
        bore = _shaft(t, 12.0)
        b = CADDesignBrief(object_type="shaft_collar", object_family="shaft_collar",
                           mechanical_function="clamp onto a shaft", bores=[bore],
                           required_features=["bore", "clamp_slit", "clamp_screw"],
                           assumptions=[f"{bore:g}mm bore; M{int(_screw_clear(t))} clamp screw"])
        return prog(f"bore={bore}\nscrew_d={_screw_clear(t)}\n", _SHAFT_COLLAR, b,
                    ["clamp_slit", "clamp_screw", "bore"])

    if "elbow" in t or ("90" in t and "pipe" in t):
        pd = _num(t, "pipe", "diameter", default=40.0) or 40.0
        b = CADDesignBrief(object_type="pipe_elbow", object_family="pipe_elbow",
                           mechanical_function="90-degree flanged pipe elbow", bores=[pd - 10.0],
                           required_features=["pipe", "flanges", "bore"],
                           assumptions=[f"{pd:g}mm pipe; two circular flanges; 90-degree"])
        return prog(f"pipe_d={pd}\n", _PIPE_ELBOW, b, ["flanges", "bore"])

    if ("block" in t and ("slot" in t or "counterbor" in t)) or "stepped slot" in t:
        b = CADDesignBrief(object_type="block_with_slot", object_family="block_with_slot",
                           holes=[BriefHole(purpose="mounting", count=2, counterbore=True, diameter_mm=3.4)],
                           required_features=["block", "stepped_slot", "counterbore"],
                           assumptions=["stepped slot; two counterbored holes"])
        return prog("", _CBORE_BLOCK, b, ["stepped_slot", "counterbore"])

    if "vise jaw" in t or ("jaw" in t and ("vise" in t or "vice" in t)):
        b = CADDesignBrief(object_type="vise_jaw", object_family="vise_jaw",
                           holes=[BriefHole(purpose="mounting", count=2, diameter_mm=3.4)],
                           required_features=["jaw", "v_groove", "mounting_holes"],
                           assumptions=["two mounting holes; V groove"])
        return prog("", _VISE_JAW, b, ["v_groove", "mounting_holes"])

    if "nema" in t or ("motor" in t and ("mount" in t or "plate" in t)):
        b = CADDesignBrief(object_type="motor_mount_plate", object_family="motor_mount_plate",
                           bores=[22.0],
                           holes=[BriefHole(purpose="motor", count=4, pattern="grid",
                                            bolt_circle_diameter_mm=31.0, diameter_mm=3.2)],
                           required_features=["plate", "nema17_pattern", "center_bore"],
                           assumptions=["NEMA 17: 31mm hole square, 22mm pilot bore, M3 holes"])
        return prog("", _NEMA17, b, ["nema17_pattern", "center_bore"])

    return None
