"""Ground-truth measurement of generated geometry.

Two complementary sources, neither of which a planner can fake by writing numbers
into metadata:

* BRep (OpenCascade, via CadQuery): bounding box, solid VOLUME, surface area —
  exact, kernel-computed from the real solid.
* Mesh (the exported STL): watertight/manifold status, connected components, and
  genus (= physical through-hole count), via ``analyze_stl``.

These feed the dimension report, the print-readiness checks, and the benchmark.
"""
from __future__ import annotations

from app.generation.mesh_analysis import analyze_stl


def measure_solid(solid) -> dict:
    """Exact BRep measurements of a CadQuery Workplane's solid."""
    shape = solid.val()
    bb = shape.BoundingBox()
    try:
        volume = float(shape.Volume())
    except Exception:  # noqa: BLE001 - degenerate/empty shapes report no volume
        volume = 0.0
    try:
        area = float(shape.Area())
    except Exception:  # noqa: BLE001
        area = 0.0
    return {
        "bbox_mm": {"x": round(bb.xlen, 3), "y": round(bb.ylen, 3), "z": round(bb.zlen, 3)},
        "volume_mm3": round(volume, 3),
        "surface_area_mm2": round(area, 3),
    }


def mesh_facts(stl_bytes: bytes) -> dict:
    """Printability-relevant facts derived from the exported mesh."""
    s = analyze_stl(stl_bytes)
    return {
        "triangles": s.triangles,
        "watertight": s.watertight,
        "manifold": s.boundary_edges == 0,
        "boundary_edges": s.boundary_edges,
        "components": s.components,
        "through_holes_genus": s.through_holes,
    }
