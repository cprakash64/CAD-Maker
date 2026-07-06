"""Dev-only debug overlay for the Drawing → CAD sketch reconstruction.

Renders the reconstructed MechanicalSketchIR back onto (a lightened copy of) the
source drawing so a developer can SEE what was detected and what was ignored:

* green   — outer profile
* blue    — circular holes
* orange  — slots (rectangular / rounded / arc)
* magenta — counterbores / nested groups
* grey    — construction / annotation / noise that was ignored

Returns PNG bytes; never raises (returns None on any failure). The IR JSON is
served alongside it by the debug endpoint.
"""
from __future__ import annotations

import io

from app.drawing.sketch_ir import MechanicalSketchIR
from app.observability import log_event

_COLORS = {
    "outer": (34, 197, 94),      # green
    "circle": (37, 99, 235),     # blue   — circular_hole
    "slot": (234, 88, 12),       # orange — rounded/arc slot
    "rectangle": (22, 163, 74),  # green  — rectangular cutout/slot
    "arched": (6, 182, 212),     # cyan   — arched_rectangular_cutout
    "freeform": (234, 179, 8),   # yellow — polygon / freeform cutout
    "recess": (219, 39, 119),    # pink   — recessed channel / blind recess
    "nested": (168, 85, 247),    # purple — concentric group
    "ignored": (156, 64, 64),    # grey-red — ignored annotation
}

# Per cut-kind overlay colour. A curved-top window is CYAN — never blue (blue is
# reserved for a true circular hole), so a mis-classification is obvious.
_CUT_COLOR = {
    "circular_hole": "circle",
    "rectangular_slot": "rectangle",
    "rounded_slot": "slot",
    "arc_slot": "slot",
    "arched_rectangular_cutout": "arched",
    "polygon_hole": "freeform",
    "arbitrary_cutout": "freeform",
    "recessed_channel": "recess",
    "blind_recess": "recess",
    "through_channel_cut": "recess",
}


def render_debug_overlay(image_bytes: bytes,
                         ir: MechanicalSketchIR) -> bytes | None:
    """Draw the IR features over the source image. Never raises."""
    try:
        return _render(image_bytes, ir)
    except Exception as exc:  # noqa: BLE001 - debug artifact is best-effort
        log_event("drawing_debug_overlay_failed", reason=type(exc).__name__)
        return None


def _render(image_bytes: bytes, ir: MechanicalSketchIR) -> bytes | None:
    from PIL import Image, ImageDraw

    if ir.outer is None or not ir.scale:
        return None
    base = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    W, H = base.size
    # Lighten the drawing so overlays read clearly.
    canvas = Image.blend(base, Image.new("RGB", base.size, (255, 255, 255)), 0.6)
    d = ImageDraw.Draw(canvas)

    px_per_mm = ir.scale.px_per_mm
    ow = ir.outer.bbox_mm.get("w", 0) * px_per_mm
    oh = ir.outer.bbox_mm.get("h", 0) * px_per_mm
    # Place the sketch's centred-mm origin at the image centre (approximation —
    # the overlay is illustrative, not pixel-exact).
    ox, oy = W / 2.0, H / 2.0

    def to_px(mm_xy):
        return (ox + mm_xy[0] * px_per_mm, oy - mm_xy[1] * px_per_mm)

    # Outer profile.
    if len(ir.outer.vertices) >= 3:
        pts = [to_px(v) for v in ir.outer.vertices]
        d.line(pts + [pts[0]], fill=_COLORS["outer"], width=3)

    for cf in ir.cut_features:
        col = _COLORS[_CUT_COLOR.get(cf.kind, "freeform")]
        if cf.kind == "circular_hole" and cf.diameter_mm:
            cx, cy = to_px(cf.center)
            r = cf.diameter_mm / 2 * px_per_mm
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=3)
        elif len(cf.vertices) >= 3:
            pts = [to_px(v) for v in cf.vertices]
            d.line(pts + [pts[0]], fill=col, width=3)

    # Concentric stacks: draw EVERY ring of the group (a Ø8.2/4.8/3.6 stack shows
    # all three circles), falling back to the 2-ring nested view when the richer
    # group list is absent.
    groups = getattr(ir, "concentric_groups", None) or []
    if groups:
        for g in groups:
            cx, cy = to_px(g.center)
            for dia in g.circles_mm:
                r = dia / 2 * px_per_mm
                d.ellipse((cx - r, cy - r, cx + r, cy + r),
                          outline=_COLORS["nested"], width=3)
    else:
        for nf in ir.nested_features:
            cx, cy = to_px(nf.center)
            for dia in (nf.outer_diameter_mm, nf.inner_diameter_mm):
                r = dia / 2 * px_per_mm
                d.ellipse((cx - r, cy - r, cx + r, cy + r),
                          outline=_COLORS["nested"], width=3)

    for ce in ir.construction_entities:
        if len(ce.bbox) == 4:
            d.rectangle(ce.bbox, outline=_COLORS["ignored"], width=1)

    # Legend.
    legend = [("outer profile", "outer"), ("circular hole", "circle"),
              ("rect cutout", "rectangle"), ("arched window", "arched"),
              ("freeform cutout", "freeform"), ("recess/channel", "recess"),
              ("slot", "slot"), ("concentric group", "nested"),
              ("ignored", "ignored")]
    for i, (label, key) in enumerate(legend):
        y = 8 + i * 18
        d.rectangle((8, y, 24, y + 12), fill=_COLORS[key])
        d.text((30, y), label, fill=(30, 30, 30))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
