"""Restricted OpenSCAD-style generator (fallback for broad mechanical shapes).

SAFETY: the LLM never emits SCAD code. It emits a validated ``GeneralCADPlan``
(data only); we compile that to a small, restricted SCAD source ourselves, lint
it, and run OpenSCAD in a sandboxed subprocess (timeout, temp dir only, no
network, no file/include/import). Only STL comes out of this path — STEP is only
offered for precision-template and feature-graph models (never faked).
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.cad.base import CadGenerationError
from app.schemas.generation import GeneralCADPlan

# Hard safety limits.
_MAX_PRIMITIVES = 200
_MAX_DIM = 5000.0
_MAX_PATTERN = 400
_TIMEOUT_S = 20

# Tokens that must never appear in generated SCAD (defense in depth — we author
# the source, but we still lint it before running).
_FORBIDDEN = (
    "include", "use", "import", "surface", "text(", "import(", "<", ">",
    "system", "`", "$fa=0;", "dxf", "stl", "off", "//", "/*",
)
_ALLOWED_FUNCS = {
    "cube", "cylinder", "sphere", "polyhedron", "linear_extrude", "rotate_extrude",
    "translate", "rotate", "mirror", "scale", "union", "difference", "intersection",
    "hull", "minkowski", "polygon", "circle", "square", "for", "color", "module",
}


def scad_available() -> bool:
    return _openscad_bin() is not None


def _openscad_bin() -> str | None:
    cand = os.environ.get("OPENSCAD_BIN") or shutil.which("openscad")
    if cand and Path(cand).exists():
        return cand
    mac = "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"
    return mac if Path(mac).exists() else None


# --- plan -> restricted SCAD source ---------------------------------------
def _n(v: float) -> float:
    f = float(v)
    if not math.isfinite(f) or abs(f) > _MAX_DIM:
        raise CadGenerationError(f"dimension {f} out of range")
    return round(f, 4)


def scad_source_from_plan(plan: GeneralCADPlan) -> str:
    if not plan.is_nonempty():
        raise CadGenerationError("plan has no primitives")
    if len(plan.primitives) > _MAX_PRIMITIVES:
        raise CadGenerationError("too many primitives")

    adds, subs = [], []
    for p in plan.primitives:
        solid = _primitive_scad(p)
        (subs if p.op == "subtract" else adds).append(solid)
    for h in plan.holes:
        subs.append(_hole_scad(h))

    body = "union(){\n" + "\n".join(adds) + "\n}" if adds else "union(){}"
    if subs:
        body = "difference(){\n" + body + "\n" + "\n".join(subs) + "\n}"
    return f"$fn=64;\n{body}\n"


def _primitive_scad(p) -> str:
    x, y, z = (_n(c) for c in (p.at + [0, 0, 0])[:3])
    k, pr = p.kind, p.params
    tr = f"translate([{x},{y},{z}])"
    if k == "box":
        return f"{tr} cube([{_n(pr.get('width',10))},{_n(pr.get('depth',10))},{_n(pr.get('height',10))}], center=true);"
    if k == "cylinder":
        return f"{tr} cylinder(h={_n(pr.get('height',10))}, r={_n(pr.get('radius',5))}, center=true);"
    if k == "tube":
        ro, ri, h = _n(pr.get("radius", 10)), _n(pr.get("inner_radius", 5)), _n(pr.get("height", 10))
        return f"{tr} difference(){{ cylinder(h={h}, r={ro}, center=true); cylinder(h={h+2}, r={ri}, center=true); }}"
    if k in ("hex_prism", "polygon_prism"):
        sides = 6 if k == "hex_prism" else int(_n(pr.get("sides", 6)))
        sides = max(3, min(64, sides))
        r = _n(pr.get("radius", pr.get("diameter", 10) / 2 or 5))
        h = _n(pr.get("height", 10))
        return f"{tr} cylinder(h={h}, r={r}, center=true, $fn={sides});"
    if k == "sphere":
        return f"{tr} sphere(r={_n(pr.get('radius',5))});"
    if k == "cone":
        return f"{tr} cylinder(h={_n(pr.get('height',10))}, r1={_n(pr.get('radius1',5))}, r2={_n(pr.get('radius2',2))}, center=true);"
    raise CadGenerationError(f"unsupported primitive kind '{k}'")


def _hole_scad(h) -> str:
    depth = _n(h.depth or 5000)
    return f"translate([{_n(h.x)},{_n(h.y)},0]) cylinder(h={depth}, r={_n(h.diameter)/2}, center=true);"


# --- static lint ----------------------------------------------------------
def lint_scad(src: str) -> None:
    low = src.lower()
    for tok in _FORBIDDEN:
        if tok in low:
            raise CadGenerationError(f"SCAD lint: forbidden token '{tok}'")
    if src.count("for") > _MAX_PATTERN:
        raise CadGenerationError("SCAD lint: too many loops")
    if len(src) > 200_000:
        raise CadGenerationError("SCAD lint: source too large")


# --- sandboxed run --------------------------------------------------------
def run_scad_to_stl(plan: GeneralCADPlan) -> bytes:
    """Compile plan -> SCAD -> STL in a sandboxed subprocess. Raises if OpenSCAD
    is unavailable or fails (caller decides whether to repair/clarify)."""
    binary = _openscad_bin()
    if binary is None:
        raise CadGenerationError("OpenSCAD is not installed on this server")
    src = scad_source_from_plan(plan)
    lint_scad(src)

    with tempfile.TemporaryDirectory(prefix="scad_") as tmp:
        scad_path = Path(tmp) / "model.scad"
        stl_path = Path(tmp) / "model.stl"
        scad_path.write_text(src)
        try:
            proc = subprocess.run(
                [binary, "-o", str(stl_path), str(scad_path)],
                cwd=tmp, capture_output=True, timeout=_TIMEOUT_S,
                env={"PATH": os.environ.get("PATH", ""), "OPENSCADPATH": tmp},
            )
        except subprocess.TimeoutExpired as exc:
            raise CadGenerationError("SCAD generation timed out") from exc
        if proc.returncode != 0 or not stl_path.exists() or stl_path.stat().st_size == 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[:300]
            raise CadGenerationError(f"OpenSCAD failed: {err}")
        data = stl_path.read_bytes()
        if not data:
            raise CadGenerationError("SCAD produced an empty STL")
        return data
