"""The CAD compiler: prompt -> brief -> program -> sandbox -> verify -> repair.

This is the general generation path. Templates remain available as a mode, but
broad mechanical prompts are generated as restricted, sandbox-run CadQuery
programs and validated by the semantic verifier before being accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.cad.base import CadGenerationError
from app.export.exporter import GenerationResult
from app.generation.code_sandbox import run_program
from app.generation.semantic_verifier import verify
from app.generation.stl_preview import parse_stl
from app.observability import log_event
from app.schemas.brief import CADDesignBrief, CADProgramSpec, SemanticReport


@dataclass
class CompileOutput:
    ok: bool
    result: GenerationResult | None = None
    brief: CADDesignBrief | None = None
    program: CADProgramSpec | None = None
    report: SemanticReport | None = None
    route: str = "cadquery_program"
    repair_attempts: int = 0
    assumptions: list[str] = field(default_factory=list)
    export_formats: list[str] = field(default_factory=lambda: ["stl", "step"])
    code: str | None = None
    explanation: str | None = None
    clarification: str | None = None


def compile_prompt(prompt: str, provider, max_repairs: int = 2) -> CompileOutput | None:
    """Run the compiler. Returns None if the provider can't author a program for
    this prompt (caller may fall back to templates/feature-graph/clarification)."""
    trusted = getattr(provider, "name", "") == "mock"
    feedback = None
    last_report: SemanticReport | None = None
    last_brief = last_program = None

    for attempt in range(max_repairs + 1):
        out = provider.cad_program(prompt, feedback=feedback)
        if not out:
            return None  # provider declines -> caller falls back
        brief, program = out
        last_brief, last_program = brief, program
        if not program.generated_code:
            return None
        try:
            stl, step, meta = run_program(program.generated_code, trusted=trusted)
        except CadGenerationError as exc:
            feedback = f"The program failed to build: {exc}. Fix it."
            log_event("compiler_build_failed", attempt=attempt, detail=str(exc)[:200])
            continue

        mesh, bbox = parse_stl(stl)
        from app.generation.mesh_analysis import analyze_stl
        stats = analyze_stl(stl)
        report = verify(brief, meta, bbox, mesh=stats)
        last_report = report
        if report.passed:
            result = GenerationResult(
                spec_hash=_hash(program.generated_code),
                stl_bytes=stl, step_bytes=step, preview=mesh,
                bounding_box_mm=meta.get("dimensions", bbox),
                features=[{"id": "body", "type": "body",
                           "label": str(meta.get("object_type", "part")), "anchor": [0, 0, 0]}],
            )
            log_event("compiler_ok", attempt=attempt, object_type=meta.get("object_type"),
                      triangles=mesh.triangle_count)
            return CompileOutput(
                ok=True, result=result, brief=brief, program=program, report=report,
                route="cadquery_program", repair_attempts=attempt,
                assumptions=list(brief.assumptions) + list(program.assumptions),
                export_formats=program.expected_exports or ["stl", "step"],
                code=program.generated_code,
                explanation=brief.mechanical_function or None,
            )
        feedback = (
            "The generated model failed these semantic checks: "
            + report.summary() + " Return a corrected program."
        )
        log_event("compiler_semantic_fail", attempt=attempt, detail=report.summary()[:200])

    # Exhausted repairs.
    return CompileOutput(
        ok=False, brief=last_brief, program=last_program, report=last_report,
        repair_attempts=max_repairs,
        clarification=(
            "I generated a draft but it didn't pass the semantic checks "
            + (last_report.summary() if last_report else "")
            + ". Could you add or clarify key dimensions/features?"
        ),
    )


def _hash(code: str) -> str:
    import hashlib

    return hashlib.sha256(code.encode()).hexdigest()[:16]
