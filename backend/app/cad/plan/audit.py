"""Feature-level audit: does the COMPILED feature graph contain the mechanical
features the PROMPT asked for?

This is the semantic-accuracy gate. Export checks prove a file exists; this
audit proves the model means the right thing: a clamp block really has a tube
bore and tightening bolts, a blind flange really has no center bore, a straight
spool really isn't a tee. Requirements are derived from the prompt INDEPENDENTLY
of the plan, so a wrong plan (e.g. a plain box for a clamp prompt) fails the
audit even though its STEP/STL export perfectly.

Canonical stable feature IDs (roles): base_plate, clamp_body, tube_bore,
clamp_gap, tightening_bolt_holes, mounting_holes, bearing_boss, shaft_bore,
hinge_ears, pin_hole, flange_body, bolt_circle, center_bore, pipe_body,
branch_pipe, side_walls, sensor_hole, enclosure_body, v_groove.

Audit failures are surfaced as warnings at runtime (they never block a compiled
model's downloads) but they FAIL the benchmark tests — wrong primary geometry is
a bug even when files exist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.cad.plan.compiler import CadPlanResult
from app.cad.plan.deterministic import _count, _near, _screws
from app.cad.plan.schema import CadPlan, Feature, FeatureKind

_HOLE_KINDS = {
    FeatureKind.hole, FeatureKind.hole_pattern_rect, FeatureKind.hole_pattern_circle,
    FeatureKind.countersink, FeatureKind.counterbore,
}


# --- canonical role resolution ----------------------------------------------
def resolve_role(f: Feature) -> str | None:
    """Map any plan feature (deterministic or LLM-authored) to a canonical
    semantic role, using its id + description text and its kind."""
    t = f"{f.id} {f.description}".lower()
    k = f.kind
    if k == FeatureKind.v_groove:
        return "v_groove"
    if k == FeatureKind.boss:
        return "bearing_boss"
    if k == FeatureKind.circular_flange:
        return "flange_body"
    if k in (FeatureKind.pipe, FeatureKind.pipe_spool):
        return "branch_pipe" if "branch" in t else "pipe_body"
    if k == FeatureKind.shell:
        return "enclosure_shell"
    if k == FeatureKind.rectangular_wall:
        return "hinge_ears" if "ear" in t else "side_walls"
    if k == FeatureKind.plate:
        return "base_plate"
    if k in (FeatureKind.box, FeatureKind.rectangular_cut, FeatureKind.slot):
        if f.op == "cut" or k != FeatureKind.box:
            if re.search(r"\bgap\b|\bsplit\b|\bslit\b|\bsaddle\b", t):
                return "clamp_gap"
            return "cutout"
        if "clamp" in t:
            return "clamp_body"
        if "enclosure" in t or "case" in t or "housing" in t:
            return "enclosure_body"
        if "base" in t:
            return "base_plate"
        return "body"
    if k in _HOLE_KINDS:
        if "sensor" in t:
            return "sensor_hole"
        if "pin" in t or "pivot" in t:
            return "pin_hole"
        if "tighten" in t or "clamp bolt" in t or "clamping" in t:
            return "tightening_bolt_holes"
        if "tube" in t or "saddle" in t:
            return "tube_bore"
        if "center" in t and "bore" in t:
            return "center_bore"
        if "shaft" in t or "bore" in t:
            return "shaft_bore"
        if "mount" in t or "corner" in t or re.search(r"\bbase\b", t):
            return "mounting_holes"
        if k == FeatureKind.hole_pattern_circle or "bolt circle" in t or "pcd" in t:
            return "bolt_circle"
        if "bolt" in t or "clearance" in t or "screw" in t:
            return "mounting_holes"
        return "hole"
    return None


def _feature_count(f: Feature) -> int:
    """How many physical instances a feature contributes (patterns expand)."""
    if f.kind == FeatureKind.hole_pattern_rect:
        return int(f.p("nx", 2, "cols")) * int(f.p("ny", 2, "rows"))
    if f.kind == FeatureKind.hole_pattern_circle:
        return int(f.p("count", 0, "bolt_count", "holes"))
    return 1


def _feature_dia(f: Feature) -> float:
    for key in ("diameter", "dia", "d", "hole_diameter", "od"):
        if key in f.params:
            return abs(float(f.params[key]))
    return 0.0


# --- requirements derived from the prompt ------------------------------------
@dataclass
class Requirement:
    feature_id: str            # canonical role
    description: str           # human-readable requirement
    count: int = 1             # minimum instance count (ignored when forbidden)
    forbidden: bool = False
    diameter: float | None = None        # expected hole/bore diameter (mm)
    params: dict[str, float] = field(default_factory=dict)  # feature params to verify


def _tol(expected: float) -> float:
    return max(0.6, abs(expected) * 0.05)


def derive_requirements(prompt: str) -> list[Requirement]:
    """Prompt → required/forbidden canonical features. Rule-based and
    deliberately independent of the planner, so it can catch a wrong plan."""
    t = (prompt or "").lower()
    reqs: list[Requirement] = []
    sc = _screws(t)

    is_tee = bool(re.search(r"\btee\b|\bt[- ]?pipe\b", t)) or ("branch" in t and "pipe" in t)
    is_spool = "spool" in t or ("pipe" in t and "flange" in t and "both ends" in t)
    is_clamp = "clamp" in t and bool(re.search(r"\btube\b|\bpipe\b|\brod\b|\bbar\b|\bshaft\b", t))
    is_vise = "vise" in t or "v groove" in t or "v-groove" in t

    if is_vise:
        reqs.append(Requirement("v_groove", "V groove along the jaw"))
        n = _count(t, "holes", "mounting holes")
        if n:
            reqs.append(Requirement("mounting_holes", f"{n} mounting holes", count=n))
        return reqs

    if is_clamp:
        tube = _near(t, "round tube", "round bar", "tube", "pipe", "rod", "bar", "shaft")
        bolts = _count(t, "bolts", "bolt holes", "screws") or 2
        reqs.append(Requirement("clamp_body", "clamp body (not just a flat plate)"))
        reqs.append(Requirement("tube_bore",
                                f"Ø{tube or '?'}mm tube bore or saddle through the clamp",
                                diameter=tube))
        reqs.append(Requirement("clamp_gap", "visible split / clamp gap"))
        reqs.append(Requirement("tightening_bolt_holes",
                                f"{bolts} tightening bolt holes crossing the clamp",
                                count=bolts))
        if "base" in t or "mount" in t:
            reqs.append(Requirement("base_plate", "flat mounting base"))
            reqs.append(Requirement("mounting_holes", "base mounting holes"))
        return reqs

    if is_tee:
        # Position-aware tee anatomy: every item gets a stable id, so a missing
        # branch flange or a half-empty bolt pattern fails by NAME.
        vertical = "vertical main" in t
        reqs.append(Requirement(
            "main_pipe",
            "vertical main run pipe (axis Z)" if vertical else "main run pipe body",
            params={"vertical": 1.0} if vertical else {}))
        reqs.append(Requirement("branch_pipe", "perpendicular side branch pipe"))
        reqs.append(Requirement("main_bore", "bore through the main pipe"))
        reqs.append(Requirement("branch_bore", "bore through the branch pipe"))
        if "flange" in t:
            fod = _near(t, "flange outer diameter", "flange od", "flange diameter")
            m = re.search(r"(\d+)\s*x\s*ø?\s*(\d+(?:\.\d+)?)\s*mm\s*(?:bolt\s*)?holes", t)
            per_flange = int(m.group(1)) if m else _count(t, "holes per flange", "holes each")
            bolt_dia = float(m.group(2)) if m else None
            for pos in ("top", "bottom", "branch"):
                params = {"od": fod} if fod and pos != "branch" else {}
                reqs.append(Requirement(
                    f"{pos}_flange",
                    f"{pos} flange" + (f" (Ø{fod:g} from the drawing)" if params else ""),
                    params=params))
                reqs.append(Requirement(
                    f"{pos}_bolt_pattern",
                    f"{per_flange or 'a'} bolt hole(s) on the {pos} flange",
                    count=per_flange or 1, diameter=bolt_dia))
        return reqs

    if is_spool:
        bolts = _count(t, "holes") or 0
        reqs.append(Requirement("pipe_body", "straight pipe body"))
        reqs.append(Requirement("flange_body", "a flange on each end", count=2))
        if "straight" in t:
            reqs.append(Requirement("branch_pipe", "no tee / side branch (straight spool)",
                                    forbidden=True))
        if bolts:
            reqs.append(Requirement("bolt_circle",
                                    f"{bolts} bolt holes per flange", count=bolts,
                                    params={"bolt_count": float(bolts)}))
        return reqs

    if "flange" in t and "pipe" not in t:
        od = _near(t, "outer diameter", "od")
        thk = _near(t, "thick")
        params: dict[str, float] = {}
        if od:
            params["od"] = od
        if thk:
            params["thickness"] = thk
        pcd = _near(t, "pcd", "bolt circle", "bolt-circle")
        bolts = _count(t, "holes", "bolts") or 0
        if pcd:
            params["pcd"] = pcd
        if bolts:
            params["bolt_count"] = float(bolts)
        reqs.append(Requirement("flange_body", "circular flange body", params=params))
        if bolts:
            reqs.append(Requirement(
                "bolt_circle", f"{bolts}× {sc[1] if sc else ''} clearance holes on the PCD",
                count=bolts, diameter=sc[0] if sc else None))
        if "no center bore" in t or "no bore" in t or "no centre bore" in t:
            reqs.append(Requirement("center_bore", "no center bore", forbidden=True))
        return reqs

    if "bearing" in t and ("block" in t or "housing" in t):
        shaft = _near(t, "shaft")
        reqs.append(Requirement("base_plate", "rectangular base"))
        reqs.append(Requirement("bearing_boss", "raised bearing boss"))
        reqs.append(Requirement("shaft_bore", f"Ø{shaft or '?'}mm shaft bore",
                                diameter=shaft))
        reqs.append(Requirement("mounting_holes", "base mounting holes"))
        return reqs

    if re.search(r"\bhinge\b|\bears?\b", t):
        pin = _near(t, "pin hole", "mm pin")
        reqs.append(Requirement("base_plate", "base plate"))
        reqs.append(Requirement("hinge_ears", "two side ears", count=2))
        reqs.append(Requirement("pin_hole", f"Ø{pin or '?'}mm coaxial pin hole",
                                diameter=pin))
        return reqs

    if re.search(r"\bu[- ]?(shaped|bracket)\b", t):
        base_n = _count(t, "holes") or 2
        pivot = _near(t, "pivot hole", "mm pivot")
        reqs.append(Requirement("base_plate", "base"))
        reqs.append(Requirement("side_walls", "two side walls", count=2))
        reqs.append(Requirement("mounting_holes", f"{base_n} base holes", count=base_n))
        if pivot or "pivot" in t:
            reqs.append(Requirement("pin_hole", "pivot hole through each side wall",
                                    count=2, diameter=pivot))
        return reqs

    if "enclosure" in t or ("case" in t and "sensor" in t):
        reqs.append(Requirement("enclosure_body", "enclosure body"))
        reqs.append(Requirement("enclosure_shell", "hollow shell / two-part split"))
        if "sensor" in t:
            reqs.append(Requirement("sensor_hole", "cylindrical sensor hole"))
        n = _count(t, "mounting holes", "holes") or 4
        reqs.append(Requirement("mounting_holes", f"{n} mounting holes", count=n))
        return reqs

    if "nema" in t or "stepper" in t or "motor mount" in t or "motor mounting" in t:
        reqs.append(Requirement("base_plate", "motor mounting plate"))
        reqs.append(Requirement("center_bore", "center bore for the motor boss"))
        reqs.append(Requirement("mounting_holes", "motor bolt pattern", count=4))
        return reqs

    if re.search(r"\bl[- ]?bracket\b", t):
        reqs.append(Requirement("base_plate", "base plate"))
        reqs.append(Requirement("side_walls", "vertical wall"))
        return reqs

    if "plate" in t:
        reqs.append(Requirement("base_plate", "rectangular plate"))
        if "no center bore" in t or "no bore" in t:
            reqs.append(Requirement("center_bore", "no center bore", forbidden=True))
        return reqs

    return reqs  # unrecognized family -> nothing to assert (audit passes)


# --- the audit ----------------------------------------------------------------
@dataclass
class AuditItem:
    feature_id: str
    requirement: str
    forbidden: bool
    satisfied: bool
    detail: str


@dataclass
class FeatureAudit:
    items: list[AuditItem] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(i.satisfied for i in self.items)

    def failures(self) -> list[AuditItem]:
        return [i for i in self.items if not i.satisfied]

    def to_json(self) -> dict:
        return {
            "passed": self.passed,
            "items": [
                {"feature_id": i.feature_id, "requirement": i.requirement,
                 "forbidden": i.forbidden, "satisfied": i.satisfied, "detail": i.detail}
                for i in self.items
            ],
        }


def _role_features(plan: CadPlan) -> dict[str, list[Feature]]:
    roles: dict[str, list[Feature]] = {}
    for f in plan.features:
        role = resolve_role(f)
        if role:
            roles.setdefault(role, []).append(f)
    return roles


def _role_count(roles: dict[str, list[Feature]], role: str) -> int:
    n = sum(_feature_count(f) for f in roles.get(role, []))
    if role == "flange_body":
        # A pipe_spool composite carries a flange on each end.
        n += 2 * len(roles.get("pipe_body", [])) if any(
            f.kind == FeatureKind.pipe_spool for f in roles.get("pipe_body", [])) else 0
    if role == "bolt_circle":
        # Bolt circles also live inside circular_flange / pipe_spool composites.
        for f in roles.get("flange_body", []) + roles.get("pipe_body", []):
            n += int(f.p("bolt_count", 0, "holes", "count", "bolt_holes"))
    return n


def _has_center_bore(plan: CadPlan, roles: dict[str, list[Feature]]) -> bool:
    if roles.get("center_bore"):
        return True
    for f in plan.features:
        if f.kind in (FeatureKind.circular_flange, FeatureKind.pipe_spool):
            if f.p("bore", 0, "center_bore", "id", "inner_diameter") > 0 and \
                    f.kind == FeatureKind.circular_flange:
                return True
    # A plain hole exactly at the XY origin counts as a center bore.
    for f in roles.get("shaft_bore", []) + roles.get("hole", []):
        x, y, _ = f.at
        if f.kind == FeatureKind.hole and abs(x) < 1e-6 and abs(y) < 1e-6 and f.axis == "z":
            return True
    return False


# --- position-aware matchers (flanged tee / branch anatomy) -------------------
def _ftext(f: Feature) -> str:
    return f"{f.id} {f.description}".lower()


def _pipe_bore(f: Feature) -> float:
    return f.p("id", 0, "bore", "inner_diameter")


def _match_tee_item(req: Requirement, plan: CadPlan) -> tuple[bool, str] | None:
    """Evaluate the positional tee requirements (main_pipe, top_flange,
    branch_bolt_pattern, …). Returns None for ids this matcher doesn't own."""
    fid = req.feature_id
    pipes = [f for f in plan.features if f.kind == FeatureKind.pipe]
    mains = [f for f in pipes if "branch" not in _ftext(f)]
    branches = [f for f in pipes if "branch" in _ftext(f)]

    if fid == "main_pipe":
        if not mains:
            return False, "no main pipe in the plan"
        if req.params.get("vertical"):
            # The drawing shows a VERTICAL main run (top/bottom flanges) — a
            # horizontal generic tee is the wrong anatomy even if it compiles.
            vertical_mains = [f for f in mains if f.axis == "z"]
            if not vertical_mains:
                return False, ("main run built horizontal "
                               f"(axis {mains[0].axis}) — drawing shows a vertical run")
            if any(f.axis == "z" for f in branches):
                return False, "branch must be perpendicular to the vertical main run"
        return True, f"{len(mains)} main pipe(s)"
    if fid == "branch_pipe":
        return bool(branches), f"{len(branches)} branch pipe(s)"
    if fid == "main_bore":
        ok = any(_pipe_bore(f) > 0 for f in mains)
        return ok, "main pipe is hollow" if ok else "main pipe has no bore"
    if fid == "branch_bore":
        ok = any(_pipe_bore(f) > 0 for f in branches)
        return ok, "branch is hollow" if ok else "branch has no bore"

    pos = None
    for p in ("top", "bottom", "branch"):
        if fid in (f"{p}_flange", f"{p}_bolt_pattern"):
            pos = p
    if pos is None:
        return None
    flanges = [f for f in plan.features
               if f.kind == FeatureKind.circular_flange and pos in _ftext(f)]
    if fid.endswith("_flange"):
        if not flanges:
            return False, f"no {pos} flange in the plan"
        for key, want in req.params.items():
            if not any(abs(f.p(key, 0) - want) <= _tol(want) for f in flanges):
                return False, f"{pos} flange {key}≠{want:g} (drawing dimension not honored)"
        return True, f"{pos} flange present"
    # *_bolt_pattern: the flange must carry >= count bolt holes (and Ø when known).
    if not flanges:
        return False, f"no {pos} flange to carry the bolt pattern"
    best = max(int(f.p("bolt_count", 0, "holes", "count", "bolt_holes")) for f in flanges)
    if best < req.count:
        return False, f"{best} bolt holes on the {pos} flange (need {req.count})"
    if req.diameter and not any(
            abs(f.p("bolt_diameter", 0, "hole_diameter") - req.diameter) <= _tol(req.diameter)
            for f in flanges):
        return False, f"bolt Ø{req.diameter:g}mm not found on the {pos} flange"
    return True, f"{best} bolt holes on the {pos} flange"


