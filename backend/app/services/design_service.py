"""Orchestration: prompt -> spec -> geometry -> exports -> checks -> persistence.

Pure-ish functions over a DB session. This is where the safety pipeline is
enforced: nothing reaches CadQuery without passing DesignSpec validation, and
every export is verified non-empty by the storage layer.
"""
from __future__ import annotations

import re
import time

from sqlalchemy.orm import Session

from app.cad.base import CadGenerationError
from app.cad.complexity import assess_complexity
from app.cad.registry import get_template
from app.config import settings
from app.llm.budget import generation_budget
from app.explain import explain
from app.export.exporter import generate
from app.manufacturability.checks import run_checks
from app.models import Design, ExportFile, Feedback, ManufacturingCheck, Project
from app.observability import elapsed_ms, log_event
from app.parsing.modification_parser import parse_and_apply
from app.parsing.prompt_parser import parse_prompt
from app.schemas.design_spec import DesignSpec
from app.storage.storage import StorageError, get_storage


# User-safe message returned when a manufacturable export is blocked.
DOWNLOAD_BLOCKED_MESSAGE = (
    "This design failed validation and is not safe to export as a manufacturable file."
)


def validation_summary(design: Design) -> dict:
    """The dimension report's validation block ({status, critical_failures,
    warnings}), or {} when there's no report (older / non-CadPlan designs)."""
    report = (design.semantic_json or {}).get("dimension_report") or {}
    return report.get("validation") or {}


def validation_status(design: Design) -> str | None:
    return validation_summary(design).get("status")


def is_critical_failure(design: Design) -> bool:
    """True only when validation found a production-blocking failure. A None/
    missing status (no report) is NOT critical — those designs export as before."""
    return validation_status(design) == "critical_failure"


def recovery_info(design: Design) -> dict:
    return (design.semantic_json or {}).get("recovery") or {}


def user_owns_design(db: Session, design: Design, user_id: str) -> bool:
    project = db.get(Project, design.project_id)
    return project is not None and project.user_id == user_id


def _ensure_project(
    db: Session, project_id: str | None, name: str | None, user_id: str
) -> Project:
    if project_id:
        project = db.get(Project, project_id)
        # Only reuse a project the caller actually owns.
        if project is not None and project.user_id == user_id:
            return project
    project = Project(name=name or "Untitled part", user_id=user_id)
    db.add(project)
    db.flush()
    return project


def _editable_parameters(spec: DesignSpec) -> dict[str, float]:
    """Template defaults overlaid with the spec's current (mm) dimensions."""
    if spec.object_type == "feature_graph":
        return {}  # feature-graph parts have no editable template parameters
    template = get_template(spec.object_type)
    params = template.default_dimensions()
    for key, value in spec.dimensions.items():
        params[key] = spec.to_mm(value)
    return params


def _regenerate_geometry(db: Session, design: Design, spec: DesignSpec) -> None:
    """Build geometry, refresh preview/exports/checks on the design row."""
    start = time.perf_counter()
    try:
        result = generate(spec)
    except CadGenerationError:
        log_event(
            "cad_generation_failed",
            design_id=design.id,
            object_type=spec.object_type,
            provider=settings.llm_provider,
        )
        raise
    storage = get_storage()

    design.object_type = spec.object_type
    design.spec_json = spec.model_dump(mode="json")
    design.spec_hash = result.spec_hash
    design.explanation = explain(spec)
    design.bounding_box = result.bounding_box_mm
    design.provider = settings.llm_provider
    design.generation_ms = int(elapsed_ms(start))
    design.preview_json = {
        "positions": result.preview.positions,
        "indices": result.preview.indices,
        "vertex_count": result.preview.vertex_count,
        "triangle_count": result.preview.triangle_count,
    }
    design.features_json = result.features

    # Requested-vs-generated dimension report + 3D-print readiness (BRep + mesh
    # ground truth). Advisory: it never blocks a built model, only informs.
    from app.cad.plan.dimension_report import build_spec_report

    smallest_hole = min((spec.to_mm(h.diameter) for h in spec.holes), default=None)
    design.semantic_json = {
        "dimension_report": build_spec_report(
            requested_dimensions_mm={k: spec.to_mm(v) for k, v in spec.dimensions.items()},
            bbox_mm=result.bounding_box_mm,
            volume_mm3=result.volume_mm3,
            surface_area_mm2=result.surface_area_mm2,
            hole_count=len(spec.holes),
            smallest_hole_mm=smallest_hole,
            stl_bytes=result.stl_bytes,
        )
    }

    # Replace exports. Storage is owner-scoped by the design id prefix.
    for old in list(design.exports):
        db.delete(old)
    db.flush()
    for fmt, data in (("stl", result.stl_bytes), ("step", result.step_bytes)):
        key = f"{design.id}/{result.spec_hash}.{fmt}"
        try:
            storage.save(key, data)
        except StorageError:
            log_event("export_failed", design_id=design.id, fmt=fmt)
            raise
        db.add(
            ExportFile(
                design_id=design.id,
                fmt=fmt,
                storage_key=key,
                # Download goes through an owner-checked API route, never a
                # public path, so private files stay private.
                url=f"{settings.public_base_url}/api/designs/{design.id}/files/{fmt}",
                size_bytes=len(data),
            )
        )
    log_event(
        "geometry_generated",
        design_id=design.id,
        object_type=spec.object_type,
        provider=settings.llm_provider,
        triangle_count=result.preview.triangle_count,
        generation_ms=design.generation_ms,
    )

    # Replace checks.
    for old in list(design.checks):
        db.delete(old)
    db.flush()
    for c in run_checks(spec):
        db.add(
            ManufacturingCheck(
                design_id=design.id,
                check=c.check,
                severity=c.severity.value,
                passed=c.passed,
                message=c.message,
            )
        )


