"""Manual review helper: export the reference buggy chassis to backend/tmp/.

    python -m scripts.export_reference_chassis

Writes (backend/tmp/ is gitignored — artifacts are never committed):
  reference_buggy_chassis.step
  reference_buggy_chassis.stl
  reference_buggy_chassis_metadata.json

This is a debug/visual-inspection tool only — not part of the request pipeline.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.cad.assembly.chassis import build_chassis
from app.cad.assembly.report import build_assembly_report
from app.cad.plan.compiler import export_solid

_PROMPT = (
    "Create a detailed 3D CAD model of a rear-wheel-drive sports car chassis frame "
    "using welded steel tubular construction, roll cage structure, side-impact bars, "
    "suspension mounting points, body panels. Approximately 4200 mm long, 1800 mm "
    "wide, and 1200 mm high."
)


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "tmp"
    out.mkdir(parents=True, exist_ok=True)

    build = build_chassis(_PROMPT)
    stl, step, _ = export_solid(build.solid)
    report = build_assembly_report(build, stl, step)

    (out / "reference_buggy_chassis.step").write_bytes(step)
    (out / "reference_buggy_chassis.stl").write_bytes(stl)
    (out / "reference_buggy_chassis_metadata.json").write_text(
        json.dumps({"measured": report["measured"], "snapshot": report["snapshot"],
                    "validation": report["validation"], "spec": report["spec"]},
                   indent=2, default=str)
    )
    m = report["measured"]
    print(f"style={m['chassis_style']} tubes={m['tube_count']} components={m['component_count']} "
          f"plates={m['plate_count']} holes={m['hole_feature_count']} slots={m['slot_feature_count']} "
          f"status={report['validation']['status']}")
    print(f"wrote STEP/STL/metadata to {out}")


if __name__ == "__main__":
    main()
