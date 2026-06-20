"""Geometric analysis of a generated mesh — ground truth, not self-reported metadata.

We weld the exported STL into a manifold and derive facts that a program CANNOT
fake by writing numbers into metadata:

* connected components (catches accidentally-disconnected bodies)
* genus per component via the Euler characteristic V - E + F = 2 - 2g
  -> a single solid's genus == the number of through-holes / handles. A plate
     with 8 bolt holes + a center bore is genus 9; a plain disk is genus 0.
* outer radial-profile variation (distinguishes a hex/gear profile from a plain
  circular disk).

So "metadata says 8 holes" is only believed if the geometry actually shows them.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from app.generation.stl_preview import _parse_ascii, _parse_binary, _looks_ascii


@dataclass
class MeshStats:
    triangles: int
    welded_vertices: int
    edges: int
    boundary_edges: int          # edges used by != 2 faces (open/non-manifold)
    components: int
    through_holes: int           # total genus across components (>=0)
    watertight: bool
    outer_radius_cv: float       # coeff. of variation of the outer rim radius
    outer_corner_count: int      # distinct angular corners on the outer profile
    bbox: dict


def _triangles(data: bytes):
    tris = _parse_ascii(data) if _looks_ascii(data) else _parse_binary(data)
    return tris or _parse_binary(data)


def analyze_stl(data: bytes, weld_decimals: int = 3) -> MeshStats:
    tris = _triangles(data)
    if not tris:
        return MeshStats(0, 0, 0, 0, 0, 0, False, 0.0, {"x": 0, "y": 0, "z": 0})

    # Weld coincident vertices.
    index: dict[tuple, int] = {}
    coords: list[tuple] = []

    def vid(p) -> int:
        key = (round(p[0], weld_decimals), round(p[1], weld_decimals), round(p[2], weld_decimals))
        i = index.get(key)
        if i is None:
            i = len(coords)
            index[key] = i
            coords.append(key)
        return i

    faces: list[tuple] = []
    edge_faces: dict[tuple, int] = defaultdict(int)
    for a, b, c in tris:
        ia, ib, ic = vid(a), vid(b), vid(c)
        if ia == ib or ib == ic or ia == ic:
            continue  # degenerate
        faces.append((ia, ib, ic))
        for u, v in ((ia, ib), (ib, ic), (ic, ia)):
            edge_faces[(min(u, v), max(u, v))] += 1

    V = len(coords)
    F = len(faces)
    E = len(edge_faces)
    boundary = sum(1 for n in edge_faces.values() if n != 2)
    watertight = boundary == 0

    # Connected components (union-find over welded vertices via shared faces).
    parent = list(range(V))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for ia, ib, ic in faces:
        union(ia, ib)
        union(ib, ic)

    comp_v: dict[int, int] = defaultdict(int)
    for i in range(V):
        if coords[i] is not None:
            comp_v[find(i)] += 1
    comp_f: dict[int, int] = defaultdict(int)
    comp_e: dict[int, set] = defaultdict(set)
    for ia, ib, ic in faces:
        r = find(ia)
        comp_f[r] += 1
        for u, v in ((ia, ib), (ib, ic), (ic, ia)):
            comp_e[r].add((min(u, v), max(u, v)))

    components = len([r for r in comp_f if comp_f[r] > 0])
    total_holes = 0
    for r, f in comp_f.items():
        if f == 0:
            continue
        v = comp_v[r]
        e = len(comp_e[r])
        chi = v - e + f
        g = max(0, round((2 - chi) / 2))
        total_holes += g

    # Outer radial profile (about the Z axis) — circular vs hex/gear.
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    zs = [c[2] for c in coords]
    cx = (max(xs) + min(xs)) / 2.0
    cy = (max(ys) + min(ys)) / 2.0
    radii = [math.hypot(x - cx, y - cy) for x, y in zip(xs, ys)]
    rmax = max(radii) if radii else 0.0
    outer = [r for r in radii if r >= 0.85 * rmax] if rmax > 0 else []
    if len(outer) >= 8:
        mean = sum(outer) / len(outer)
        var = sum((r - mean) ** 2 for r in outer) / len(outer)
        cv = (math.sqrt(var) / mean) if mean > 0 else 0.0
    else:
        cv = 0.0

    # Distinct angular "corners" on the outermost rim (r >= 0.97 rmax). A hexagon
    # has ~6, a tessellated circle has many (>=16), a gear has ~tooth-count.
    corners: set[int] = set()
    if rmax > 0:
        for x, y, r in zip(xs, ys, radii):
            if r >= 0.97 * rmax:
                corners.add(round(math.degrees(math.atan2(y - cy, x - cx)) / 6.0))
    corner_count = len(corners)

    bbox = {"x": round(max(xs) - min(xs), 3), "y": round(max(ys) - min(ys), 3),
            "z": round(max(zs) - min(zs), 3)}
    return MeshStats(
        triangles=F, welded_vertices=V, edges=E, boundary_edges=boundary,
        components=components, through_holes=total_holes, watertight=watertight,
        outer_radius_cv=round(cv, 4), outer_corner_count=corner_count, bbox=bbox,
    )
