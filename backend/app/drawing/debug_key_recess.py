"""Local debug for the key/channel recess detector.

    python -m app.drawing.debug_key_recess path/to/key.png [thickness_mm]

Prints the dark-region candidates (accept/reject reasons), the reconstructed IR
``cut_by_kind``, through-hole count, and each recess depth — so the runtime
classification can be inspected directly instead of guessed at. Also writes a
colour-coded debug overlay next to the image.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = Path(argv[0])
    thickness = float(argv[1]) if len(argv) > 1 else 6.0
    if not path.exists():
        print(f"no such file: {path}")
        return 2
    image_bytes = path.read_bytes()

    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir, resolve_recess_depth
    from app.drawing.vectorize import build_sketch_ir

    # Capture the candidate log events emitted during vectorization.
    import app.observability as obs

    events: list[dict] = []
    orig = obs.log_event

    def _capture(event, **kw):
        if event in ("drawing_recess_detector_started", "drawing_dark_region_candidate",
                     "drawing_dark_regions_neutralized", "drawing_dark_region_detected",
                     "drawing_slot_suppressed_by_dark_recess", "drawing_loop_classified"):
            events.append({"event": event, **kw})
        return orig(event, **kw)

    obs.log_event = _capture
    try:
        ir = build_sketch_ir(image_bytes, thickness_mm=thickness)
    finally:
        obs.log_event = orig

    print("=== recess detector events ===")
    for e in events:
        print(json.dumps(e))
    if ir is None:
        print("\nvectorizer returned None (no usable outer profile)")
        return 1

    kinds = Counter(c.kind for c in ir.cut_features)
    recess = [c for c in ir.cut_features
              if c.kind in ("recessed_channel", "blind_recess", "through_channel_cut")]
    through_holes = sum(1 for c in ir.cut_features if c.kind == "circular_hole") \
        + len(ir.concentric_groups)
    print("\n=== reconstructed IR ===")
    print("cut_by_kind:", dict(kinds))
    print("through_holes:", through_holes)
    print("recesses:", [(c.kind, "through" if c.through else "blind") for c in recess])
    print("recess_depth_mm (for thickness %g):" % thickness, resolve_recess_depth(thickness))

    plan = generate_cad_from_sketch_ir(ir, default_thickness_mm=thickness)
    if plan is not None:
        rfeat = [f for f in plan.features if f.id.startswith("recess")]
        print("plan recess features:",
              [(f.id, "through" if f.through else "blind", f.p("recess_depth", 0))
               for f in rfeat])

    try:
        from app.drawing.debug_overlay import render_debug_overlay

        png = render_debug_overlay(image_bytes, ir)
        if png:
            out = path.with_name(path.stem + "_recess_overlay.png")
            out.write_bytes(png)
            print("\noverlay written:", out)
    except Exception as exc:  # noqa: BLE001
        print("overlay skipped:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
