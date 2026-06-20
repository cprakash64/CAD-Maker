"""Drawing-to-CAD Assist routes.

POST /api/drawings/interpret  — upload a 2D drawing image -> validated interpretation
POST /api/drawings/confirm    — user-confirmed interpretation -> generated design

The model only ever returns a validated DrawingInterpretationSpec; geometry is
created from it (after confirmation) by the trusted templates. Missing critical
data yields clarification questions, never a guess.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.cad.base import CadGenerationError
from app.database import get_db
from app.drawing.interpret import interpret_image, to_design_spec
from app.drawing.scale import ScaledDrawing, infer_scale
from app.models import User
from app.observability import log_event
from app.rate_limit import rate_limit
from app.schemas.drawing_spec import DrawingInterpretationSpec
from app.services import design_service

router = APIRouter(prefix="/api/drawings", tags=["drawings"])

_MAX_IMAGE_BYTES = 12 * 1024 * 1024


@router.post("/interpret", response_model=DrawingInterpretationSpec,
             dependencies=[rate_limit("drawing")])
async def interpret(
    file: UploadFile = File(...),
    hint: str | None = Form(default=None),
    user: User = Depends(get_current_user),
) -> DrawingInterpretationSpec:
    from app.config import settings

    if not settings.drawing_to_cad_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Image understanding is unavailable: the current provider "
                f"('{settings.llm_provider}') cannot read drawings. Set "
                "LLM_PROVIDER=openai with an API key (or DEV_ALLOW_MOCK_DRAWING=true "
                "in development to use the text-hint workaround)."
            ),
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 12 MB)")
    media_type = file.content_type or "image/png"
    interp = interpret_image(data, media_type, hint=hint)
    log_event(
        "drawing_interpreted",
        suggested_object_type=interp.suggested_object_type,
        actionable=interp.is_actionable(),
        clarifications=len(interp.clarification_questions),
        confidence=interp.overall_confidence,
    )
    return interp


@router.post("/confirm", dependencies=[rate_limit("drawing")])
def confirm(
    interp: DrawingInterpretationSpec,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a design from a user-confirmed interpretation — ASSUMPTION-FIRST.

    A recognized mechanical drawing generates even with open clarification
    questions: missing units/PCD/thicknesses become assumptions + warnings on
    the design. Only a non-mechanical, unrecognizable, or very-low-confidence
    interpretation is refused. Template types build via the trusted template
    path when fully specified; everything else (incl. partially-specified
    template types) builds via the feature-graph engine.
    """
    if not interp.generatable_with_assumptions():
        raise HTTPException(
            status_code=422,
            detail=(
                interp.unsupported_reason
                or "This doesn't look like a recognizable mechanical drawing. "
                "Upload a clearer image or add a correction hint describing the part."
            ),
        )
    from app.routers.designs import _to_dto

    design = _generate_from_interpretation(db, interp, user)
    return _to_dto(design, user)


