"""Validate a compiled CadPlan — ASSUMPTION-FIRST.

A model that COMPILES and EXPORTS is shippable. Validation therefore has exactly
two fatal (export-blocking) checks:

  * not_empty       — the solid actually has geometry
  * exports_present — STEP + STL were written non-empty

Everything else (bounding-box mismatch, hole-count / through-hole-count
mismatch, the mesh-genus "holes really cut" signal) is a NON-BLOCKING WARNING.
These warnings are informational — the model is still shown with downloads.

Why warnings, not errors: through-hole counting from raw mesh genus is brittle
(a single coaxial pin cut through two hinge ears is one *intent* hole but two
physical openings; a part may legitimately have more/fewer topological holes than
a naive expected count). User intent is tracked separately in ``expected.*_count``
and surfaced, but it never blocks a compiled model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.cad.plan.compiler import CadPlanResult
from app.cad.plan.dimension_report import build_dimension_report
from app.cad.plan.schema import CadPlan
from app.config import settings
from app.generation.mesh_analysis import analyze_stl

# The ONLY checks that block export. Everything else is advisory.
FATAL_CHECKS = {"not_empty", "exports_present", "impossible_geometry"}


@dataclass
class Check:
    name: str
    passed: bool
    expected: str | None = None
    actual: str | None = None
    severity: str = "warning"  # warning | error (error == export-blocking)


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)
    dimension_report: dict | None = None  # requested vs measured + print readiness

    @property
    def passed(self) -> bool:
        """True when nothing FATAL failed — i.e. the model is exportable."""
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def has_warnings(self) -> bool:
        return any(not c.passed for c in self.checks if c.severity == "warning")

    def warning_messages(self) -> list[str]:
        out = []
        for c in self.checks:
            if c.passed or c.severity == "error":
                continue
            label = c.name.replace("_", " ")
            if c.expected is not None:
                out.append(f"{label}: expected {c.expected}, got {c.actual}")
            else:
                out.append(label)
        return out

    def to_semantic_json(self) -> dict:
        return {
            "checks": [
                {"name": c.name, "passed": c.passed, "expected": c.expected,
                 "actual": c.actual, "severity": c.severity}
                for c in self.checks
            ],
            "passed": self.passed,
            "has_warnings": self.has_warnings,
            "summary": self.summary(),
            "dimension_report": self.dimension_report,
        }

    def summary(self) -> str:
        ok = sum(1 for c in self.checks if c.passed)
        return f"{ok}/{len(self.checks)} checks passed"

    def diagnostics(self) -> str:
        """FATAL-only failure text, fed to the LLM for the one-shot repair pass.
        Warnings never trigger a repair (the model is already shippable)."""
        fails = [c for c in self.checks if not c.passed and c.severity == "error"]
        if not fails:
            return ""
        lines = ["The compiled model failed these BLOCKING checks — fix the feature graph:"]
        for c in fails:
            detail = f" (expected {c.expected}, got {c.actual})" if c.expected is not None else ""
            lines.append(f"- {c.name}{detail}")
        return "\n".join(lines)


def _bbox_match(expected: dict, actual: dict) -> tuple[bool, str, str]:
    ok = True
    for axis in ("x", "y", "z"):
        if axis not in expected:
            continue
        exp = float(expected[axis])
        act = float(actual.get(axis, 0.0))
        tol = max(2.0, abs(exp) * 0.05)  # generous: 5% or 2mm — advisory only
        if abs(exp - act) > tol:
            ok = False
    fmt = lambda d: "×".join(str(round(d.get(a, 0), 1)) for a in ("x", "y", "z"))
    return ok, fmt(expected), fmt(actual)


def validate(
    plan: CadPlan, result: CadPlanResult, stl_bytes: bytes, step_bytes: bytes
) -> ValidationReport:
    report = ValidationReport()
    exp = plan.expected

    # --- FATAL: must hold for an exportable model -------------------------
    tri = 0
    stats = None
    if stl_bytes:
        stats = analyze_stl(stl_bytes)
        tri = stats.triangles
    report.checks.append(Check(
        name="not_empty", passed=tri > 0, severity="error",
        expected=">0 triangles", actual=str(tri),
    ))

    fmts = exp.export_formats or ["step", "stl"]
    need_step, need_stl = "step" in fmts, "stl" in fmts
    exports_ok = (not need_stl or len(stl_bytes) > 0) and (not need_step or len(step_bytes) > 0)
    report.checks.append(Check(
        name="exports_present", passed=exports_ok, severity="error",
        expected=f"non-empty {'+'.join(fmts)}",
        actual=f"stl={len(stl_bytes)}B step={len(step_bytes)}B",
    ))

    # --- ADVISORY warnings (never block export) ---------------------------
    if exp.bbox_mm:
        ok, e, a = _bbox_match(exp.bbox_mm, result.bbox_mm)
        report.checks.append(Check(name="bbox_match", passed=ok, severity="warning",
                                   expected=e, actual=a))

    if exp.hole_count is not None:
        report.checks.append(Check(
            name="hole_count", passed=result.hole_count == exp.hole_count,
            severity="critical", expected=str(exp.hole_count), actual=str(result.hole_count),
        ))

    if exp.through_hole_count is not None:
        report.checks.append(Check(
            name="through_hole_count",
            passed=result.through_hole_count == exp.through_hole_count,
            severity="critical",
            expected=str(exp.through_hole_count), actual=str(result.through_hole_count),
        ))

    # Mesh-genus "holes really cut" signal — advisory only. Brittle by nature
    # (coaxial cuts, bores/lumens), so it informs but never blocks.
    want_through = exp.through_hole_count
    if want_through and want_through > 0 and stats is not None:
        report.checks.append(Check(
            name="through_holes_cut_geometry",
            passed=stats.through_holes >= want_through,
            severity="warning",
            expected=f">={want_through} (mesh genus)", actual=str(stats.through_holes),
        ))

    # --- DIMENSION REPORT + 3D-PRINT READINESS ----------------------------
    # Compute BRep + mesh ground truth and compare against the requested dims.
    # Geometry-trust failures (disconnected bodies, dim drift, hole mismatch,
    # non-watertight/non-manifold, zero volume) are CRITICAL — surfaced via the
    # report's validation status. They do NOT change export-blocking semantics
    # (report.passed stays based on not_empty/exports_present), so an inspectable
    # model is still produced, but it is clearly marked "Failed validation".
    report.dimension_report = build_dimension_report(plan, result, stl_bytes)
    measured = report.dimension_report["measured"]
    pr = report.dimension_report["print_readiness"]

    wt = report.dimension_report["within_tolerance"]
    if wt is not None:
        report.checks.append(Check(
            name="dimensions_within_tolerance", passed=wt, severity="critical",
            expected="all measured dims within tolerance",
            actual="within tolerance" if wt else "drift detected",
        ))
    report.checks.append(Check(
        name="watertight_manifold",
        passed=bool(measured["watertight"] and measured["manifold"]),
        severity="critical", expected="closed, manifold mesh",
        actual=f"watertight={measured['watertight']} manifold={measured['manifold']}",
    ))
    report.checks.append(Check(
        name="single_body", passed=measured["components"] == 1, severity="critical",
        expected="1 fused body", actual=f"{measured['components']} component(s)",
    ))
    report.checks.append(Check(
        name="positive_volume", passed=measured["volume_mm3"] > 0, severity="critical",
        expected=">0 mm³", actual=f"{measured['volume_mm3']} mm³",
    ))
    if pr["min_hole_diameter_mm"] is not None:
        report.checks.append(Check(
            name="printable_hole_size",
            passed=pr["min_hole_diameter_mm"] >= settings.printer_min_hole_mm,
            severity="warning", expected=f">= {settings.printer_min_hole_mm}mm",
            actual=f"min hole Ø{pr['min_hole_diameter_mm']}mm",
        ))

    return report
