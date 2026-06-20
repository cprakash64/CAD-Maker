"""Render screenshot/thumbnail regression outputs for the 10 manual prompts.

    CADMAKER_SANDBOX=inprocess python -m scripts.render_thumbnails

Compiles each prompt, renders a shaded 3D thumbnail PNG, and writes an index JSON
with the geometric facts (genus/through-holes, components, profile corners) so a
human can eyeball that holes are actually cut and the profile is right.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from app.generation.compiler import compile_prompt  # noqa: E402
from app.generation.code_sandbox import run_program  # noqa: E402
from app.generation.cad_programs import generate_program  # noqa: E402
from app.generation.mesh_analysis import _triangles, analyze_stl  # noqa: E402
from app.llm.mock_provider import MockLLMProvider  # noqa: E402

PROMPTS = [
    "a simple bearing housing for a 20mm shaft",
    "a rectangular block with a stepped slot and two counterbored holes",
    "a hexagonal spacer with a 6mm through hole",
    "a shaft collar with an M6 clamp screw",
    "a flange plate with 8 holes on a 100mm bolt circle",
    "a pulley with a 10mm shaft hole and 60mm outer diameter",
    "a hexagonal gear with a 10mm shaft",
    "a 90 degree pipe elbow with circular flanges",
    "a small vise jaw with two mounting holes and a V groove",
    "a motor mounting plate for a NEMA 17 stepper",
    "full inline-four crankshaft",  # template path (no compiler family)
]

OUT = Path(__file__).resolve().parent.parent / "reports" / "thumbnails"


def _render(tris, path: Path, title: str) -> None:
    fig = plt.figure(figsize=(3, 3))
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(tris, edgecolor=(0, 0, 0, 0.08), linewidths=0.1)
    coll.set_facecolor((0.55, 0.68, 0.86))
    ax.add_collection3d(coll)
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    if xs:
        rng = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1
        cx, cy, cz = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2
        ax.set_xlim(cx - rng / 2, cx + rng / 2)
        ax.set_ylim(cy - rng / 2, cy + rng / 2)
        ax.set_zlim(cz - rng / 2, cz + rng / 2)
    ax.view_init(elev=35, azim=-50)
    ax.set_axis_off()
    ax.set_title(title, fontsize=7)
    fig.savefig(path, dpi=90, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    provider = MockLLMProvider()
    index = []
    for prompt in PROMPTS:
        if generate_program(prompt) is not None:
            out = compile_prompt(prompt, provider)
            if not (out and out.ok):
                index.append({"prompt": prompt, "ok": False,
                              "reason": out.report.summary() if out and out.report else "no program"})
                continue
            stl = out.result.stl_bytes
            family = out.brief.object_family
            stats = analyze_stl(stl)
            tris = _triangles(stl)
            png = OUT / f"{family}.png"
            _render(tris, png, family)
            index.append({
                "prompt": prompt, "ok": True, "family": family, "thumbnail": png.name,
                "through_holes": stats.through_holes, "components": stats.components,
                "outer_corners": stats.outer_corner_count, "watertight": stats.watertight,
                "semantic_passed": out.report.passed,
            })
            print(f"  {family:20s} holes={stats.through_holes} comps={stats.components} -> {png.name}")
        else:  # template path (e.g. crankshaft)
            from app.parsing.complex_plan import plan_prompt
            from app.export.exporter import generate
            r = plan_prompt(prompt)
            if r.spec is None:
                index.append({"prompt": prompt, "ok": False, "reason": "no spec"})
                continue
            gen = generate(r.spec)
            stats = analyze_stl(gen.stl_bytes)
            tris = _triangles(gen.stl_bytes)
            png = OUT / f"{r.spec.object_type}.png"
            _render(tris, png, r.spec.object_type)
            index.append({"prompt": prompt, "ok": True, "family": r.spec.object_type,
                          "thumbnail": png.name, "through_holes": stats.through_holes,
                          "components": stats.components})
            print(f"  {r.spec.object_type:20s} (template) -> {png.name}")

    (OUT / "index.json").write_text(json.dumps(index, indent=2))
    print(f"Wrote {len(index)} thumbnails + index.json to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