@router.post("/generate", dependencies=[rate_limit("drawing")])
async def generate(
    file: UploadFile = File(...),
    hint: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """ONE-SHOT drawing → CAD: interpret the uploaded image and, when a
    mechanical object is recognized with usable confidence, immediately generate
    the model (assumption-first — open questions become assumptions + warnings).

    Returns ``{generated, interpretation, design}``: ``design`` is the full
    design DTO when generation ran, else null with the interpretation explaining
    why (non-mechanical / unreadable image)."""
    from app.config import settings

    from app.routers.designs import _to_dto

    if not settings.drawing_to_cad_enabled():
        raise HTTPException(
            status_code=409,
            detail=(
                "Image understanding is unavailable: the current provider "
                f"('{settings.llm_provider}') cannot read drawings."
            ),
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 12 MB)")
    interp = interpret_image(data, file.content_type or "image/png", hint=hint)
    log_event(
        "drawing_generate",
        suggested_object_type=interp.suggested_object_type,
        generatable=interp.generatable_with_assumptions(),
        confidence=interp.overall_confidence,
    )
    if not interp.generatable_with_assumptions():
        return {"generated": False, "interpretation": interp.model_dump(),
                "design": None}
    design = _generate_from_interpretation(db, interp, user)
    return {"generated": True, "interpretation": interp.model_dump(),
            "design": _to_dto(design, user).model_dump()}


def _generate_from_interpretation(db: Session, interp: DrawingInterpretationSpec, user: User):
    """Shared confirm/generate path with HARD drawing-mode acceptance.

    In drawing mode the required-feature audit gates the result: the planner's
    model (which already gets one internal repair pass on a failed audit) must
    pass, otherwise we REBUILD from the deterministic fallback built on the
    structured drawing data. Only if that also fails do we refuse — a wrong
    model is never shown as a drawing's "success"."""
    scaled = infer_scale(interp)
    prompt = _drawing_to_prompt(interp, scaled)
    try:
        design = None
        # Pipe families ALWAYS build via the feature graph (positional flange/
        # bolt-pattern anatomy + audit); other fully-specified template types
        # keep the trusted template path.
        prefer_graph = (interp.suggested_object_type in _FLANGED_FAMILIES
                        or scaled.scale != 1.0)
        if interp.maps_to_template() and interp.is_actionable() and not prefer_graph:
            spec = to_design_spec(interp)
            if spec is not None:
                design = design_service.create_design_from_spec(
                    db, spec,
                    prompt=f"[from drawing] {interp.title or spec.object_type}",
                    user_id=user.id,
                )
        if design is None:
            # Feature graph (pipe_tee, generic parts, and any partially
            # specified drawing — generates with assumptions, never blocks).
            design = design_service.create_design(
                db, prompt, None,
                interp.title or interp.suggested_object_type, user.id,
            )
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _require_drawing_accuracy(db, design, interp, scaled, prompt)
    _annotate_from_drawing(db, design, interp, scaled)
    return design


def _audit_state(design) -> tuple[bool, list[str]]:
    """(passed, failure_descriptions) of the design's persisted feature audit."""
    audit = (design.semantic_json or {}).get("feature_audit") or {}
    if not audit:
        return True, []  # template path: no feature-graph audit to enforce
    fails = [f"{i['feature_id']}: {i['requirement']} ({i['detail']})"
             for i in audit.get("items", []) if not i.get("satisfied")]
    return bool(audit.get("passed")), fails


def _require_drawing_accuracy(db: Session, design, interp: DrawingInterpretationSpec,
                              scaled: ScaledDrawing, prompt: str) -> None:
    """Drawing mode: a failed required-feature audit is an ERROR, not a warning.

    The planner already repaired once; here we rebuild from the deterministic
    fallback (structured drawing spec for pipe branches, deterministic planner
    otherwise). If the fallback can't pass either, delete the design and refuse
    with diagnostics — never show a wrong model for a drawing."""
    from app.drawing.fallback import drawing_fallback_plan

    if design.clarification_question:
        return  # engine asked a fatal question; nothing was generated
    passed, fails = _audit_state(design)
    if passed:
        return
    fallback = drawing_fallback_plan(interp, scaled, prompt)
    if fallback is not None and design_service.rebuild_design_from_plan(
            db, design, fallback, prompt):
        log_event("drawing_fallback_rebuild", design_id=design.id,
                  object_type=design.object_type, ok=True)
        return
    passed_after, fails_after = _audit_state(design)
    if passed_after:
        return
    detail = ("Could not generate accurate CAD from this drawing — the model "
              "is missing required features even after repair and deterministic "
              "fallback: " + "; ".join(fails_after or fails))
    log_event("drawing_generation_rejected", design_id=design.id,
              failures=len(fails_after or fails))
    db.delete(design)
    db.commit()
    raise HTTPException(status_code=422, detail=detail)


def _annotate_from_drawing(db: Session, design, interp: DrawingInterpretationSpec,
                           scaled: ScaledDrawing | None = None) -> None:
    """Carry the drawing's assumptions onto the design and downgrade its open
    clarification questions to non-blocking warnings (visible, never gating)."""
    if design.clarification_question:
        return  # the engine itself asked a FATAL question — leave it intact
    extra = [f"{a.field}: {a.assumption}" for a in interp.assumptions]
    extra += list(scaled.assumptions) if scaled else []
    extra += [
        f"Assumed a default — the drawing didn't answer: {q.question}"
        for q in interp.clarification_questions
    ]
    if interp.drawing_units_confidence < 0.75 and (scaled is None or scaled.scale == 1.0):
        extra.append("Units assumed to be millimetres (not clearly marked on the drawing)")
    seen = set(design.assumptions or [])
    design.assumptions = (design.assumptions or []) + [a for a in extra if a not in seen]

    semantic = dict(design.semantic_json or {"checks": [], "passed": True})
    semantic.setdefault("checks", [])
    semantic["checks"] = list(semantic["checks"]) + [
        {"name": "drawing_clarification", "passed": False,
         "expected": None, "actual": q.question, "severity": "warning"}
        for q in interp.clarification_questions
    ] + [
        {"name": "drawing_scale", "passed": False,
         "expected": None, "actual": w, "severity": "warning"}
        for w in (scaled.warnings if scaled else [])
    ]
    design.semantic_json = semantic
    design.route_reason = "Drawing → feature-graph CAD (interpreted 2D drawing)." \
        if design.route == "cad_plan" else design.route_reason
    db.commit()
    db.refresh(design)


# Part families whose hole callouts describe a per-flange bolt circle.
_FLANGED_FAMILIES = {"flanged_pipe_branch", "pipe_tee", "pipe_spool",
                     "blind_flange", "flange", "pipe_fitting", "pipe_elbow"}


def _drawing_to_prompt(interp: DrawingInterpretationSpec,
                       scaled: ScaledDrawing | None = None) -> str:
    """Synthesize a plain-English prompt from a drawing so the assumption-first
    feature-graph engine can build it.

    The prompt is the drawing's contract with the planner, so it must preserve
    the FULL detected geometry — for a flanged branch/tee that means the
    structural anatomy (vertical main pipe + bore, perpendicular side branch +
    bore, three flanges each carrying a repeated bolt circle), every scaled
    dimension, the per-flange hole callout, and a note when drawing-scale units
    were converted — never a lossy "pipe branch with 12x1mm holes".

    Dimensions go through consistent drawing→mm scale inference and are written
    VALUE-FIRST ("120mm flange outer diameter") because the deterministic
    planner parses the number immediately before its label."""
    scaled = scaled or infer_scale(interp)
    ot = interp.suggested_object_type or "generic_mechanical_part"
    label = ot.replace("_", " ")
    flanged = ot in _FLANGED_FAMILIES

    dims: list[str] = []
    for k, v in scaled.dimensions.items():
        if not v or v <= 0:
            continue
        name = k.removesuffix("_mm").replace("_", " ")
        if "count" in name:  # counts are not lengths
            if "bolt" in name or "hole" in name:
                dims.append(f"{int(v)} holes per flange" if flanged
                            else f"{int(v)} holes")
            elif "tooth" in name:
                dims.append(f"{int(v)} teeth")
            continue
        dims.append(f"{v:g}mm {name}")
    holes = [
        f"{h.count}x {h.diameter:g}mm bolt holes per flange" if flanged
        else f"{h.count}x {h.diameter:g}mm holes"
        for h in scaled.holes
    ]

    if ot in ("flanged_pipe_branch", "pipe_tee"):
        # Full structural anatomy, not just the type name.
        body = (f"Create a {label}: a vertical main run pipe with a central "
                "bore, a perpendicular side branch pipe with its own bore, and "
                "three circular flanges (top, bottom, and branch), each "
                "carrying a repeated bolt-hole circle")
    else:
        body = f"Create a {label}"
    spec = ", ".join(dims + holes)
    if spec:
        body += f". Dimensions from the drawing: {spec}"
    if scaled.scale != 1.0:
        body += ". Drawing-scale dimensions were converted to millimetres"
    return body + ". All dimensions in mm."