def _plan_long_prompt(prompt: str) -> "ParseResult":
    """Route a long/complex prompt through ComplexCADPlan -> ParseResult."""
    from app.parsing.complex_plan import build_complex_plan
    from app.schemas.design_spec import ParseResult

    plan = build_complex_plan(prompt)
    notes = []
    if plan.materials:
        notes.append("Materials: " + ", ".join(plan.materials))
    if plan.visual_notes:
        notes.append("Visual/style (not applied to geometry): " + ", ".join(plan.visual_notes))
    if plan.unsupported_features:
        notes.append("Unsupported features ignored: " + ", ".join(plan.unsupported_features))

    if plan.template_object_type:
        try:
            spec = DesignSpec(
                object_type=plan.template_object_type,
                units="mm",
                dimensions={k: float(v) for k, v in plan.template_dimensions.items() if v > 0},
            )
        except Exception:  # noqa: BLE001 - fall back to a clarification
            return ParseResult(
                clarification_question="I couldn't form a valid part from that prompt; "
                "please restate the key dimensions.",
                assumptions=notes,
            )
        return ParseResult(spec=spec, assumptions=notes or ["Routed via complex-CAD plan"])

    # Unsupported template -> try the safe feature-graph fallback before asking.
    from app.parsing.complex_plan import plan_prompt

    fallback = plan_prompt(prompt)
    if fallback.spec is not None:
        fallback.assumptions = list(fallback.assumptions) + notes
        return fallback

    return ParseResult(
        clarification_question=plan.clarification_question
        or "I couldn't map this complex prompt to a supported part. Could you clarify?",
        assumptions=notes,
    )


def _plan_explanation(plan, result) -> str:
    bits = [f"{plan.name} ({plan.object_type.replace('_', ' ')})."]
    bb = result.bbox_mm
    bits.append(f"Envelope ~{bb['x']}×{bb['y']}×{bb['z']} mm.")
    if result.hole_count:
        bits.append(f"{result.hole_count} hole(s), {result.through_hole_count} through.")
    if plan.assumptions:
        bits.append("Assumptions: " + "; ".join(plan.assumptions[:3]) + ".")
    return " ".join(bits)


def _outcome_status(outcome) -> str | None:
    """validation_status of a freshly-built outcome (before persistence)."""
    report = getattr(outcome.report, "dimension_report", None) or {}
    return (report.get("validation") or {}).get("status")


def _outcome_criticals(outcome) -> list[str]:
    report = getattr(outcome.report, "dimension_report", None) or {}
    return (report.get("validation") or {}).get("critical_failures") or []


def _attempt_recovery(prompt: str, plan, outcome, provider):
    """Try to turn a critical_failure build into a non-critical one, ONCE.

    Strategies, in order: (1) LLM ``repair`` re-prompted with the exact critical
    diagnostics; (2) the offline deterministic planner as a fallback route. The
    first candidate that compiles, exports, and is no longer critical wins.

    Returns ``(plan, outcome, strategy, succeeded)`` — the originals unchanged
    when nothing improved (with ``strategy`` = the first thing we tried)."""
    from app.cad.base import CadGenerationError
    from app.cad.plan import deterministic
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate, repair_plan

    diag = (
        "The compiled model FAILED critical validation and must be rebuilt: "
        + "; ".join(_outcome_criticals(outcome))
        + ". Produce a single fused solid (no disconnected bodies), keep the "
        "requested overall dimensions, and cut every requested hole through."
    )
    candidates: list[tuple[str, object]] = []
    repaired = repair_plan(prompt, plan, diag, provider)
    if repaired is not None and not repaired.clarification_required and repaired.features:
        candidates.append(("repair", normalize_cad_plan(repaired, prompt)))
    det = deterministic.plan(prompt)
    if det is not None and not det.clarification_required and det.features:
        det_norm = normalize_cad_plan(det, prompt)
        if det_norm.model_dump(mode="json") != plan.model_dump(mode="json"):
            candidates.append(("deterministic_fallback", det_norm))

    for strategy, cand in candidates:
        try:
            retry = build_and_validate(cand)
        except CadGenerationError:
            continue
        if retry.report.passed and _outcome_status(retry) != "critical_failure":
            return cand, retry, strategy, True

    return plan, outcome, (candidates[0][0] if candidates else None), False


def _try_deterministic_part(db: Session, design: Design, prompt: str,
                            parse_start: float) -> Design | None:
    """Build a dedicated hard single part (robotic arm base, U bracket, hinge,
    clamp block) straight from the OFFLINE deterministic planner — no LLM call,
    so it can never time out behind a provider. Returns the finished design, or
    None to fall back to the normal pipeline if the deterministic plan declines
    or the build doesn't validate cleanly."""
    from app.cad.base import CadGenerationError
    from app.cad.plan import deterministic
    from app.cad.plan.audit import audit_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate

    plan = deterministic.plan(prompt)
    if plan is None or plan.clarification_required or not plan.features:
        return None
    plan = normalize_cad_plan(plan, prompt)
    try:
        outcome = build_and_validate(plan)
    except CadGenerationError:
        return None
    if outcome is None or not outcome.report.passed:
        return None
    audit = audit_plan(prompt, plan, outcome.result)
    recovery = {"attempted": False, "strategy": None, "succeeded": False}
    _store_plan(db, design, plan, outcome, repair_attempts=0, audit=audit, recovery=recovery)
    log_event("prompt_parsed", design_id=design.id, provider="deterministic",
              routed="deterministic_part", produced_spec=True,
              validated=outcome.report.passed,
              validation_status=_outcome_status(outcome),
              latency_ms=elapsed_ms(parse_start))
    db.commit()
    db.refresh(design)
    return design


