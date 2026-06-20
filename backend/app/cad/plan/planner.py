"""CadPlan orchestration: prompt → plan → compile → validate → (repair once).

The LLM provider returns a raw CadPlan dict (data only, never code); we validate
it into a strict ``CadPlan`` and compile it deterministically. If the provider
declines or emits an invalid/empty plan, we fall back to the offline
deterministic planner so the pipeline always produces a result (or a precise
clarification) without depending on a live API.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from app.cad.plan.compiler import CadPlanResult, compile_cad_plan, export_solid
from app.cad.plan.schema import CadPlan
from app.cad.plan.validate import ValidationReport, validate
from app.export.exporter import PreviewMesh


@dataclass
class BuildOutcome:
    plan: CadPlan
    result: CadPlanResult
    stl_bytes: bytes
    step_bytes: bytes
    preview: PreviewMesh
    report: ValidationReport


def _coerce_plan(raw: dict | None) -> CadPlan | None:
    if not raw:
        return None
    try:
        return CadPlan(**raw)
    except ValidationError:
        return None


def plan_from_prompt(prompt: str, provider) -> CadPlan | None:
    """Validated provider plan, or None when the provider can't/shouldn't plan
    this part (the caller then falls back to the legacy pipeline)."""
    raw = None
    try:
        raw = provider.plan_cad(prompt)
    except NotImplementedError:
        raw = None
    plan = _coerce_plan(raw)
    if plan is not None and (plan.is_buildable() or plan.clarification_required):
        return plan
    return None


def repair_plan(prompt: str, previous: CadPlan, diagnostics: str, provider) -> CadPlan | None:
    """One repair pass: re-prompt the provider with the structured failures."""
    try:
        raw = provider.plan_cad(prompt, feedback=diagnostics)
    except (NotImplementedError, TypeError):
        raw = None
    return _coerce_plan(raw)


def build_and_validate(plan: CadPlan) -> BuildOutcome:
    """Compile a plan and validate the resulting geometry + exports."""
    result = compile_cad_plan(plan)
    stl, step, preview = export_solid(result.solid)
    report = validate(plan, result, stl, step)
    return BuildOutcome(plan, result, stl, step, preview, report)
