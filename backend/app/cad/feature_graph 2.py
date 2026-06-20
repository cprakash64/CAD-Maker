"""Trusted feature-graph interpreter.

Builds a CadQuery solid from a validated ``CADFeatureGraph`` by dispatching on a
FIXED whitelist of operation names. There is NO eval/exec and no arbitrary
equations — every op reads only numeric parameters. Unknown ops are rejected.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import CadGenerationError
from app.schemas.complex_cad import CADFeatureGraph

_ALLOWED = {
    "box", "cylinder", "cone", "sphere", "extrude_profile", "revolve_profile",
    "cut_hole", "circular_pattern", "linear_pattern", "boolean_union",
    "boolean_cut", "fillet", "chamfer",
}

_MAX_DIM = 5000.0


def _p(params: dict, key: str, default: float = 0.0) -> float:
    v = float(params.get(key, default))
    if not math.isfinite(v) or abs(v) > _MAX_DIM:
        raise CadGenerationError(f"parameter '{key}'={v} is out of range")
    return v


def _primitive(op: str, params: dict, at) -> cq.Workplane:
    x, y, z = at
    if op == "box":
        w, d, h = _p(params, "width", 10), _p(params, "depth", 10), _p(params, "height", 10)
        if min(w, d, h) <= 0:
            raise CadGenerationError("box dimensions must be positive")
        return cq.Workplane("XY").box(w, d, h).translate((x, y, z))
    if op == "cylinder":
        r, h = _p(params, "radius", 5), _p(params, "height", 10)
        if r <= 0 or h <= 0:
            raise CadGenerationError("cylinder radius/height must be positive")
        return cq.Workplane("XY").circle(r).extrude(h).translate((x, y, z))
    if op == "cone":
        r1, r2, h = _p(params, "radius1", 5), _p(params, "radius2", 2), _p(params, "height", 10)
        if r1 <= 0 or h <= 0:
            raise CadGenerationError("cone radius1/height must be positive")
        solid = cq.Solid.makeCone(r1, max(0.0, r2), h)
        return cq.Workplane("XY").add(solid).translate((x, y, z))
    if op == "sphere":
        r = _p(params, "radius", 5)
        if r <= 0:
            raise CadGenerationError("sphere radius must be positive")
        return cq.Workplane("XY").sphere(r).translate((x, y, z))
    if op == "extrude_profile":
        pts = params.get("points")
        h = _p(params, "height", 5)
        if not isinstance(pts, list) or len(pts) < 3:
            raise CadGenerationError("extrude_profile needs >=3 (x,y) points")
        poly = [(float(p[0]), float(p[1])) for p in pts]
        return cq.Workplane("XY").polyline(poly).close().extrude(h).translate((x, y, z))
    if op == "revolve_profile":
        pts = params.get("points")
        angle = _p(params, "angle", 360)
        if not isinstance(pts, list) or len(pts) < 3:
            raise CadGenerationError("revolve_profile needs >=3 (x,y) points")
        poly = [(float(p[0]), float(p[1])) for p in pts]
        return cq.Workplane("XY").polyline(poly).close().revolve(angle).translate((x, y, z))
    raise CadGenerationError(f"unknown primitive op '{op}'")


def build_feature_graph(graph: CADFeatureGraph) -> cq.Workplane:
    if not graph.is_nonempty():
        raise CadGenerationError("feature graph has no operations")
    solids: dict[str, cq.Workplane] = {}
    last_id: str | None = None

    for raw in graph.operations:
        op = raw.get("op")
        oid = raw.get("id")
        if op not in _ALLOWED:
            raise CadGenerationError(f"operation '{op}' is not allowed")
        if not oid:
            raise CadGenerationError("every operation needs an id")
        params = raw.get("params", {}) or {}

        if op in ("box", "cylinder", "cone", "sphere", "extrude_profile", "revolve_profile"):
            solids[oid] = _primitive(op, params, tuple(raw.get("at", (0, 0, 0))))
        elif op in ("boolean_union", "boolean_cut"):
            target, tool = solids.get(raw.get("target")), solids.get(raw.get("tool"))
            if target is None or tool is None:
                raise CadGenerationError(f"{op} references unknown solid id")
            solids[oid] = target.union(tool) if op == "boolean_union" else target.cut(tool)
        elif op == "cut_hole":
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError("cut_hole references unknown solid id")
            r = _p(params, "radius", 2)
            depth = _p(params, "depth", 50)
            at = tuple(raw.get("at", (0, 0, 0)))
            tool = cq.Workplane("XY").circle(r).extrude(depth).translate(at)
            solids[oid] = target.cut(tool)
        elif op in ("circular_pattern", "linear_pattern"):
            src = solids.get(raw.get("source"))
            if src is None:
                raise CadGenerationError(f"{op} references unknown source")
            count = int(raw.get("count", 1))
            if not (1 <= count <= 200):
                raise CadGenerationError("pattern count out of range")
            result = src
            if op == "circular_pattern":
                radius = _p(params, "radius", 0)
                for k in range(1, count):
                    ang = 360.0 * k / count
                    inst = src.translate((radius, 0, 0)).rotate((0, 0, 0), (0, 0, 1), ang)
                    result = result.union(inst)
            else:
                sx, sy, sz = _p(params, "dx", 0), _p(params, "dy", 0), _p(params, "dz", 0)
                for k in range(1, count):
                    result = result.union(src.translate((sx * k, sy * k, sz * k)))
            solids[oid] = result
        elif op in ("fillet", "chamfer"):
            target = solids.get(raw.get("target"))
            if target is None:
                raise CadGenerationError(f"{op} references unknown target")
            size = _p({"s": raw.get("size", 1)}, "s", 1)
            try:
                edges = target.edges()
                solids[oid] = edges.fillet(size) if op == "fillet" else edges.chamfer(size)
            except Exception:  # noqa: BLE001 - best-effort; keep unfilleted
                solids[oid] = target
        last_id = oid

    result_id = graph.result_id or last_id
    result = solids.get(result_id)
    if result is None:
        raise CadGenerationError("feature graph produced no result solid")
    return result
