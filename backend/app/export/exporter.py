"""Build geometry from a spec and produce export files + a preview mesh.

`generate` is the single deterministic entry point used by both creation and
parameter-driven regeneration: same spec in -> same geometry out, no LLM.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cadquery as cq

from app.cad.base import CadGenerationError
from app.cad.registry import get_template
from app.schemas.design_spec import DesignSpec


@dataclass
class PreviewMesh:
    """Triangle mesh for the browser viewer (flat arrays, Three.js friendly)."""

    positions: list[float]  # x,y,z per vertex, flattened
    indices: list[int]  # triangle vertex indices
    vertex_count: int
    triangle_count: int


@dataclass
class GenerationResult:
    spec_hash: str
    stl_bytes: bytes
    step_bytes: bytes
    preview: PreviewMesh
    bounding_box_mm: dict[str, float]
    features: list[dict]
    volume_mm3: float = 0.0
    surface_area_mm2: float = 0.0


def spec_hash(spec: DesignSpec) -> str:
    payload = json.dumps(spec.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _tessellate(solid: "cq.Workplane", tolerance: float = 0.1) -> PreviewMesh:
    shape = solid.val()
    vertices, triangles = shape.tessellate(tolerance)
    positions: list[float] = []
    for v in vertices:
        positions.extend((v.x, v.y, v.z))
    indices: list[int] = []
    for tri in triangles:
        indices.extend(tri)
    return PreviewMesh(
        positions=positions,
        indices=indices,
        vertex_count=len(vertices),
        triangle_count=len(triangles),
    )


def _export_bytes(solid: "cq.Workplane", suffix: str,
                  tolerance: float | None = None,
                  angular_tolerance: float | None = None) -> bytes:
    """Export to a temp file (CadQuery writes files), then read bytes back.

    ``tolerance`` / ``angular_tolerance`` refine the STL tessellation; they are
    required for fine helical features (modeled threads) so the mesh resolves the
    helix and stays watertight. STEP is BRep-based and ignores these."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        kwargs = {}
        if tolerance is not None:
            kwargs["tolerance"] = tolerance
        if angular_tolerance is not None:
            kwargs["angularTolerance"] = angular_tolerance
        cq.exporters.export(solid, str(tmp_path), **kwargs)
        data = tmp_path.read_bytes()
        if not data:
            raise RuntimeError(f"Export produced empty {suffix} file")
        return data
    finally:
        tmp_path.unlink(missing_ok=True)


def _has_modeled_thread(spec: DesignSpec) -> bool:
    """True when the spec carries a thread (pitch + major diameter) that is cut as
    fine helical geometry and therefore needs a finer export/preview tessellation."""
    dims = spec.dimensions or {}
    return float(dims.get("thread_pitch", 0) or 0) > 0 and \
        float(dims.get("thread_major_diameter", 0) or 0) > 0


def build_solid(spec: DesignSpec) -> "cq.Workplane":
    """Build geometry only, converting kernel failures to CadGenerationError."""
    if spec.object_type == "feature_graph":
        from app.cad.feature_graph import build_feature_graph
        from app.schemas.complex_cad import CADFeatureGraph

        if not spec.feature_graph:
            raise CadGenerationError("feature_graph design has no graph")
        return build_feature_graph(CADFeatureGraph(**spec.feature_graph))

    template = get_template(spec.object_type)
    try:
        return template.build(spec)
    except CadGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CadGenerationError(
            f"Could not build valid geometry for {spec.object_type}: {exc}"
        ) from exc


def mesh_only(spec: DesignSpec, tolerance: float = 0.2) -> PreviewMesh:
    """Tessellate the solid without producing export files (used by drawings)."""
    return _tessellate(build_solid(spec), tolerance)


# Complex precision templates whose output must pass ground-truth topology
# validation (valid BRep, single fused body, non-degenerate) before export —
# a crankshaft with unfused journals/webs must be REJECTED, not downloaded.
TOPOLOGY_GATED_TYPES = {"inline_4_crankshaft"}


def generate(spec: DesignSpec) -> GenerationResult:
    # build_solid handles both templates and the feature-graph fallback, and
    # converts kernel failures into CadGenerationError.
    solid = build_solid(spec)
    bb = solid.val().BoundingBox()
    bbox = {"x": round(bb.xlen, 3), "y": round(bb.ylen, 3), "z": round(bb.zlen, 3)}
    try:
        volume_mm3 = round(float(solid.val().Volume()), 3)
    except Exception:  # noqa: BLE001 - degenerate shape reports no volume
        volume_mm3 = 0.0
    try:
        surface_area_mm2 = round(float(solid.val().Area()), 3)
    except Exception:  # noqa: BLE001
        surface_area_mm2 = 0.0
    # Modeled threads are fine helical features: a coarse mesh hides the thread
    # and tears the surface open, so export the STL (and preview) at the thread
    # tessellation tolerance. STEP is BRep and always carries the modeled thread.
    fine = _has_modeled_thread(spec)
    if fine:
        from app.cad.threads.metric import (
            THREAD_STL_ANGULAR_TOLERANCE,
            THREAD_STL_TOLERANCE,
        )
        stl_bytes = _export_bytes(solid, ".stl", tolerance=THREAD_STL_TOLERANCE,
                                  angular_tolerance=THREAD_STL_ANGULAR_TOLERANCE)
    else:
        stl_bytes = _export_bytes(solid, ".stl")

    if spec.object_type in TOPOLOGY_GATED_TYPES:
        from app.cad.topology import validate_topology  # local import avoids a cycle

        report = validate_topology(solid, stl_bytes)
        if not report.ok:
            raise CadGenerationError(
                f"{spec.object_type} failed topology validation: "
                + "; ".join(report.problems)
                + ". This part is marked unsupported instead of exporting broken CAD."
            )

    from app.cad.features import extract_features  # local import avoids a cycle

    features = [f.model_dump() for f in extract_features(spec, bbox)]
    return GenerationResult(
        spec_hash=spec_hash(spec),
        stl_bytes=stl_bytes,
        step_bytes=_export_bytes(solid, ".step"),
        preview=_tessellate(solid, tolerance=0.04 if fine else 0.1),
        bounding_box_mm=bbox,
        features=features,
        volume_mm3=volume_mm3,
        surface_area_mm2=surface_area_mm2,
    )