def _try_cad_plan(db: Session, design: Design, prompt: str, parse_start: float) -> Design | None:
    """Primary route: plain English -> CadPlan -> deterministic CadQuery compile
    -> validate -> (one repair pass). Returns the finished/clarification design,
    or None to fall back to the legacy template pipeline if nothing builds."""
    from app.cad.base import CadGenerationError
    from app.cad.plan.audit import audit_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate, plan_from_prompt, repair_plan
    from app.cad.plan.policy import decide_clarification
    from app.llm.factory import get_cad_provider

    provider = get_cad_provider()
    plan = plan_from_prompt(prompt, provider)
    if plan is None:
        return None  # feature-graph planner didn't handle this -> legacy fallback

    # ASSUMPTION-FIRST: only a FATAL decision (impossible primary geometry/scale)
    # blocks generation. An LLM that over-eagerly asked for secondary dimensions
    # is downgraded to a warning and we generate anyway.
    decision = decide_clarification(plan, prompt)
    if decision.severity == "fatal":
        design.object_type = plan.object_type
        design.route = "cad_plan"
        design.route_reason = "Primary shape or scale can't be inferred."
        design.assumptions = plan.assumptions
        design.missing_required = decision.questions
        design.clarification_question = (
            " ".join(decision.questions) or "Could you describe the part and its main size?"
        )
        log_event("prompt_parsed", design_id=design.id, provider=provider.name,
                  routed="cad_plan", produced_spec=False, latency_ms=elapsed_ms(parse_start))
        db.commit()
        db.refresh(design)
        return design

    if not plan.features:
        return None  # nothing to build -> legacy fallback

    plan = normalize_cad_plan(plan, prompt)  # fill secondary dims + intent counts

    repair_attempts = 0
    outcome = None
    try:
        outcome = build_and_validate(plan)
    except CadGenerationError as exc:
        diagnostics = f"compile error: {exc}"

    # Repair ONLY on a FATAL failure (compile error / empty / missing exports).
    # Non-fatal warnings never trigger a repair — the model is already shippable.
    if outcome is None or not outcome.report.passed:
        diag = diagnostics if outcome is None else outcome.report.diagnostics()
        repaired = repair_plan(prompt, plan, diag or "compile failed", provider)
        if repaired is not None and not repaired.clarification_required and repaired.features:
            repaired = normalize_cad_plan(repaired, prompt)
            try:
                retry = build_and_validate(repaired)
            except CadGenerationError:
                retry = None
            if retry is not None and (outcome is None or retry.report.passed):
                outcome, plan, repair_attempts = retry, repaired, 1

    if outcome is None:
        return None  # could not build any geometry at all -> legacy fallback

    # FEATURE-LEVEL AUDIT: does the compiled model contain the mechanical
    # features the prompt asked for (tube bore, boss, no center bore, ...)?
    # A failed audit triggers ONE repair pass; if the repair doesn't improve
    # things the model still ships, with the failures surfaced as warnings.
    audit = audit_plan(prompt, plan, outcome.result)
    if not audit.passed and repair_attempts == 0:
        missing = "; ".join(
            f"{i.feature_id}: {i.requirement} ({i.detail})" for i in audit.failures())
        repaired = repair_plan(
            prompt, plan,
            "The model compiled but is missing requested mechanical features — "
            f"add them to the feature graph: {missing}", provider)
        if repaired is not None and not repaired.clarification_required and repaired.features:
            repaired = normalize_cad_plan(repaired, prompt)
            try:
                retry = build_and_validate(repaired)
            except CadGenerationError:
                retry = None
            if retry is not None and retry.report.passed:
                retry_audit = audit_plan(prompt, repaired, retry.result)
                if retry_audit.passed:
                    outcome, plan, audit, repair_attempts = retry, repaired, retry_audit, 1

    # CRITICAL-FAILURE RECOVERY: a model that compiled+exported but failed
    # critical validation (disconnected bodies, dimension drift, missing/through
    # holes, non-watertight/manifold, zero volume) is NOT a usable result. Try
    # ONE automatic recovery (repair, then deterministic fallback) and re-validate
    # before finalizing. We always record what was attempted; the design is still
    # stored (inspectable) even if recovery fails — the export route blocks it.
    recovery = {"attempted": False, "strategy": None, "succeeded": False}
    if _outcome_status(outcome) == "critical_failure":
        recovery["attempted"] = True
        new_plan, new_outcome, strategy, ok = _attempt_recovery(prompt, plan, outcome, provider)
        recovery["strategy"] = strategy
        recovery["succeeded"] = ok
        log_event("critical_recovery", design_id=design.id, strategy=strategy,
                  succeeded=ok, criticals=len(_outcome_criticals(outcome)))
        if ok:
            outcome, plan = new_outcome, new_plan
            repair_attempts += 1
            audit = audit_plan(prompt, plan, outcome.result)

    _store_plan(db, design, plan, outcome, repair_attempts, audit, recovery=recovery)
    log_event("prompt_parsed", design_id=design.id, provider=provider.name,
              routed="cad_plan", produced_spec=True,
              validated=outcome.report.passed,
              validation_status=_outcome_status(outcome),
              latency_ms=elapsed_ms(parse_start))
    db.commit()
    db.refresh(design)
    return design


def _store_plan(db: Session, design: Design, plan, outcome, repair_attempts: int,
                audit=None, recovery: dict | None = None) -> None:
    """Persist a CadPlan-built design: exports, preview, validation, assumptions."""
    import hashlib
    import json as _json

    result = outcome.result
    storage = get_storage()
    plan_json = plan.model_dump(mode="json")
    digest = hashlib.sha256(
        _json.dumps(plan_json, sort_keys=True).encode()
    ).hexdigest()[:16]

    export_formats = plan.expected.export_formats or ["step", "stl"]
    design.object_type = plan.object_type
    design.spec_json = None  # CadPlan-built, not a DesignSpec
    design.spec_hash = digest
    design.explanation = _plan_explanation(plan, result)
    design.bounding_box = result.bbox_mm
    design.provider = settings.cad_llm_provider or settings.llm_provider
    design.route = "cad_plan"
    design.route_reason = "Compiled from a parametric CAD feature graph."
    # assumptions = inferred values + compiler notes. Advisory VALIDATION
    # warnings live in semantic_json and are surfaced via the DTO `warnings`
    # field — they never block a compiled model.
    design.assumptions = list(plan.assumptions) + [f"Note: {w}" for w in result.warnings]
    design.auto_repaired = repair_attempts > 0
    design.repair_attempts = repair_attempts
    design.export_formats = export_formats
    semantic = outcome.report.to_semantic_json()
    if audit is not None:
        semantic["feature_audit"] = audit.to_json()
        # Failed audit items surface as non-blocking warnings in the UI.
        semantic["checks"].extend(
            {"name": f"feature_audit_{i.feature_id}", "passed": i.satisfied,
             "expected": i.requirement, "actual": i.detail, "severity": "warning"}
            for i in audit.items if not i.satisfied
        )
    semantic["recovery"] = recovery or {
        "attempted": False, "strategy": None, "succeeded": False
    }
    design.semantic_json = semantic
    design.features_json = result.feature_meta
    design.missing_required = []
    # A compiled, exported model is NEVER "needs clarification". Warnings are
    # shown alongside the model + downloads.
    design.clarification_question = None
    design.preview_json = {
        "positions": outcome.preview.positions,
        "indices": outcome.preview.indices,
        "vertex_count": outcome.preview.vertex_count,
        "triangle_count": outcome.preview.triangle_count,
    }

    for old in list(design.exports):
        db.delete(old)
    db.flush()
    fmt_bytes = {"stl": outcome.stl_bytes, "step": outcome.step_bytes}
    for fmt in export_formats:
        data = fmt_bytes.get(fmt)
        if not data:
            continue
        key = f"{design.id}/{digest}.{fmt}"
        try:
            storage.save(key, data)
        except StorageError:
            log_event("export_failed", design_id=design.id, fmt=fmt)
            raise
        db.add(ExportFile(
            design_id=design.id, fmt=fmt, storage_key=key,
            url=f"{settings.public_base_url}/api/designs/{design.id}/files/{fmt}",
            size_bytes=len(data),
        ))

    # Surface the validation report as manufacturing-style checks.
    for old in list(design.checks):
        db.delete(old)
    db.flush()
    for c in outcome.report.checks:
        sev = "info" if c.passed else c.severity
        msg = c.name.replace("_", " ")
        if c.expected is not None:
            msg += f": expected {c.expected}, got {c.actual}"
        db.add(ManufacturingCheck(
            design_id=design.id, check=c.name, severity=sev, passed=c.passed, message=msg
        ))
    for i in (audit.items if audit is not None else []):
        db.add(ManufacturingCheck(
            design_id=design.id, check=f"feature_audit_{i.feature_id}",
            severity="info" if i.satisfied else "warning", passed=i.satisfied,
            message=f"{i.requirement} — {i.detail}",
        ))
    log_event("geometry_generated", design_id=design.id, object_type=design.object_type,
              provider=design.provider, route="cad_plan",
              triangle_count=outcome.preview.triangle_count)