def audit_plan(prompt: str, plan: CadPlan, result: CadPlanResult | None = None) -> FeatureAudit:
    """Compare the prompt's required features against the (compiled) plan.

    ``result`` is the compiler output: when provided, hole-bearing requirements
    are also cross-checked against the holes that were actually CUT (the
    compiler counts real subtractive operations, not metadata).
    """
    audit = FeatureAudit()
    roles = _role_features(plan)

    for req in derive_requirements(prompt):
        positional = None if req.forbidden else _match_tee_item(req, plan)
        if positional is not None:
            ok, detail = positional
            audit.items.append(AuditItem(
                feature_id=req.feature_id, requirement=req.description,
                forbidden=False, satisfied=ok, detail=detail,
            ))
            continue
        if req.forbidden:
            if req.feature_id == "center_bore":
                present = _has_center_bore(plan, roles)
            elif req.feature_id == "branch_pipe":
                present = bool(roles.get("branch_pipe"))
            else:
                present = bool(roles.get(req.feature_id))
            audit.items.append(AuditItem(
                feature_id=req.feature_id, requirement=req.description,
                forbidden=True, satisfied=not present,
                detail="absent as required" if not present else "FORBIDDEN feature is present",
            ))
            continue

        feats = roles.get(req.feature_id, [])
        count = _role_count(roles, req.feature_id)
        ok = count >= req.count
        detail = f"{count} found (need {req.count})"

        if ok and req.diameter:
            match = any(abs(_feature_dia(f) - req.diameter) <= _tol(req.diameter)
                        for f in feats) if feats else False
            if not match and req.feature_id == "bolt_circle":
                # Bolt diameter may live on the flange composite.
                match = any(
                    abs(f.p("bolt_diameter", 0, "hole_diameter") - req.diameter)
                    <= _tol(req.diameter)
                    for f in roles.get("flange_body", []) + roles.get("pipe_body", []))
            if not match:
                ok = False
                detail = f"diameter Ø{req.diameter}mm not found"

        if ok and req.params:
            carriers = feats or roles.get("flange_body", []) or roles.get("pipe_body", [])
            for key, want in req.params.items():
                if not any(abs(f.p(key, 0) - want) <= _tol(want) for f in carriers):
                    ok = False
                    detail = f"param {key}={want} not satisfied"
                    break

        audit.items.append(AuditItem(
            feature_id=req.feature_id, requirement=req.description,
            forbidden=False, satisfied=ok, detail=detail,
        ))

    # Geometric cross-check: every required hole-bearing feature must correspond
    # to holes the compiler REALLY cut (belt-and-braces against metadata lies).
    if result is not None:
        hole_roles = {"tube_bore", "tightening_bolt_holes", "mounting_holes",
                      "pin_hole", "shaft_bore", "sensor_hole", "bolt_circle",
                      "center_bore"}
        need = sum(r.count for r in derive_requirements(prompt)
                   if not r.forbidden and r.feature_id in hole_roles)
        if need:
            audit.items.append(AuditItem(
                feature_id="holes_cut", forbidden=False,
                requirement=f"at least {need} holes physically cut",
                satisfied=result.hole_count >= need,
                detail=f"{result.hole_count} holes cut by the compiler",
            ))
    return audit
