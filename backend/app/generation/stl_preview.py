"""Parse STL bytes (ASCII or binary) into a preview mesh + bounding box, so a
sandbox-generated model can feed the same preview/bbox pipeline as templates.
"""
from __future__ import annotations

import struct

from app.export.exporter import PreviewMesh


def parse_stl(data: bytes) -> tuple[PreviewMesh, dict]:
    tris = _parse_binary(data) if not _looks_ascii(data) else _parse_ascii(data)
    if not tris:
        # Some ASCII files start with "solid" but are actually binary.
        tris = _parse_binary(data)
    positions: list[float] = []
    for (a, b, c) in tris:
        positions.extend(a)
        positions.extend(b)
        positions.extend(c)
    indices = list(range(len(positions) // 3))
    xs = positions[0::3] or [0.0]
    ys = positions[1::3] or [0.0]
    zs = positions[2::3] or [0.0]
    bbox = {
        "x": round(max(xs) - min(xs), 3),
        "y": round(max(ys) - min(ys), 3),
        "z": round(max(zs) - min(zs), 3),
    }
    mesh = PreviewMesh(
        positions=[round(p, 4) for p in positions],
        indices=indices,
        vertex_count=len(indices),
        triangle_count=len(tris),
    )
    return mesh, bbox


def _looks_ascii(data: bytes) -> bool:
    head = data[:256].lstrip().lower()
    return head.startswith(b"solid") and b"facet" in data[:2048].lower()


def _parse_ascii(data: bytes) -> list[tuple]:
    tris: list[tuple] = []
    verts: list[tuple] = []
    for line in data.decode("ascii", "replace").splitlines():
        s = line.strip()
        if s.startswith("vertex"):
            parts = s.split()
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            if len(verts) == 3:
                tris.append(tuple(verts))
                verts = []
    return tris


def _parse_binary(data: bytes) -> list[tuple]:
    if len(data) < 84:
        return []
    (count,) = struct.unpack_from("<I", data, 80)
    tris: list[tuple] = []
    off = 84
    for _ in range(count):
        if off + 50 > len(data):
            break
        vals = struct.unpack_from("<12f", data, off)  # normal(3) + 3 verts(9)
        tris.append(((vals[3], vals[4], vals[5]), (vals[6], vals[7], vals[8]),
                     (vals[9], vals[10], vals[11])))
        off += 50
    return tris