def rebuild_design_from_plan(db: Session, design: Design, plan, prompt: str) -> bool:
    """Rebuild an existing design row from a deterministic CadPlan (the
    drawing-mode fallback path). Returns True when the rebuilt model compiles,
    exports, and passes the feature audit."""
    from app.cad.base import CadGenerationError
    from app.cad.plan.audit import audit_plan
    from app.cad.plan.normalize import normalize_cad_plan
    from app.cad.plan.planner import build_and_validate

    plan = normalize_cad_plan(plan, prompt)
    try:
        outcome = build_and_validate(plan)
    except CadGenerationError:
        return False
    if not outcome.report.passed:
        return False
    audit = audit_plan(prompt, plan, outcome.result)
    _store_plan(db, design, plan, outcome,
                repair_attempts=int(design.repair_attempts or 0) + 1, audit=audit)
    db.commit()
    db.refresh(design)
    return audit.passed


def _try_compiler(db: Session, design: Design, prompt: str, parse_start: float) -> Design | None:
    """Run the CAD compiler if it has a program for this prompt. Returns the
    finished design on success/clarification, or None to fall back to planning."""
    from app.generation.cad_programs import generate_program
    from app.generation.compiler import compile_prompt
    from app.llm.factory import get_provider

    if generate_program(prompt) is None:
        return None  # no compiler family -> fall back to templates/feature-graph
    out = compile_prompt(prompt, get_provider())
    if out is None:
        return None
    log_event("prompt_parsed", design_id=design.id, provider=settings.llm_provider,
              routed="cadquery_program", produced_spec=out.ok,
              latency_ms=elapsed_ms(parse_start))
    if out.ok:
        _store_program(db, design, out)
        db.commit()
        db.refresh(design)
        return design
    # Compiler ran but the model failed semantic checks after repairs.
    design.route = "cadquery_program"
    design.assumptions = out.assumptions
    design.semantic_json = out.report.model_dump() if out.report else None
    design.repair_attempts = out.repair_attempts
    design.clarification_question = out.clarification
    db.commit()
    db.refresh(design)
    return design


def _store_program(db: Session, design: Design, out) -> None:
    """Persist a sandbox-generated program design (geometry from STL/STEP bytes)."""
    result = out.result
    storage = get_storage()
    design.object_type = out.brief.object_type
    design.spec_json = None
    design.spec_hash = result.spec_hash
    design.explanation = out.explanation or (out.brief.mechanical_function or None)
    design.bounding_box = result.bounding_box_mm
    design.provider = settings.llm_provider
    design.route = "cadquery_program"
    design.route_reason = "Sandboxed CadQuery program, semantically verified."
    design.assumptions = out.assumptions
    design.auto_repaired = out.repair_attempts > 0
    design.repair_attempts = out.repair_attempts
    design.export_formats = out.export_formats
    design.program_code = out.code
    design.semantic_json = out.report.model_dump() if out.report else None
    design.features_json = result.features
    design.clarification_question = None
    design.preview_json = {
        "positions": result.preview.positions,
        "indices": result.preview.indices,
        "vertex_count": result.preview.vertex_count,
        "triangle_count": result.preview.triangle_count,
    }
    for old in list(design.exports):
        db.delete(old)
    db.flush()
    fmts = [("stl", result.stl_bytes)]
    if "step" in out.export_formats and result.step_bytes:
        fmts.append(("step", result.step_bytes))
    for fmt, data in fmts:
        key = f"{design.id}/{result.spec_hash}.{fmt}"
        storage.save(key, data)
        db.add(ExportFile(
            design_id=design.id, fmt=fmt, storage_key=key,
            url=f"{settings.public_base_url}/api/designs/{design.id}/files/{fmt}",
            size_bytes=len(data),
        ))
    log_event("geometry_generated", design_id=design.id, object_type=design.object_type,
              provider=settings.llm_provider, route="cadquery_program",
              triangle_count=result.preview.triangle_count)


def _try_assembly(db: Session, design: Design, prompt: str) -> Design:
    """Generate a simplified CONCEPT assembly for a supported complex family
    (tubular chassis / space frame). Deterministic CadQuery — no LLM."""
    from app.cad.assembly.chassis import build_chassis
    from app.cad.assembly.report import build_assembly_report
    from app.cad.plan.compiler import export_solid

    build = build_chassis(prompt)
    stl_bytes, step_bytes, preview = export_solid(build.solid)
    report = build_assembly_report(build, stl_bytes, step_bytes)
    _store_assembly(db, design, build, stl_bytes, step_bytes, preview, report)
    log_event("assembly_generated", design_id=design.id, family="tubular_chassis",
              components=len(build.components), tubes=build.tube_count,
              status=report["validation"]["status"])
    db.commit()
    db.refresh(design)
    return design


