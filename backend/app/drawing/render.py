"""Render orthographic / isometric drawing views from a model's mesh.

Uses matplotlib's Agg (PNG) and SVG backends — headless, no GPU. Triangles from
the real tessellated solid are projected onto each view plane and drawn back-to-
front (painter's algorithm) for a clean solid look. Overall dimensions are taken
from the model's true bounding box; template-specific callouts come from the
validated spec, so every annotation reflects real geometry.
"""
from __future__ import annotations

import io
import math

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

from app.drawing import STANDARD_VIEWS  # noqa: E402
from app.drawing.dimensions import dimension_lines, dimension_notes  # noqa: E402
from app.export.exporter import mesh_only  # noqa: E402
from app.schemas.design_spec import DesignSpec  # noqa: E402

_COS30 = math.cos(math.pi / 6)
_SIN30 = math.sin(math.pi / 6)


def _project(verts: np.ndarray, view: str):
    """Return (u, w, depth) arrays. Larger depth = nearer the camera."""
    x, y, z = verts[:, 0], verts[:, 1], verts[:, 2]
    if view == "top":
        return x, y, z
    if view == "front":
        return x, z, -y
    if view == "right":
        return y, z, x
    if view == "left":
        return -y, z, -x
    if view == "iso":
        u = (x - y) * _COS30
        w = z + (x + y) * _SIN30
        return u, w, (x + y + z)
    raise ValueError(f"unknown view '{view}'")


def render_view(spec: DesignSpec, view: str, fmt: str = "png") -> bytes:
    if view not in STANDARD_VIEWS:
        raise ValueError(f"unknown view '{view}'")
    if fmt not in ("png", "svg"):
        raise ValueError(f"unsupported format '{fmt}'")

    mesh = mesh_only(spec)
    verts = np.array(mesh.positions, dtype=float).reshape(-1, 3)
    tris = np.array(mesh.indices, dtype=int).reshape(-1, 3)

    u, w, depth = _project(verts, view)
    tri_depth = depth[tris].mean(axis=1)
    order = np.argsort(tri_depth)  # far -> near

    polys = np.stack([u[tris], w[tris]], axis=-1)[order]  # (n_tri, 3, 2)

    fig, ax = plt.subplots(figsize=(6, 6), dpi=100)
    coll = PolyCollection(
        polys, facecolors="#c9d4e8", edgecolors="#33415c", linewidths=0.15
    )
    ax.add_collection(coll)
    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(f"{spec.object_type} — {view} view", fontsize=10, color="#1f2a44")

    # Dimension annotations (orthographic views only; iso is pictorial).
    if view != "iso":
        _draw_dimensions(ax, spec, view, u, w)

    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _draw_dimensions(ax, spec: DesignSpec, view: str, u, w) -> None:
    umin, umax = float(u.min()), float(u.max())
    wmin, wmax = float(w.min()), float(w.max())
    span_u, span_w = umax - umin, wmax - wmin
    pad = 0.08 * max(span_u, span_w, 1.0)

    for dim in dimension_lines(spec, view, (umin, umax, wmin, wmax)):
        if dim["axis"] == "u":  # horizontal dimension below the part
            y = wmin - pad
            ax.annotate(
                "", xy=(umin, y), xytext=(umax, y),
                arrowprops=dict(arrowstyle="<->", color="#b03a2e", lw=0.8),
            )
            ax.text((umin + umax) / 2, y - pad * 0.4, dim["label"],
                    ha="center", va="top", fontsize=8, color="#b03a2e")
        else:  # vertical dimension left of the part
            xpos = umin - pad
            ax.annotate(
                "", xy=(xpos, wmin), xytext=(xpos, wmax),
                arrowprops=dict(arrowstyle="<->", color="#b03a2e", lw=0.8),
            )
            ax.text(xpos - pad * 0.4, (wmin + wmax) / 2, dim["label"],
                    ha="right", va="center", rotation=90, fontsize=8, color="#b03a2e")

    # Template callouts (e.g. hole diameter, wall thickness) as a corner note.
    notes = dimension_notes(spec, view)
    if notes:
        ax.text(umin, wmax + pad, "   ".join(notes), fontsize=7, color="#1f2a44",
                ha="left", va="bottom")


def render_all_views(spec: DesignSpec, fmt: str = "png") -> dict[str, bytes]:
    return {v: render_view(spec, v, fmt) for v in STANDARD_VIEWS}
