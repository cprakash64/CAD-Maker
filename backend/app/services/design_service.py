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


def _gear_subtype(prompt: str | None) -> str | None:
    """An unsupported gear subtype named in the prompt (helical/bevel/worm/…),
    so the build flags 'approximated as a spur gear' instead of a silent PASS."""
    from app.cad.semantic_audits import detect_gear_subtype
    return detect_gear_subtype(prompt)


def _is_gear_intent(object_type: str | None, prompt: str | None) -> bool:
    """True when a GEAR (not pulley) was asked for on a gear-capable template —
    forces the tooth audit even if the generator omitted tooth_count, while never
    auditing a non-gear part (e.g. a 'gearbox plate') as a gear."""
    from app.cad.semantic_audits import GEAR_OBJECT_TYPES

    if (object_type or "").lower() not in GEAR_OBJECT_TYPES:
        return False
    return bool(re.search(r"\bgears?\b|\bsprocket\b|\bcog\b", (prompt or "").lower()))


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
    # Gear context for the semantic tooth audit (tooth_count lives in the spec's
    # dimensions for the gear/pulley template).
    tooth_count = None
    tc = spec.dimensions.get("tooth_count")
    if tc is not None:
        try:
            tooth_count = int(round(float(tc)))
        except (TypeError, ValueError):
            tooth_count = None
    design.semantic_json = {
        "dimension_report": build_spec_report(
            requested_dimensions_mm={k: spec.to_mm(v) for k, v in spec.dimensions.items()},
            bbox_mm=result.bounding_box_mm,
            volume_mm3=result.volume_mm3,
            surface_area_mm2=result.surface_area_mm2,
            hole_count=len(spec.holes),
            smallest_hole_mm=smallest_hole,
            stl_bytes=result.stl_bytes,
            object_type=spec.object_type,
            requested_tooth_count=tooth_count,
            requested_gear_type=_gear_subtype(design.prompt),
            gear_intent=_is_gear_intent(spec.object_type, design.prompt),
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


def _try_gear(db: Session, design: Design, prompt: str, parse_start: float) -> Design:
    """DETERMINISTIC SPUR-GEAR route. Any gear prompt is built here, BEFORE the
    LLM/CadPlan, feature-graph, pulley or generic-part paths, so a gear can never
    be routed to a smooth disc. Tagged route='deterministic_spur_gear'.

    Geometry is the module-based toothed profile (GearPulleyTemplate); the
    semantic tooth audit runs inside _regenerate_geometry (gear_intent), so a
    smooth-disc request fails validation instead of passing."""
    from app.cad.gear import (
        ROUTE_DETERMINISTIC_SPUR_GEAR,
        gear_dimensions,
        parse_gear_params,
    )

    params = parse_gear_params(prompt)
    spec = DesignSpec(
        object_type="simple_gear_or_pulley",
        dimensions=gear_dimensions(params),
        manufacturing_method="fdm_3d_print",
        material="PLA",
    )
    _regenerate_geometry(db, design, spec)  # geometry + exports + dimension report

    design.route = ROUTE_DETERMINISTIC_SPUR_GEAR
    design.route_reason = "Gear prompt → deterministic module-based spur gear."
    design.clarification_question = None
    assumptions = [
        f"Spur gear: module {params['module_mm']:g}mm, {params['tooth_count']} teeth, "
        f"Ø{params['outside_diameter_mm']:g}mm outside, "
        f"Ø{params['root_diameter_mm']:g}mm root, {params['thickness_mm']:g}mm thick, "
        f"Ø{params['bore_diameter_mm']:g}mm {'keyed' if params['square_bore'] else 'circular'} bore.",
        "Approximate trapezoidal spur teeth — concept CAD, not certified AGMA/ISO.",
    ]
    if params["smooth_disc"]:
        assumptions.append(
            "Request asked for a SMOOTH disc labelled as a gear — built without "
            "teeth, which fails the gear audit (a smooth disc is not a gear).")
    design.assumptions = assumptions

    # Debug metadata for the UI / dev verification (family, route, gear sizes,
    # and the geometry-measured visible-teeth verdict).
    semantic = dict(design.semantic_json or {})
    gear_audit = ((semantic.get("dimension_report") or {}).get("semantic_audit") or {}).get("gear") or {}
    semantic["gear_debug"] = {
        "family": "gear",
        "route": ROUTE_DETERMINISTIC_SPUR_GEAR,
        "tooth_count": params["tooth_count"] if not params["smooth_disc"] else 0,
        "module": params["module_mm"],
        "outside_diameter": params["outside_diameter_mm"],
        "pitch_diameter": params["pitch_diameter_mm"],
        "root_diameter": params["root_diameter_mm"],
        "bore_diameter": params["bore_diameter_mm"],
        "bore_shape": "keyed" if params["square_bore"] else "circular",
        "measured_tooth_count": gear_audit.get("measured_tooth_count"),
        "gear_visible_teeth": gear_audit.get("gear_visible_teeth"),
    }
    design.semantic_json = semantic

    db.commit()
    db.refresh(design)
    log_event("prompt_parsed", design_id=design.id, provider="deterministic",
              routed=ROUTE_DETERMINISTIC_SPUR_GEAR, produced_spec=True,
              tooth_count=params["tooth_count"],
              visible_teeth=semantic["gear_debug"]["gear_visible_teeth"],
              validation_status=reconciled_validation_status(design),
              latency_ms=elapsed_ms(parse_start))
    return design


def _try_hex_standoff(db: Session, design: Design, prompt: str, parse_start: float) -> Design:
    """DETERMINISTIC HEX-STANDOFF route. A hex standoff/spacer prompt is built
    here, BEFORE the LLM/CadPlan, feature-graph, round-spacer or generic-part
    paths, so it can never be routed to a round cylinder. Tagged
    route='deterministic_hex_standoff'.

    Geometry is a true six-sided prism (HexStandoffTemplate); the geometry-
    measured hex audit runs inside _regenerate_geometry (object_type
    'hex_standoff'), so a round body fails validation instead of passing."""
    from app.cad.hex_standoff import (
        ROUTE_DETERMINISTIC_HEX_STANDOFF,
        hex_dimensions,
        parse_hex_params,
    )

    params = parse_hex_params(prompt)
    spec = DesignSpec(
        object_type="hex_standoff",
        dimensions=hex_dimensions(params),
        manufacturing_method="fdm_3d_print",
        material="PLA",
    )
    _regenerate_geometry(db, design, spec)  # geometry + exports + hex audit

    design.route = ROUTE_DETERMINISTIC_HEX_STANDOFF
    design.route_reason = "Hex standoff prompt → deterministic six-sided hex prism."
    design.clarification_question = None
    bore_txt = (f"Ø{params['bore_diameter_mm']:g}mm through bore"
                if params["bore_diameter_mm"] else "solid (no bore)")
    assumptions = [
        f"Hex standoff: {params['across_flats_mm']:g}mm across flats "
        f"(≈{params['across_corners_mm']:g}mm across corners), "
        f"{params['length_mm']:g}mm long, {bore_txt}.",
        "True hexagonal prism — across-flats is preserved exactly; across-corners "
        "is derived. Concept CAD — threads are not modelled.",
    ]
    if params["metric_screw"]:
        assumptions.append(
            f"M{params['metric_screw']:g} callout → "
            f"Ø{params['bore_diameter_mm']:g}mm clearance bore.")
    design.assumptions = assumptions

    semantic = dict(design.semantic_json or {})
    hex_audit = ((semantic.get("dimension_report") or {}).get("semantic_audit") or {}).get("hex") or {}
    semantic["hex_debug"] = {
        "family": "hex_standoff",
        "route": ROUTE_DETERMINISTIC_HEX_STANDOFF,
        "across_flats": params["across_flats_mm"],
        "across_corners": params["across_corners_mm"],
        "length": params["length_mm"],
        "bore_diameter": params["bore_diameter_mm"],
        "measured_corner_count": hex_audit.get("measured_corner_count"),
        "hex_six_sided": hex_audit.get("hex_six_sided"),
    }
    design.semantic_json = semantic

    db.commit()
    db.refresh(design)
    log_event("prompt_parsed", design_id=design.id, provider="deterministic",
              routed=ROUTE_DETERMINISTIC_HEX_STANDOFF, produced_spec=True,
              across_flats=params["across_flats_mm"],
              hex_six_sided=hex_audit.get("hex_six_sided"),
              validation_status=reconciled_validation_status(design),
              latency_ms=elapsed_ms(parse_start))
    return design


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

    # Gears / pulleys go through the dedicated module-based template (consistent
    # tooth geometry, prompt-dimension fidelity, and the semantic tooth audit) —
    # never the program path, which hard-coded thickness and skipped the audit.
    if re.search(r"\b(gear|pulley|sprocket|cog)\b", prompt.lower()):
        return None

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


def _attach_contract(db: Session, design: Design, prompt: str) -> None:
    """Record the prompt understanding + the resolved universal-contract terminal
    state on the design. Advisory metadata: it reports what happened, it never
    re-routes or alters geometry. Committed in place."""
    from app.cad.contract import contract_metadata
    from app.cad.understanding import understand_prompt

    semantic = dict(design.semantic_json or {})
    try:
        semantic["understanding"] = understand_prompt(prompt).to_dict()
    except Exception:  # noqa: BLE001 — understanding is best-effort metadata
        pass
    contract = contract_metadata(design)
    semantic["contract"] = contract
    semantic["telemetry"] = _build_telemetry(design, semantic, contract)
    design.semantic_json = semantic
    db.add(design)
    db.commit()
    db.refresh(design)


def _build_telemetry(design: Design, semantic: dict, contract: dict) -> dict:
    """Flat, beta-testing telemetry: the routing/validation decisions for this
    design in one place (also surfaced via the DTO)."""
    cls = semantic.get("classification") or {}
    understanding = semantic.get("understanding") or {}
    return {
        "route_selected": design.route,
        "family_selected": cls.get("family_id") or understanding.get("family"),
        "confidence": cls.get("confidence"),
        "missing_fields": list(design.missing_required or []),
        "generation_outcome": contract.get("outcome"),
        "validation_status": reconciled_validation_status(design),
        "repair_attempted": int(design.repair_attempts or 0) > 0,
        "export_blocked": is_critical_failure(design),
    }


def _store_failed_safe(db: Session, design: Design, prompt: str, exc: Exception) -> Design:
    """Last-resort safe landing: an unexpected error during generation must not
    surface as a broken model or a 500. Reset the row to a geometry-free
    `failed_safe` state with an honest, actionable message."""
    db.rollback()
    design = db.get(Design, design.id)
    design.route = "failed_safe"
    design.route_reason = "Generation could not produce safe geometry."
    design.object_type = None
    design.spec_json = None
    design.preview_json = None
    design.bounding_box = None
    design.clarification_question = (
        "I couldn't generate a safe model for that prompt. Try describing one "
        "single mechanical part with its key dimensions (for example: 'a "
        "rectangular plate 80x40x5mm with four 6mm holes')."
    )
    design.missing_required = [
        "A single-part description with overall dimensions in mm",
    ]
    design.can_generate_with_defaults = False
    design.semantic_json = {"failed_safe": {"error": type(exc).__name__}}
    for old in list(design.exports):
        db.delete(old)
    for old in list(design.checks):
        db.delete(old)
    db.commit()
    db.refresh(design)
    return design


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

    # UNIVERSAL CONTRACT GUARANTEE: every prompt must leave generation in one
    # of six safe terminal states. Known, intentional signals (LLM unavailable /
    # CAD generation refused) keep propagating so the API renders them as clean
    # 503/422 responses; ONLY an unexpected error is caught here and converted
    # into a `failed_safe` design instead of a 500 with no usable result.
    from app.llm.base import LLMUnavailableError

    try:
        design = _dispatch_generation(db, design, prompt, classification)
    except (LLMUnavailableError, CadGenerationError):
        raise
    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        log_event("generation_failed_safe", design_id=design.id,
                  error=type(exc).__name__, detail=str(exc)[:200])
        design = _store_failed_safe(db, design, prompt, exc)

    _attach_classification(db, design, classification)
    _attach_contract(db, design, prompt)
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

    # 2bb) DETERMINISTIC HEX-STANDOFF GATE: a hex standoff/spacer is built by the
    #      dedicated six-sided hex-prism builder BEFORE the LLM/CadPlan, feature-
    #      graph, round-spacer or generic-part paths — the production bug was a hex
    #      standoff routed to one of those and rendered as a round cylinder. Runs
    #      AFTER the complexity gate (so a whole machine that mentions a spacer
    #      still decomposes). A plain round "spacer" (no 'hex') is unaffected.
    from app.cad.hex_standoff import is_hex_standoff_prompt

    if is_hex_standoff_prompt(prompt):
        try:
            return _try_hex_standoff(db, design, prompt, parse_start)
        except CadGenerationError as exc:
            log_event("hex_standoff_generation_failed", design_id=design.id,
                      detail=str(exc)[:200])
            # fall through to the normal pipeline only if the hex build failed.

    # 2c) DETERMINISTIC SPUR-GEAR GATE: a genuine gear part is built by the
    #     dedicated module-based toothed-profile builder BEFORE the LLM/CadPlan,
    #     feature-graph, pulley or generic-part paths — the production bug was a
    #     gear routed to one of those and rendered as a smooth disc. Runs AFTER the
    #     complexity gate so whole machines that merely mention a gear (e.g. an
    #     aircraft with 'landing gear') still decompose. A bare "pulley" is not a
    #     gear and is unaffected.
    from app.cad.gear import is_gear_prompt

    if is_gear_prompt(prompt):
        try:
            return _try_gear(db, design, prompt, parse_start)
        except CadGenerationError as exc:
            log_event("gear_generation_failed", design_id=design.id,
                      detail=str(exc)[:200])
            # fall through to the normal pipeline only if the gear build failed.

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
    # Everyday concept-fallback families — built offline (single connected concept
    # solids) so a casual everyday prompt never times out or hits free-form CAD.
    "hammer", "wrench", "pliers", "wheel", "fan_blade", "hook",
    "generic_handle", "tool_holder", "generic_stand", "simple_casing",
})


