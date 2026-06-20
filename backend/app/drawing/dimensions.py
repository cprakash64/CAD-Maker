"""Template-based dimension annotations for drawing views.

Overall extents are read straight from the projected geometry (for orthographic
views the projected extent equals the real mm length). Template-specific
callouts (hole diameter, spacing, wall thickness, key journals) come from the
validated spec — only where reliable.
"""
from __future__ import annotations

from app.schemas.design_spec import DesignSpec

# Which real-world dimension each view's (u, w) axes correspond to, for labels.
_AXIS_LABEL = {
    "top": ("width (X)", "depth (Y)"),
    "front": ("width (X)", "height (Z)"),
    "right": ("depth (Y)", "height (Z)"),
    "left": ("depth (Y)", "height (Z)"),
}


def dimension_lines(spec: DesignSpec, view: str, bounds) -> list[dict]:
    """Overall dimension arrows for a view. Returns [{axis: 'u'|'w', label}]."""
    umin, umax, wmin, wmax = bounds
    labels = _AXIS_LABEL.get(view)
    if not labels:
        return []
    return [
        {"axis": "u", "label": f"{umax - umin:.1f} mm"},
        {"axis": "w", "label": f"{wmax - wmin:.1f} mm"},
    ]


def dimension_notes(spec: DesignSpec, view: str) -> list[str]:
    """Reliable template-specific callouts shown as a corner note."""
    notes: list[str] = []
    dims = spec.dimensions
    ot = spec.object_type

    if spec.holes and view in ("top", "front"):
        d = spec.to_mm(spec.holes[0].diameter)
        n = len(spec.holes)
        notes.append(f"{n}x ø{d:.1f} hole" + ("s" if n != 1 else ""))
        # Hole spacing for a simple 2-hole pattern along X.
        if n >= 2:
            xs = sorted(spec.to_mm(h.x) for h in spec.holes)
            spacing = xs[-1] - xs[0]
            if spacing > 0:
                notes.append(f"hole spacing {spacing:.1f}")

    if "wall_thickness" in dims:
        notes.append(f"wall {spec.to_mm(dims['wall_thickness']):.1f}")
    if "thickness" in dims and view in ("front", "right", "left"):
        notes.append(f"thickness {spec.to_mm(dims['thickness']):.1f}")
    if ot == "spacer" and "bore_diameter" in dims:
        notes.append(f"bore ø{spec.to_mm(dims['bore_diameter']):.1f}")
    if ot == "inline_4_crankshaft":
        if "main_journal_diameter_mm" in dims or True:
            notes.append("5 main / 4 rod journals")

    return notes
