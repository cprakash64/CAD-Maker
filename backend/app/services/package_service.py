"""Build the downloadable CAD package and the manufacturing report.

The package bundles everything a maker needs to take the part into another tool:
STEP, STL, the validated DesignSpec, a manufacturing report (JSON + text), and
the drawing views (PNG + SVG).
"""
from __future__ import annotations

import io
import json
import zipfile

from app.drawing import STANDARD_VIEWS
from app.drawing.render import render_view
from app.explain import explain
from app.export.exporter import generate
from app.manufacturability.checks import run_checks
from app.schemas.design_spec import DesignSpec


def manufacturing_report(spec: DesignSpec) -> dict:
    checks = run_checks(spec)
    gen = generate(spec)
    failed = [c for c in checks if not c.passed]
    return {
        "object_type": spec.object_type,
        "material": spec.material,
        "manufacturing_method": spec.manufacturing_method,
        "units": "mm",
        "bounding_box_mm": gen.bounding_box_mm,
        "explanation": explain(spec),
        "dimensions_mm": spec.dims_in_mm(),
        "hole_count": len(spec.holes),
        "checks": [
            {"check": c.check, "severity": c.severity.value, "passed": c.passed,
             "message": c.message}
            for c in checks
        ],
        "warnings": [c.message for c in failed if c.severity.value == "warning"],
        "errors": [c.message for c in failed if c.severity.value == "error"],
        "summary": (
            f"{len(checks)} checks run, "
            f"{sum(1 for c in checks if not c.passed)} flagged."
        ),
    }


def _report_text(report: dict) -> str:
    lines = [
        "SourceCAD AI Part Studio — Manufacturing Report",
        "=" * 48,
        f"Part type : {report['object_type']}",
        f"Material  : {report['material']}",
        f"Method    : {report['manufacturing_method']}",
        f"Bounding  : {report['bounding_box_mm']} mm",
        "",
        report["explanation"],
        "",
        "Checks:",
    ]
    for c in report["checks"]:
        mark = "OK " if c["passed"] else "!! "
        lines.append(f"  {mark}[{c['severity']}] {c['check']}: {c['message']}")
    return "\n".join(lines) + "\n"


def build_package_zip(spec: DesignSpec, design_id: str) -> bytes:
    gen = generate(spec)
    report = manufacturing_report(spec)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{spec.object_type}.step", gen.step_bytes)
        zf.writestr(f"{spec.object_type}.stl", gen.stl_bytes)
        zf.writestr("design_spec.json", json.dumps(spec.model_dump(mode="json"), indent=2))
        zf.writestr("manufacturing_report.json", json.dumps(report, indent=2))
        zf.writestr("manufacturing_report.txt", _report_text(report))
        for view in STANDARD_VIEWS:
            zf.writestr(f"drawings/{view}.png", render_view(spec, view, "png"))
            zf.writestr(f"drawings/{view}.svg", render_view(spec, view, "svg"))
        zf.writestr("README.txt", _PACKAGE_README.format(part=spec.object_type))
    return buf.getvalue()


_PACKAGE_README = """SourceCAD AI Part Studio — CAD Package
=======================================
Part: {part}

Contents:
  {part}.step              STEP (AP214) solid — import into Fusion 360 / FreeCAD / SolidWorks
  {part}.stl               Mesh for 3D printing / preview
  design_spec.json         The exact validated parameters used to generate this part
  manufacturing_report.*   Checks, warnings and assumptions (JSON + text)
  drawings/*.png|.svg      Top / front / right / left / isometric views

See the in-app "Import & Compatibility" docs for Fusion 360, AutoCAD and FreeCAD steps.
"""