# Vague-prompt detection: a generic part CATEGORY word with no type and no
# dimension ("make a bracket" / "a mount" / "a holder").
_BRACKET_TYPE_RE = re.compile(
    r"\b(l|u|hinge|angle|flat|corner|gusset|shelf|mounting)[- ]?bracket\b", re.I)
# Mount qualifiers that make a "mount" prompt specific enough to build.
_MOUNT_QUALIFIER_RE = re.compile(
    r"\b(motor|nema|stepper|engine|pipe|shock|servo|camera|sensor|wall|"
    r"vibration|gpu|fan)\b", re.I)

# Ready-to-run suggestions offered when a category prompt is too vague. Each
# `prompt` is a complete, generatable request the UI can submit on one click.
VAGUE_SUGGESTIONS: list[dict] = [
    {"label": "L bracket",
     "prompt": "An L bracket with 60mm legs, 5mm thick, 20mm wide, two 6mm holes per face"},
    {"label": "U bracket",
     "prompt": "A U bracket 80mm wide, 60mm tall, 6mm thick with two M6 base holes "
               "and an 8mm pivot hole through each side wall"},
    {"label": "Flat mounting plate",
     "prompt": "A rectangular mounting plate 80x40x5mm with four 6mm holes"},
    {"label": "Hinge bracket",
     "prompt": "A hinge bracket with a 70x40x6mm base and two side ears 30mm tall, "
               "6mm thick, with an 8mm pin hole through both ears"},
    {"label": "Pipe clamp",
     "prompt": "A pipe clamp for 32mm OD tube, 20mm wide, with two M5 holes"},
    {"label": "Motor mount",
     "prompt": "A NEMA 17 motor plate 60mm square, 6mm thick, with a 22mm center bore "
               "and four M3 holes on a 31mm square pattern"},
    {"label": "Shelf bracket",
     "prompt": "A shelf bracket 120mm x 80mm x 5mm with a corner gusset and four "
               "6mm mounting holes"},
]


