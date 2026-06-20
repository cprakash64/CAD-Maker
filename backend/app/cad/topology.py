"""Topology validation for complex generated solids.

"Exports a file" is not "is a valid part". A crankshaft whose journals, webs
and throws didn't fuse exports fine but is broken CAD. This module checks the
GROUND TRUTH of the built solid:

  * the BRep is valid (OCCT ``Shape.isValid()`` — catches self-intersections,
    bad faces, zero-thickness shells the kernel can detect),
  * the exported mesh is ONE connected component (no floating solids — every
    journal/web/throw actually fused),
  * the model is non-degenerate (real triangles, non-zero extent on all axes).

Used to gate the precision crankshaft template: a model that fails is REJECTED
with diagnostics instead of being exported as broken geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TopologyReport:
    valid_brep: bool = True
    components: int = 1
    triangles: int = 0
    degenerate: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_topology(solid, stl_bytes: bytes) -> TopologyReport:
    """Ground-truth topology checks on a built cadquery solid + its mesh."""
    from app.generation.mesh_analysis import analyze_stl

    report = TopologyReport()
    try:
        shape = solid.val() if hasattr(solid, "val") else solid
        report.valid_brep = bool(shape.isValid())
    except Exception as exc:  # noqa: BLE001 - a crash IS an invalid shape
        report.valid_brep = False
        report.problems.append(f"BRep validity check failed: {exc}")
    if not report.valid_brep and not report.problems:
        report.problems.append(
            "invalid BRep solid (self-intersection / corrupt faces)")

    stats = analyze_stl(stl_bytes) if stl_bytes else None
    if stats is None or stats.triangles == 0:
        report.degenerate = True
        report.triangles = 0
        report.problems.append("empty mesh (no triangles)")
        return report
    report.triangles = stats.triangles
    report.components = stats.components
    if stats.components != 1:
        report.problems.append(
            f"{stats.components} disconnected solids — features did not fuse "
            "into one body")

    try:
        bb = (solid.val() if hasattr(solid, "val") else solid).BoundingBox()
        if min(bb.xlen, bb.ylen, bb.zlen) <= 1e-6:
            report.degenerate = True
            report.problems.append("zero-thickness geometry (degenerate bounding box)")
    except Exception:  # noqa: BLE001 - bbox failure already implies invalid shape
        pass
    return report
