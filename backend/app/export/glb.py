"""Minimal binary glTF 2.0 (.glb) writer, built directly from an existing
PreviewMesh (positions/indices) — no re-tessellation, no BRep dependency.

This is a preview/web format only, never a manufacturable file (that's STL/
STEP). It is therefore available whenever a design has ANY mesh at all,
including concept and critical-failure designs where STL/STEP are blocked —
so a user can still visually inspect what was built even when it can't be
exported for manufacturing. See app.routers.designs's /files/{fmt} route and
docs on export eligibility (STEP/STL require a verified solid; GLB does not).
"""
from __future__ import annotations

import json
import struct

_GLTF_VERSION = 2
_MAGIC = 0x46546C67  # "glTF"
_CHUNK_JSON = 0x4E4F534A  # "JSON"
_CHUNK_BIN = 0x004E4942  # "BIN\0"

_COMPONENT_TYPE_FLOAT = 5126
_COMPONENT_TYPE_UNSIGNED_INT = 5125
_TARGET_ARRAY_BUFFER = 34962
_TARGET_ELEMENT_ARRAY_BUFFER = 34963
_MODE_TRIANGLES = 4


def _pad(data: bytes, align: int, pad_byte: bytes) -> bytes:
    rem = len(data) % align
    if rem:
        data += pad_byte * (align - rem)
    return data


def _chunk(chunk_type: int, data: bytes) -> bytes:
    return struct.pack("<II", len(data), chunk_type) + data


def build_glb_bytes(positions: list[float], indices: list[int]) -> bytes:
    """A single-mesh, single-primitive, indexed triangle-list .glb.

    Positions only (no normals/UVs/materials) — a geometry-accurate preview,
    not a render. ``positions`` is a flat x,y,z-per-vertex list (mm);
    ``indices`` is a flat triangle-vertex-index list, matching PreviewMesh."""
    vertex_count = len(positions) // 3
    index_count = len(indices)

    pos_bytes = _pad(struct.pack(f"<{len(positions)}f", *positions), 4, b"\x00")
    idx_bytes = struct.pack(f"<{index_count}I", *indices)
    idx_byte_length = len(idx_bytes)
    idx_byte_offset = len(pos_bytes)
    bin_chunk = pos_bytes + _pad(idx_bytes, 4, b"\x00")

    if vertex_count:
        xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
        pos_min, pos_max = [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]
    else:
        pos_min = pos_max = [0.0, 0.0, 0.0]

    gltf = {
        "asset": {"version": "2.0", "generator": "LunaiCAD"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0},
                "indices": 1,
                "mode": _MODE_TRIANGLES,
            }],
        }],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes),
             "target": _TARGET_ARRAY_BUFFER},
            {"buffer": 0, "byteOffset": idx_byte_offset, "byteLength": idx_byte_length,
             "target": _TARGET_ELEMENT_ARRAY_BUFFER},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": _COMPONENT_TYPE_FLOAT, "count": vertex_count,
             "type": "VEC3", "min": pos_min, "max": pos_max},
            {"bufferView": 1, "componentType": _COMPONENT_TYPE_UNSIGNED_INT, "count": index_count,
             "type": "SCALAR"},
        ],
    }
    json_bytes = _pad(json.dumps(gltf, separators=(",", ":")).encode("utf-8"), 4, b" ")

    body = _chunk(_CHUNK_JSON, json_bytes) + _chunk(_CHUNK_BIN, bin_chunk)
    header = struct.pack("<III", _MAGIC, _GLTF_VERSION, 12 + len(body))
    return header + body


def build_glb_from_preview_json(preview_json: dict) -> bytes | None:
    """``build_glb_bytes`` from a design's stored ``preview_json``, or None if
    there's no mesh to build from."""
    if not preview_json:
        return None
    positions = preview_json.get("positions") or []
    indices = preview_json.get("indices") or []
    if not positions or not indices:
        return None
    return build_glb_bytes(positions, indices)