def _vague_category(prompt: str) -> str | None:
    """The vague part category ('bracket' | 'mount' | 'holder'), or None. Only
    fires with NO dimension/number AND no specific type — so 'make a bracket'
    clarifies but 'an L bracket with 60mm legs' or 'motor mount' builds."""
    t = (prompt or "").strip().lower()
    if not t or re.search(r"\d", t):  # any number -> has real detail
        return None
    if re.search(r"\bbracket\b", t) and not _BRACKET_TYPE_RE.search(t) \
            and "hinge" not in t and "gusset" not in t:
        return "bracket"
    if re.search(r"\bmount\b", t) and not _MOUNT_QUALIFIER_RE.search(t):
        return "mount"
    if re.search(r"\bholder\b", t) and "tool holder" not in t and "tool-holder" not in t:
        return "holder"
    return None


def _vague_clarification(prompt: str) -> dict | None:
    """Clarification (with clickable, ready-to-run suggestions) for a too-vague
    category prompt, else None."""
    category = _vague_category(prompt)
    if category is None:
        return None
    return {
        "question": (
            f"I can build that, but \"{category}\" is broad — pick one of the "
            "suggested parts below (each is ready to generate), or add a type and "
            "key dimensions (e.g. length × width × thickness in mm and hole count)."
        ),
        "questions": [
            f"Which kind of {category}? Choose a suggestion or name the type.",
            "Overall dimensions (length × width × thickness in mm)?",
            "How many mounting holes, and what diameter?",
        ],
        "options": VAGUE_SUGGESTIONS,
    }


def _everyday_object_clarification(prompt: str, classification) -> dict | None:
    """Clarification for an unsupported everyday object, else None. Only fires
    when the classifier did NOT map the prompt to a specific family (so supported
    objects and detailed mechanical parts are unaffected). The everyday-object
    list lives in app.cad.understanding (single source of truth)."""
    from app.cad.families import GENERIC_PART_FAMILY
    from app.cad.understanding import detect_unsupported_everyday

    fam_id = getattr(classification, "family_id", None)
    if fam_id and fam_id != GENERIC_PART_FAMILY:
        return None  # mapped to a real family -> generate normally
    obj = detect_unsupported_everyday(prompt)
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
    # Ready-to-run family suggestions (clickable in the UI) live in semantic_json
    # so the DTO can surface them; classification/contract metadata is merged in
    # afterwards by _attach_classification / _attach_contract.
    options = clar.get("options")
    design.semantic_json = {"clarification_options": options} if options else None
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
