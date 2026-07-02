"""Generate debug previews + exports for the tire / rim / wheel-assembly families.

    python -m scripts.tire_debug

For each prompt it runs the REAL deterministic router (``detect_part_request``),
builds the spec, generates STL + STEP, and renders front / isometric / side PNGs so
a human can eyeball that the tire looks like a real tire (rounded crown + shoulders,
bulged sidewalls, open bore, tread on the crown) and that the wheel assembly shows a
rim seated inside the tire. Outputs go to ``backend/tmp/tire_debug/`` (git-ignored).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from app.cad.part_family import detect_part_request  # noqa: E402
from app.export.exporter import generate  # noqa: E402
from app.generation.mesh_analysis import _triangles, analyze_stl  # noqa: E402
from app.schemas.design_spec import DesignSpec  # noqa: E402

PROMPTS = [
    ("01_generic_make_a_tire", "Make a tire"),
    ("02_generic_100mm_tire", "Create a 100 mm tire"),
    ("03_street_100x60x30", "Create a 100 mm street tire, 60 mm inner diameter, 30 mm wide"),
    ("04_slick_racing_100x60x30", "Create a slick racing tire, 100 mm outer diameter, "
                                  "60 mm inner diameter, 30 mm wide"),
    ("05_all_terrain_100x60x32", "Create an all-terrain tire, 100 mm outer diameter, "
                                 "60 mm inner diameter, 32 mm wide"),
    ("06_off_road_100x60x30", "Create an off-road tire with aggressive chunky tread, "
                              "100 mm outer diameter, 60 mm inner diameter, 30 mm wide"),
    ("07_street_wheel_assembly", "Create a complete street wheel assembly with a "
                                 "matching 5-spoke rim"),
    ("08_off_road_wheel_assembly", "Create a complete off-road wheel assembly with "
                                   "aggressive tread and a matching rim"),
]

OUT = Path(__file__).resolve().parent.parent / "tmp" / "tire_tread_styles"

_VIEWS = [("front", 90, -90), ("iso", 24, -60), ("side", 8, 0)]


def _render(tris, path: Path, title: str) -> None:
    fig = plt.figure(figsize=(9, 3))
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    rng = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1
    ctr = [(max(a) + min(a)) / 2 for a in (xs, ys, zs)]
    for i, (name, elev, azim) in enumerate(_VIEWS):
        ax = fig.add_subplot(1, 3, i + 1, projection="3d")
        coll = Poly3DCollection(tris, edgecolor=(0, 0, 0, 0.06), linewidths=0.1)
        coll.set_facecolor((0.62, 0.63, 0.66))
        ax.add_collection3d(coll)
        for lim, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), ctr):
            lim(c - rng / 2, c + rng / 2)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        ax.set_title(f"{title} — {name}", fontsize=8)
    fig.savefig(path, dpi=95, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, prompt in PROMPTS:
        req = detect_part_request(prompt)
        if req is None or req.object_type is None:
            print(f"  {tag:16s} SKIP (not routed): {prompt}")
            continue
        spec = DesignSpec(object_type=req.object_type, dimensions=req.params,
                          manufacturing_method="cnc_milling",
                          material="rubber" if req.object_type == "tire" else "aluminum")
        res = generate(spec)
        stl_path = OUT / f"{tag}.stl"
        step_path = OUT / f"{tag}.step"
        png_path = OUT / f"{tag}.png"
        stl_path.write_bytes(res.stl_bytes)
        step_path.write_bytes(res.step_bytes)
        stats = analyze_stl(res.stl_bytes)
        _render(_triangles(res.stl_bytes), png_path, tag)
        bb = res.bounding_box_mm
        tread = f"{req.tread_style}({req.tread_style_source})" if req.tread_style else "-"
        print(f"  {tag:26s} {req.object_type:14s} tread={tread:20s} "
              f"bbox={bb['x']:.1f}x{bb['y']:.1f}x{bb['z']:.1f} "
              f"comps={stats.components} wt={stats.watertight} tris={res.preview.triangle_count}")
        print(f"      -> {png_path}")
        print(f"      -> {stl_path}")
        print(f"      -> {step_path}")
    print(f"\nDebug artifacts written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