def _store_assembly(db: Session, design: Design, build, stl_bytes: bytes,
                    step_bytes: bytes, preview, report: dict) -> None:
    """Persist a concept-assembly design: exports, preview, validation, components."""
    import hashlib
    import json as _json

    storage = get_storage()
    digest = hashlib.sha256(
        _json.dumps(report.get("components"), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    env = build.envelope_mm
    val = report["validation"]

    design.object_type = "tubular_chassis_assembly"
    design.spec_json = None
    design.spec_hash = digest
    design.route = "assembly"
    design.route_reason = "Detailed concept assembly (tubular chassis / space frame)."
    design.bounding_box = report["measured"]["bbox_mm"]
    design.provider = settings.cad_llm_provider or settings.llm_provider
    design.explanation = (
        f"Detailed concept chassis — welded tubular space frame, "
        f"~{env['x']:g}×{env['y']:g}×{env['z']:g} mm, {build.tube_count} tubes, "
        f"{len(build.components)} named components across front / engine bay / cabin / "
        f"roll cage / rear zones. Concept CAD — not structurally certified."
    )
    design.assumptions = [
        f"Target envelope {env['x']:g}×{env['y']:g}×{env['z']:g} mm",
        f"Round tubes Ø{build.tube_od:g}mm, {build.tube_wall:g}mm wall "
        "(exported as solid cylinders; wall carried as cut-list metadata)",
        "Detailed concept assembly — not a certified or FEA-analyzed structural design",
    ]
    design.missing_required = []
    design.clarification_question = None
    design.auto_repaired = False
    design.repair_attempts = 0
    design.export_formats = ["step", "stl"]
    design.features_json = []  # an assembly is not single-feature editable
    design.semantic_json = {
        "design_mode": "assembly",
        "dimension_report": report,
        "checks": [],  # detail lives in dimension_report + ManufacturingCheck rows
        "passed": val["status"] != "critical_failure",
    }
    design.preview_json = {
        "positions": preview.positions,
        "indices": preview.indices,
        "vertex_count": preview.vertex_count,
        "triangle_count": preview.triangle_count,
    }

    for old in list(design.exports):
        db.delete(old)
    db.flush()
    for fmt, data in (("stl", stl_bytes), ("step", step_bytes)):
        if not data:
            continue
        key = f"{design.id}/{digest}.{fmt}"
        try:
            storage.save(key, data)
        except StorageError:
            log_event("export_failed", design_id=design.id, fmt=fmt)
            raise
        db.add(ExportFile(
            design_id=design.id, fmt=fmt, storage_key=key,
            url=f"{settings.public_base_url}/api/designs/{design.id}/files/{fmt}",
            size_bytes=len(data),
        ))

    for old in list(design.checks):
        db.delete(old)
    db.flush()
    db.add(ManufacturingCheck(
        design_id=design.id, check="validation_profile", severity="info",
        passed=True, message="Validation profile: Assembly (concept model)."))
    for msg in val["critical_failures"]:
        db.add(ManufacturingCheck(
            design_id=design.id, check="assembly_critical", severity="critical",
            passed=False, message=msg))
    for msg in val["warnings"]:
        db.add(ManufacturingCheck(
            design_id=design.id, check="assembly_warning", severity="warning",
            passed=False, message=msg))


def _try_frame_family(db: Session, design: Design, prompt: str, family_id: str) -> Design:
    """Generate a supported deterministic frame / concept assembly (or the
    primary component of one). Deterministic CadQuery — no LLM."""
    from app.cad.assembly.frame_report import build_frame_report
    from app.cad.assembly.frames import build_frame_family
    from app.cad.plan.compiler import export_solid

    build = build_frame_family(prompt, family_id)
    stl_bytes, step_bytes, preview = export_solid(build.solid)
    report = build_frame_report(build, stl_bytes, step_bytes)
    _store_frame_build(db, design, build, stl_bytes, step_bytes, preview, report)
    log_event("assembly_generated", design_id=design.id, family=family_id,
              components=build.member_count, design_mode=build.design_mode,
              status=report["validation"]["status"])
    db.commit()
    db.refresh(design)
    return design


def _store_frame_build(db: Session, design: Design, build, stl_bytes: bytes,
                       step_bytes: bytes, preview, report: dict) -> None:
    """Persist a frame / concept-assembly build: exports, preview, validation,
    components. Mirrors _store_assembly but driven by the generic frame report."""
    import hashlib
    import json as _json

    storage = get_storage()
    digest = hashlib.sha256(
        _json.dumps(report.get("components"), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    env = build.envelope_mm
    val = report["validation"]

    design.object_type = build.family_id
    design.spec_json = None
    design.spec_hash = digest
    design.route = "assembly"
    design.route_reason = f"Deterministic concept assembly ({build.display_name})."
    design.bounding_box = report["measured"]["bbox_mm"]
    design.provider = settings.cad_llm_provider or settings.llm_provider
    env_txt = (f"~{env.get('x', 0):g}×{env.get('y', 0):g}×{env.get('z', 0):g} mm "
               if env else "")
    design.explanation = (
        f"{build.display_name} — concept assembly, {env_txt}"
        f"{build.member_count} components. Concept CAD — not structurally certified."
    )
    assumptions = list(build.notes)
    if build.decomposition_note:
        assumptions.append(build.decomposition_note)
    design.assumptions = assumptions
    design.missing_required = []
    design.clarification_question = None
    design.auto_repaired = False
    design.repair_attempts = 0
    design.export_formats = ["step", "stl"]
    design.features_json = []  # an assembly is not single-feature editable
    design.semantic_json = {
        "design_mode": build.design_mode,
        "dimension_report": report,
        "checks": [],  # detail lives in dimension_report + ManufacturingCheck rows
        "passed": val["status"] != "critical_failure",
    }
    design.preview_json = {
        "positions": preview.positions,
        "indices": preview.indices,
        "vertex_count": preview.vertex_count,
        "triangle_count": preview.triangle_count,
    }

    for old in list(design.exports):
        db.delete(old)
    db.flush()
    for fmt, data in (("stl", stl_bytes), ("step", step_bytes)):
        if not data:
            continue
        key = f"{design.id}/{digest}.{fmt}"
        try:
            storage.save(key, data)
        except StorageError:
            log_event("export_failed", design_id=design.id, fmt=fmt)
            raise
        db.add(ExportFile(
            design_id=design.id, fmt=fmt, storage_key=key,
            url=f"{settings.public_base_url}/api/designs/{design.id}/files/{fmt}",
            size_bytes=len(data),
        ))

    for old in list(design.checks):
        db.delete(old)
    db.flush()
    db.add(ManufacturingCheck(
        design_id=design.id, check="validation_profile", severity="info", passed=True,
        message=f"Validation profile: {report.get('profile_label', build.profile)}."))
    for msg in val["critical_failures"]:
        db.add(ManufacturingCheck(
            design_id=design.id, check="assembly_critical", severity="critical",
            passed=False, message=msg))
    for msg in val["warnings"]:
        db.add(ManufacturingCheck(
            design_id=design.id, check="assembly_warning", severity="warning",
            passed=False, message=msg))


def _store_decomposition(db: Session, design: Design, assessment) -> None:
    """Persist a 'this is a large assembly — decompose it' result (no geometry)."""
    design.object_type = "assembly"
    design.route = "needs_decomposition"
    design.route_reason = "Large multi-part assembly — generate one component at a time."
    design.explanation = assessment.reason
    design.assumptions = []
    design.missing_required = []
    design.clarification_question = None
    design.spec_json = None
    design.semantic_json = {"decomposition": assessment.to_dict()}
    db.commit()
    db.refresh(design)


def _attach_classification(db: Session, design: Design, classification) -> None:
    """Merge the structured prompt classification into the design's semantic_json
    (advisory metadata; never blocks or changes geometry). Committed in place."""
    semantic = dict(design.semantic_json or {})
    semantic["classification"] = classification.to_dict()
    design.semantic_json = semantic
    db.add(design)
    db.commit()
    db.refresh(design)


def create_design(
    db: Session,
    prompt: str,
    project_id: str | None,
    name: str | None,
    user_id: str,
) -> Design:
    project = _ensure_project(db, project_id, name, user_id)
    design = Design(project_id=project.id, prompt=prompt)
    db.add(design)
    # Persist the placeholder and COMMIT immediately, so we never hold a write
    # transaction (and its SQLite write lock) open across the slow LLM / CadQuery
    # work below. This is what prevents "database is locked" under concurrent or
    # duplicate submits — each request locks only briefly to write, not for the
    # whole multi-second generation.
    db.commit()
    db.refresh(design)

    # STRUCTURED CLASSIFICATION (cheap, offline): record what family/strategy the
    # prompt maps to BEFORE generation. Stored as advisory metadata and surfaced
    # in the API; it never blocks or alters the geometry pipeline below.
    from app.cad.classification import classify_prompt

    classification = classify_prompt(prompt)

    design = _dispatch_generation(db, design, prompt, classification)
    _attach_classification(db, design, classification)
    return design


def _dispatch_generation(db: Session, design: Design, prompt: str,
                         classification=None) -> Design:
    # DETERMINISTIC-FIRST HARD-PROMPT ROUTER. Supported families are built
    # offline (no OpenAI call, so they can't time out). Order: structural-frame /
    # concept assemblies, then a vague-prompt clarification gate, then the
    # large-assembly decomposition gate, then deterministic single parts.
    # Anything left falls through to the LLM/CadPlan pipeline.
    from app.cad.assembly.frames import detect_fallback_directive, detect_frame_family

    # 1) Frame / concept-assembly families (machine frame, CNC router, engine
    #    test stand, drone, motorcycle subframe, skateboard motor mount).
    frame_family = detect_frame_family(prompt)
    if frame_family:
        try:
            return _try_frame_family(db, design, prompt, frame_family)
        except CadGenerationError as exc:
            log_event("frame_generation_failed", design_id=design.id,
                      family=frame_family, detail=str(exc)[:200])
            # fall through to the complexity gate / generation below.

    parse_start = time.perf_counter()

    # 2) VAGUE PROMPT GATE: a part named with no type and no dimensions ("make a
    #    bracket") should ask for clarification rather than emit a failed/guessed
    #    model. Cheap string check; never blocks a prompt that has real detail.
    vague = _vague_clarification(prompt)
    if vague is not None:
        return _store_clarification(db, design, vague, parse_start)

    # 2b) UNSUPPORTED EVERYDAY OBJECT: common objects we don't have a deterministic
    #     family for (hammer, wrench, pliers, ...) commonly produce disconnected /
    #     garbage geometry via freeform CAD. When the classifier didn't map the
    #     prompt to a specific family, ask for clarification instead of failing.
    everyday = _everyday_object_clarification(prompt, classification)
    if everyday is not None:
        return _store_clarification(db, design, everyday, parse_start)

    # COMPLEXITY GATE (cheap, no LLM/CAD): whole machines / large multi-subsystem
    # assemblies (car chassis, airframe, jet engine, EV platform, ...) decompose
    # fast instead of being attempted as one synchronous part (or misrouted).
    assessment = assess_complexity(prompt)
    if assessment.is_complex:
        # Supported assembly families get a simplified CONCEPT model instead of
        # a bare decomposition prompt. Deterministic + fast (no LLM). If the
        # build fails for any reason, fall back to decomposition guidance.
        if assessment.supported_family == "tubular_chassis":
            try:
                return _try_assembly(db, design, prompt)
            except CadGenerationError as exc:
                log_event("assembly_generation_failed", design_id=design.id,
                          detail=str(exc)[:200])
        # EXPLICIT FALLBACK COMPONENT: when the prompt grants permission ("if too
        # complex, generate the <X> first"), build that primary component instead
        # of returning generic decomposition.
        fallback = detect_fallback_directive(prompt)
        if fallback:
            try:
                return _try_frame_family(db, design, prompt, fallback)
            except CadGenerationError as exc:
                log_event("fallback_generation_failed", design_id=design.id,
                          family=fallback, detail=str(exc)[:200])
        _store_decomposition(db, design, assessment)
        log_event("prompt_decomposition_required", design_id=design.id,
                  subsystems=len(assessment.subsystems))
        return design

    # 3) DETERMINISTIC-FIRST for any specific supported single-part family. The
    #    offline planner is fast and reliable, so these families build without an
    #    OpenAI call and can never time out. Scoped to a curated allowlist (and
    #    only the feature-graph engine) so families with dedicated drawing-mode /
    #    edit / rescue flows keep using the normal pipeline.
    fam_id = getattr(classification, "family_id", None)
    if settings.cad_engine == "feature_graph" and fam_id in DETERMINISTIC_FIRST_FAMILIES:
        built = _try_deterministic_part(db, design, prompt, parse_start)
        if built is not None:
            return built
        # else: deterministic planner declined -> LLM pipeline below.

    # Bound the TOTAL generation time (all model fallbacks + repair passes) so a
    # request can never hang for minutes; exceeding it raises LLMUnavailableError
    # which the API surfaces as a clean 503.
    with generation_budget(settings.cad_generation_timeout_seconds):
        return _run_generation(db, design, prompt, parse_start)


# Single-part families with a robust deterministic builder that we route OFFLINE
# first (no OpenAI call → can't time out). Deliberately curated: families with
# dedicated drawing-mode/edit/rescue behaviour (pipe fittings, enclosures, NEMA
# plates, flanges, …) are NOT here so those flows are preserved.
DETERMINISTIC_FIRST_FAMILIES = frozenset({
    "l_bracket", "u_bracket", "hinge_bracket", "clamp_block",
    "robotic_arm_base_bracket", "screwdriver",
})


# Vague-prompt detection: a generic part word with no type and no dimension.
_VAGUE_BRACKET_RE = re.compile(r"\bbracket\b", re.I)
_BRACKET_TYPE_RE = re.compile(
    r"\b(l|u|hinge|angle|flat|corner|gusset|shelf|mounting)[- ]?bracket\b", re.I)


def _vague_clarification(prompt: str) -> dict | None:
    """Return clarification questions for a too-vague structural prompt, else
    None. Conservative: only fires when there is NO dimension/number AND no
    specific bracket type — so 'make a bracket' clarifies but 'an L bracket with
    60mm legs' generates with defaults as before."""
    t = (prompt or "").strip().lower()
    if not t or re.search(r"\d", t):  # any number -> has real detail
        return None
    if _VAGUE_BRACKET_RE.search(t) and not _BRACKET_TYPE_RE.search(t) \
            and "hinge" not in t and "gusset" not in t:
        return {
            "question": (
                "I can build that, but I need a bit more detail so I don't guess "
                "wrong: (1) Bracket type — L, U, flat, hinge, or angle? "
                "(2) Overall size — length × width × thickness in mm? "
                "(3) How many mounting holes, and what diameter?"
            ),
            "questions": [
                "Bracket type: L, U, flat, hinge, or angle?",
                "Overall dimensions (length × width × thickness in mm)?",
                "How many mounting holes, and what diameter?",
            ],
        }
    return None


# Common everyday objects we do NOT have a deterministic family for. Freeform CAD
# for these frequently yields disconnected/garbage geometry, so we clarify rather
# than emit a failed model. (Supported everyday objects — screwdriver, knob — map
# to a specific family and never reach this gate.)
_UNSUPPORTED_EVERYDAY = (
    "hammer", "mallet", "wrench", "spanner", "pliers", "plier", "saw",
    "axe", "hatchet", "chisel", "crowbar", "shovel", "rake", "scissors",
    "drill", "screw gun", "ratchet", "socket wrench",
)


def _everyday_object_clarification(prompt: str, classification) -> dict | None:
    """Clarification for an unsupported everyday object, else None. Only fires
    when the classifier did NOT map the prompt to a specific family (so supported
    objects and detailed mechanical parts are unaffected)."""
    from app.cad.families import GENERIC_PART_FAMILY

    fam_id = getattr(classification, "family_id", None)
    if fam_id and fam_id != GENERIC_PART_FAMILY:
        return None  # mapped to a real family -> generate normally
    t = (prompt or "").lower()
    obj = next((w for w in _UNSUPPORTED_EVERYDAY if re.search(rf"\b{re.escape(w)}\b", t)), None)
    if obj is None:
        return None
    return {
        "question": (
            f"A {obj} isn't a supported deterministic family yet, and free-form "
            "generation often produces a broken model. Tell me a bit more and I'll "
            f"build a simplified concept: (1) overall size (length × width/height in "
            "mm)? (2) which main parts/features matter most? (3) any handle/head "
            "dimensions?"
        ),
        "questions": [
            f"Overall {obj} dimensions (length × width/height in mm)?",
            "Which main parts or features matter most?",
            "Any specific handle / head dimensions?",
        ],
    }


def _store_clarification(db: Session, design: Design, clar: dict,
                         parse_start: float) -> Design:
    """Persist a clarification result (no geometry) for a vague prompt."""
    design.route = "clarification"
    design.route_reason = "Prompt too vague to generate a safe part."
    design.clarification_question = clar["question"]
    design.missing_required = clar.get("questions", [])
    design.can_generate_with_defaults = False
    design.spec_json = None
    design.semantic_json = None
    design.object_type = None
    log_event("prompt_parsed", design_id=design.id, provider="deterministic",
              routed="clarification", produced_spec=False,
              latency_ms=elapsed_ms(parse_start))
    db.commit()
    db.refresh(design)
    return design


def reconciled_validation_status(design: Design) -> str | None:
    """Effective validation status, reconciled with the evidence so the UI can
    never show a PASS that contradicts the checks:

    * critical failures stay critical_failure (export blocked elsewhere);
    * a dimension report that is NOT within tolerance, or a feature audit with
      missing required features, downgrades a PASS to 'warning' (REVIEW);
    * otherwise the dimension report's own status stands.
    """
    sem = design.semantic_json or {}
    dim = sem.get("dimension_report") or {}
    status = (dim.get("validation") or {}).get("status")
    if not status or status == "critical_failure":
        return status
    within = dim.get("within_tolerance")
    audit_passed = (sem.get("feature_audit") or {}).get("passed")
    if within is False or audit_passed is False:
        return "warning"
    return status


def _run_generation(db: Session, design: Design, prompt: str, parse_start: float) -> Design:
    # PRIMARY ROUTE: plain English -> CadPlan feature graph -> deterministic
    # CadQuery compile -> validate -> repair. Composes primitives instead of
    # routing the whole prompt to a fixed template (no flange->adapter_plate,
    # pipe-spool->tee, U-bracket->enclosure misclassification).
    if settings.cad_engine == "feature_graph":
        planned = _try_cad_plan(db, design, prompt, parse_start)
        if planned is not None:
            return planned

    # Legacy fallback pipeline (template-first), only when the feature graph
    # can't build the part — kept as a safety net.
    from app.parsing.complex_plan import looks_complex, plan_prompt

    if not looks_complex(prompt):
        compiled = _try_compiler(db, design, prompt, parse_start)
        if compiled is not None:
            return compiled

    routed = "complex_plan" if looks_complex(prompt) else "unified_plan"
    result = _plan_long_prompt(prompt) if routed == "complex_plan" else plan_prompt(prompt)
    design.assumptions = result.assumptions
    design.route = result.route or ("precision_template" if result.spec else "clarification")
    design.route_reason = result.route_reason
    design.auto_repaired = bool(result.auto_repaired)
    design.export_formats = result.export_formats
    log_event(
        "prompt_parsed",
        design_id=design.id,
        provider=settings.llm_provider,
        routed=routed,
        produced_spec=result.spec is not None,
        latency_ms=elapsed_ms(parse_start),
    )

    if result.spec is None:
        # Needs clarification — persist the question + what was missing, no geometry.
        design.clarification_question = result.clarification_question
        design.missing_required = result.missing_required
        design.can_generate_with_defaults = result.can_generate_with_defaults
        design.clarified_spec_candidate = result.clarified_spec_candidate
        if result.raw_llm_output:
            design.object_type = result.raw_llm_output.get("object_type")
        db.commit()
        db.refresh(design)
        return design

    design.clarification_question = None
    try:
        _regenerate_geometry(db, design, result.spec)
    except CadGenerationError as exc:
        # Spec validated but the geometry is contradictory (e.g. bore > body).
        # Ask for a fix instead of failing the request.
        design.spec_json = None
        design.object_type = result.spec.object_type
        design.clarification_question = (
            f"Those values don't form a buildable part: {exc}. "
            "Could you adjust them?"
        )
        db.commit()
        db.refresh(design)
        return design
    db.commit()
    db.refresh(design)
    return design


def regenerate_design(
    db: Session,
    design: Design,
    dimensions: dict[str, float],
    holes: list[dict] | None,
    fillet_radius: float | None,
    manufacturing_method: str | None,
    material: str | None,
) -> Design:
    """Deterministic rebuild from edited parameters (no LLM)."""
    base = dict(design.spec_json or {})
    if not base.get("object_type"):
        raise ValueError("Design has no validated spec yet; cannot regenerate")

    # Parameters from the UI are in mm; persist them as mm with units=mm so the
    # rebuild is unit-stable regardless of the original prompt's units.
    base["units"] = "mm"
    # Drop zero-valued params: 0 means "feature off" and the template default
    # (also 0 for optional toggles) applies, keeping the strict schema happy.
    base["dimensions"] = {k: float(v) for k, v in dimensions.items() if float(v) > 0}
    if holes is not None:
        base["holes"] = holes
    if fillet_radius is not None:
        base["fillet_radius"] = fillet_radius
    if manufacturing_method:
        base["manufacturing_method"] = manufacturing_method
    if material:
        base["material"] = material

    spec = DesignSpec(**base)  # re-validate every edit
    _regenerate_geometry(db, design, spec)
    db.commit()
    db.refresh(design)
    return design


def modify_design(db: Session, design: Design, prompt: str) -> tuple[Design, str | None]:
    """Apply a natural-language edit prompt to an existing design.

    Returns (design, clarification). If a clarification is returned the geometry
    is left unchanged. The LLM only ever emits a strict DesignModification.
    """
    if not design.spec_json:
        raise ValueError("Design has no validated spec yet; nothing to modify")

    current = DesignSpec(**design.spec_json)
    result = parse_and_apply(prompt, current)

    if result.spec is None:
        db.refresh(design)
        return design, result.clarification_question

    _regenerate_geometry(db, design, result.spec)
    if result.summary:
        existing = list(design.assumptions or [])
        design.assumptions = existing + [f"Edit: {result.summary}"]
    db.commit()
    db.refresh(design)
    return design, None


def apply_spec_edit(
    db: Session, design: Design, new_spec: DesignSpec, note: str | None = None
) -> Design:
    """Rebuild a design from an already-validated DesignSpec (localized edits,
    confirmed drawing interpretations). Deterministic; no LLM."""
    _regenerate_geometry(db, design, new_spec)
    if note:
        design.assumptions = list(design.assumptions or []) + [f"Edit: {note}"]
    db.commit()
    db.refresh(design)
    return design


def create_design_from_spec(
    db: Session, spec: DesignSpec, prompt: str, user_id: str, name: str | None = None
) -> Design:
    """Create a new owned design directly from a validated spec (e.g. a confirmed
    drawing interpretation)."""
    project = _ensure_project(db, None, name, user_id)
    design = Design(project_id=project.id, prompt=prompt)
    db.add(design)
    db.flush()
    design.clarification_question = None
    _regenerate_geometry(db, design, spec)
    db.commit()
    db.refresh(design)
    return design


def add_feedback(
    db: Session,
    design: Design,
    user_id: str,
    rating: str,
    categories: list[str],
    comment: str | None,
) -> Feedback:
    """Record (or replace) this user's feedback for a design."""
    # One feedback row per user+design: update in place if it exists.
    existing = next((f for f in design.feedback if f.user_id == user_id), None)
    if existing is not None:
        existing.rating = rating
        existing.categories = categories
        existing.comment = comment
        existing.spec_hash = design.spec_hash
        existing.object_type = design.object_type
        fb = existing
    else:
        fb = Feedback(
            user_id=user_id,
            design_id=design.id,
            rating=rating,
            categories=categories,
            comment=comment,
            spec_hash=design.spec_hash,
            object_type=design.object_type,
        )
        db.add(fb)
    db.commit()
    db.refresh(fb)
    log_event(
        "feedback_submitted",
        design_id=design.id,
        rating=rating,
        categories=categories,
        has_comment=bool(comment),
    )
    return fb
