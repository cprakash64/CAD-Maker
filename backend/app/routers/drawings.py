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
from app.schemas.drawing_spec import (
    CONFIDENCE_THRESHOLD,
    GENERATE_WITH_ASSUMPTIONS_CONFIDENCE,
    DrawingInterpretationSpec,
)
from app.services import design_service

router = APIRouter(prefix="/api/drawings", tags=["drawings"])
# Unprefixed alias router so the endpoint is also reachable at /api/drawing-to-cad.
alias_router = APIRouter(tags=["drawings"])

_MAX_IMAGE_BYTES = 12 * 1024 * 1024

# Canonical media type per sniffed file type. Derived from the file's own bytes
# so a client-supplied Content-Type can never mislabel content downstream.
_MEDIA_TYPE_BY_FILE_TYPE = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "svg": "image/svg+xml",
    "dxf": "image/vnd.dxf",
}
_MAX_DRAWING_BYTES = 20 * 1024 * 1024  # PDFs/DXFs run larger than images


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
    # Content-sniff before anything parses the bytes. The client's Content-Type
    # is a hint, never the decision — unsupported content must be rejected
    # explicitly (415) rather than degrading into an "unknown / low confidence"
    # interpretation. See docs/production-readiness.md.
    from app.services.drawing_ingest import UnsupportedDrawingFile, detect_file_type

    try:
        ftype = detect_file_type(data, file.filename, file.content_type)
    except UnsupportedDrawingFile as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    media_type = _MEDIA_TYPE_BY_FILE_TYPE.get(ftype, file.content_type or "image/png")
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


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(get_current_user)):
    """Poll a drawing-generation job: {status, stage, progress, message, error,
    design_id, result?}. ``result`` (the full generate/to-cad payload) is
    attached once the job is done or failed. Owner-scoped: other users' job ids
    404."""
    from app.services import drawing_jobs

    job = drawing_jobs.get_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found (it may have "
                            "expired or the server restarted) — start a new generation.")
    return job.to_json()


@router.get("/debug/{design_id}")
def sketch_debug(design_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """DEV debug: the reconstructed MechanicalSketchIR for a drawing-built design
    (outer profile, classified cuts, grouped counterbores, ignored entities) plus
    whether a colour-coded overlay was saved. Owner-scoped; 404 otherwise."""
    from app.config import settings
    from app.models import Design

    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="Debug is dev-only")
    design = db.get(Design, design_id)
    if design is None or not design_service.user_owns_design(db, design, user.id):
        raise HTTPException(status_code=404, detail="Design not found")
    sketch = (design.semantic_json or {}).get("sketch_ir")
    if not sketch:
        raise HTTPException(status_code=404,
                            detail="This design was not built via sketch reconstruction.")
    overlay = _sketch_debug_dir(design_id) / "overlay.png"
    return {
        "design_id": design_id,
        "summary": sketch.get("summary"),
        "sketch_ir": sketch.get("ir"),
        "overlay_available": overlay.exists(),
        "overlay_url": f"/api/drawings/debug/{design_id}/overlay" if overlay.exists() else None,
    }


@router.get("/debug/{design_id}/overlay")
def sketch_debug_overlay(design_id: str, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """DEV debug: the colour-coded detection overlay PNG for a sketch design."""
    from fastapi.responses import FileResponse

    from app.config import settings
    from app.models import Design

    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="Debug is dev-only")
    design = db.get(Design, design_id)
    if design is None or not design_service.user_owns_design(db, design, user.id):
        raise HTTPException(status_code=404, detail="Design not found")
    overlay = _sketch_debug_dir(design_id) / "overlay.png"
    if not overlay.exists():
        raise HTTPException(status_code=404, detail="No overlay saved")
    return FileResponse(str(overlay), media_type="image/png")


def _run_generate_pipeline(job, data: bytes, media_type: str, hint: str | None,
                           user_id: str) -> dict:
    """Worker for POST /generate — a THIN WRAPPER over the canonical
    Drawing → CAD pipeline (there is exactly one real generation path). Only
    the legacy payload key names differ: ``interpretation`` mirrors the
    canonical ``analysis`` block."""
    result = _run_to_cad_pipeline(
        job, data, filename=None, content_type=media_type, notes=hint,
        units=None, thickness_mm=None, family=None, user_id=user_id)
    log_event("drawing_generate", generated=result["generated"],
              design_id=((result.get("design") or {}).get("id")))
    return {
        "generated": result["generated"],
        "interpretation": result.get("analysis"),
        "analysis": result.get("analysis"),
        "design": result.get("design"),
        "message": result.get("message"),
    }


@router.post("/generate", dependencies=[rate_limit("drawing")])
async def generate(
    file: UploadFile = File(...),
    hint: str | None = Form(default=None),
    sync: bool = Form(default=False),
    user: User = Depends(get_current_user),
):
    """ONE-SHOT drawing → CAD as an ASYNC JOB: fast checks run inline, then the
    interpret+generate pipeline runs on a worker thread. Returns 202 +
    ``{job_id, status, poll}`` immediately; poll GET /api/drawings/jobs/{id}
    until done/failed — ``result`` is ``{generated, interpretation, design}``.

    ``sync=true`` (opt-in, scripts/tests) runs inline and returns the result
    payload directly with status 200."""
    from fastapi.responses import JSONResponse

    from app.services import drawing_jobs

    # Provider availability is decided INSIDE the canonical pipeline: an image
    # whose outline is deterministically traceable still generates without a
    # vision provider; only vision-dependent drawings surface the 409.
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 12 MB)")
    # Sniff content before the pipeline touches it (see /interpret above).
    from app.services.drawing_ingest import UnsupportedDrawingFile, detect_file_type

    try:
        ftype = detect_file_type(data, file.filename, file.content_type)
    except UnsupportedDrawingFile as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    media_type = _MEDIA_TYPE_BY_FILE_TYPE.get(ftype, file.content_type or "image/png")

    if sync:
        job = drawing_jobs.DrawingJob(id="sync", user_id=user.id)
        return _run_generate_pipeline(job, data, media_type, hint, user.id)

    job = drawing_jobs.create_job(user.id)
    drawing_jobs.run_job(
        job, lambda j: _run_generate_pipeline(j, data, media_type, hint, user.id))
    return JSONResponse(status_code=202, content={
        "job_id": job.id, "status": "queued",
        "poll": f"/api/drawings/jobs/{job.id}",
    })


def _run_to_cad_pipeline(job, data: bytes, filename: str | None,
                         content_type: str | None, notes: str | None,
                         units: str | None, thickness_mm: float | None,
                         family: str | None, user_id: str) -> dict:
    """Worker for POST /to-cad: ingest (vector or raster) → analysis →
    deterministic build (or vision + assumption-first engine) → DTO. Runs on a
    job thread with its own DB session; stages feed the poll UI."""
    from app.config import settings
    from app.database import SessionLocal
    from app.routers.designs import _to_dto
    from app.services import drawing_jobs, drawing_to_spec
    from app.services.drawing_ingest import UnreadableDrawingFile, ingest_drawing

    db = SessionLocal()
    try:
        drawing_jobs.set_stage(job, "reading_drawing")
        try:
            ingested = ingest_drawing(data, filename, content_type)
        except UnreadableDrawingFile as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        user = db.get(User, user_id)
        stage = lambda s: drawing_jobs.set_stage(job, s)  # noqa: E731

        # ---- deterministic vector path (DXF / SVG): exact geometry, no vision --
        if ingested.vector is not None:
            stage("extracting_dimensions")
            analysis = drawing_to_spec.analysis_from_vector(
                ingested.vector, filename=filename, units=units or None,
                thickness_mm=thickness_mm, family=family, notes=notes)
            if not analysis.usable():
                return {"generated": False, "analysis": analysis.model_dump(mode="json"),
                        "design": None,
                        "message": "The file parsed but contains no usable part outline. "
                                   "Add a note describing the part, or upload a clearer drawing."}
            stage("building_cad")
            plan = drawing_to_spec.plan_from_analysis(analysis)
            design = None
            if plan is not None:
                try:
                    design = design_service.create_design_from_plan(
                        db, plan, _vector_prompt(analysis, notes), None,
                        analysis.title, user.id)
                except CadGenerationError:
                    design = None  # fall through to the interpretation path below
            if design is None:
                design = _generate_from_interpretation(
                    db, drawing_to_spec.to_interpretation(analysis), user,
                    on_stage=stage)
            stage("validating")
            _annotate_from_analysis(db, design, analysis)
            stage("exporting")
            log_event("drawing_to_cad", file_type=ingested.file_type, source="vector",
                      design_id=design.id, holes=analysis.features.hole_count())
            return {"generated": True, "analysis": analysis.model_dump(mode="json"),
                    "design": _to_dto(design, user).model_dump(mode="json"),
                    "message": None}

        # ---- raster path (PNG/JPG/WEBP + rendered PDF) ------------------------
        # Deterministic contour extraction runs FIRST: when the drawing is one
        # dominant closed outline with no holes, the shape comes from the
        # pixels; vision is only consulted for dimensions/labels — and its
        # failure can never invent a different part.
        if ingested.image_bytes is None:
            raise HTTPException(status_code=422,
                                detail="Couldn't extract an image from this file.")
        from app.drawing.preprocess import preprocess_drawing_image
        from app.drawing.raster_profile import extract_profile
        from app.services.raster_flanged_pipe_branch import (
            detect_flanged_branch,
            interp_from_evidence,
            params_from_notes,
        )

        # Content fingerprint (determinism): the same image bytes + same code/
        # settings must take the same route and produce the same CAD. The hashes
        # make a replay observable in logs.
        import hashlib

        image_hash = hashlib.sha256(ingested.image_bytes).hexdigest()[:16]
        log_event("drawing_image_hash", image_hash=image_hash,
                  bytes=len(ingested.image_bytes))

        # Clean the raster ONCE for both the provider and the CV analyses:
        # orientation, white background, downscale, crop to the linework.
        ingested.image_bytes, prep_notes = preprocess_drawing_image(
            ingested.image_bytes)
        ingested.notes.extend(prep_notes)
        preprocess_hash = hashlib.sha256(ingested.image_bytes).hexdigest()[:16]
        log_event("drawing_preprocess_hash", image_hash=image_hash,
                  preprocess_hash=preprocess_hash)

        # Deterministic pre-classification runs BEFORE any LLM: closed-outline
        # tracing (profile parts), flange-face/bolt-ring detection (flanged
        # fittings), and side-branch topology (a branch/tee vs a straight
        # spool). All sub-second pixel analyses.
        from app.drawing.pipe_branch_evidence import (
            detect_side_branch,
            has_branch_text_cue,
        )

        profile = extract_profile(ingested.image_bytes)
        branch_det = detect_flanged_branch(ingested.image_bytes)
        notes_params = params_from_notes(notes)
        branch_topo = detect_side_branch(
            ingested.image_bytes,
            text_cues=" ".join(t for t in (notes, ingested.text_context) if t) or None)
        side_branch = bool(branch_topo and branch_topo.side_branch)

        interp = None
        if notes_params is not None:
            pass  # notes fast path: explicit parameters bypass vision entirely
        elif settings.drawing_to_cad_enabled():
            hint = _combined_hint(notes, ingested.text_context, family, units,
                                  thickness_mm)
            # With deterministic evidence in hand, vision is only a dimension-
            # quality upgrade — give it a SHORT budget instead of the full one.
            stage("interpreting")
            interp = interpret_image(
                ingested.image_bytes, ingested.media_type or "image/png",
                hint=hint,
                budget_seconds=12.0 if (branch_det or profile) else None)
            _apply_user_overrides(interp, units, thickness_mm, family)
        elif profile is None and branch_det is None:
            raise HTTPException(
                status_code=409,
                detail=("Image understanding is unavailable: the current provider "
                        f"('{settings.llm_provider}') cannot read drawings. Upload the "
                        "drawing as SVG or DXF (parsed without AI), or enable an "
                        "image-capable provider."),
            )
        stage("extracting_dimensions")

        # HYBRID flanged-pipe-branch route: pixel/notes evidence carries the
        # family when vision failed, timed out, or the user stated parameters —
        # a vision timeout degrades to a deterministic build, never a failure.
        #
        # SIDE-BRANCH OVERRIDE: when the sheet shows a side branch (main pipe +
        # perpendicular outlet + third flange) the family is flanged_pipe_branch
        # even if vision read it as a straight "pipe spool" — a branch built as
        # a two-flange spool drops the entire branch. The topology signal wins
        # over the vision label, so we take the hybrid branch build in that case
        # too (not only when vision failed).
        vision_generatable = interp is not None and interp.generatable_with_assumptions()
        want_branch_build = (branch_det is not None or notes_params is not None) and (
            notes_params is not None or not vision_generatable or side_branch)
        if want_branch_build:
            hybrid = interp_from_evidence(branch_det, vision=interp,
                                          notes_params=notes_params)
            if hybrid is not None:
                if side_branch:
                    hybrid.suggested_object_type = "flanged_pipe_branch"
                log_event("drawing_hybrid_branch_route",
                          detected=branch_det is not None,
                          from_notes=notes_params is not None,
                          side_branch=side_branch,
                          vision_ok=vision_generatable)
                interp = hybrid
        elif side_branch and vision_generatable \
                and interp.suggested_object_type in _PIPE_FLANGE_SPOOL_TYPES:
            # Vision produced usable dims but as a spool/flange; there is no
            # bolt-ring detection to assemble from, so re-label the family so the
            # deterministic branch builder (main + branch + 3 flanges) runs.
            interp.suggested_object_type = "flanged_pipe_branch"
            log_event("drawing_pipe_branch_relabeled", from_vision=True)

        if branch_topo is not None:
            log_event("drawing_pipe_route_decision",
                      route="flanged_pipe_branch" if side_branch else "pipe_spool",
                      reason=branch_topo.reason)

        # MECHANICAL SKETCH RECONSTRUCTION: a plate/bracket with STRUCTURED
        # features (counterbores, slots, rectangular cutouts) is reconstructed
        # into a feature-graph sketch — the outer profile + classified cuts +
        # concentric-grouped counterbores — rather than a rough contour with
        # random holes. Runs before the plain-profile route; the simple
        # single-outline drawings (only round/polygon holes) still take the
        # profile route below so their object_type stays profile_extrusion.
        # Pipe/flange/branch drawings have dedicated parametric builders — never
        # reconstruct them as a flat plate sketch.
        is_pipe_flange = side_branch or branch_det is not None or notes_params is not None
        from app.schemas.drawing_spec import is_pipe_flange_detection as _ispf
        if interp is not None:
            is_pipe_flange = is_pipe_flange or _ispf(
                interp.suggested_object_type, interp.detected_object_type)
        if not is_pipe_flange and not (
                family and family not in ("profile_extrusion", "generic_extruded_part",
                                          "reconstructed_sketch_part")):
            sketch = _try_sketch_reconstruction(
                db, user, ingested, interp, thickness_mm, notes, stage)
            if sketch is not None:
                return sketch

        # Dedicated 2D-profile route: extrude the traced outline exactly.
        if not (family and family not in ("profile_extrusion", "generic_extruded_part")) \
                and drawing_to_spec.profile_route_applies(profile, interp):
            # ROUTE GUARD (Part A): raster_profile approximates every
            # non-circular loop as a circle/rectangle. If a Sketch IR carries
            # richer features (polygon holes, slots, concentric groups), the
            # profile route would silently DROP them — reject it and let the
            # sketch reconstruction win instead. Only when the sketch build also
            # fails do we fall back to the (feature-losing) profile route.
            guarded = _route_guard_sketch_over_profile(
                db, user, ingested, interp, thickness_mm, notes, stage,
                is_pipe_flange, profile=profile)
            if guarded is not None:
                return guarded
            plan, used_default_scale = drawing_to_spec.plan_from_raster_profile(
                profile, interp, thickness_mm=thickness_mm,
                dimension_texts=_sheet_texts(ingested, notes))
            stage("building_cad")
            design = design_service.create_design_from_plan(
                db, plan, _profile_prompt(plan, notes), None,
                "Extruded profile (from drawing)", user.id)
            stage("validating")
            confidence = (interp.overall_confidence
                          if interp is not None and not used_default_scale else 0.55)
            analysis = drawing_to_spec.analysis_from_profile(profile, plan, confidence)
            _annotate_from_analysis(db, design, analysis,
                                    used_default_fallback=used_default_scale)
            # Silhouette-only gate: a dimensioned drawing reduced to a plain
            # extruded outline (no holes/inner opening) is flagged REVIEW.
            _apply_feature_coverage_gate(db, design, ingested, notes, interp,
                                         profile=profile)
            # FINAL PRE-SAVE GUARD: a profile_extrusion must never be a PASS for a
            # dimensioned mechanical drawing (text callouts present). Force REVIEW.
            _evidence = _drawing_dimension_evidence(ingested, notes, interp)
            _route, _mech = _drawing_route_and_mechanical(_evidence, profile=profile)
            if _mech:
                log_event("drawing_final_candidate_guard_failed",
                          design_id=design.id, route=_route,
                          reason="profile_extrusion_for_dimensioned_drawing")
                _force_review(db, design, "silhouette-only profile_extrusion "
                              "for a dimensioned mechanical drawing")
            stage("exporting")
            log_event("drawing_candidate_selection_final",
                      selected_source="raster_profile", selected_reason=(
                          "review_forced" if _mech else "logo_or_simple_profile"),
                      design_id=design.id, route=_route)
            log_event("drawing_candidate_selected", design_id=design.id,
                      source="raster_profile", holes=len(profile.holes or []),
                      estimated_dimensions=used_default_scale)
            log_event("drawing_to_cad", file_type=ingested.file_type,
                      source="raster_profile", design_id=design.id,
                      points=len(profile.points), default_scale=used_default_scale)
            return {"generated": True, "analysis": analysis.model_dump(mode="json"),
                    "design": _to_dto(design, user).model_dump(mode="json"),
                    "message": None}

        # GENERIC-REPLACEMENT SKETCH: a drawing the vision model read only
        # vaguely (or not at all) is reconstructed from its CV linework — NEVER
        # a generic-prompt cube/mounting-block. This is CV-driven, so the same
        # image takes this same route whether vision succeeded generically or
        # timed out (the "cube once, correct CAD later" inconsistency).
        _generic_family = interp is None or interp.suggested_object_type in (
            None, "generic_mechanical_part", "generic_extruded_part")
        if not is_pipe_flange and _generic_family:
            sketch = _try_sketch_reconstruction(
                db, user, ingested, interp, thickness_mm, notes, stage,
                require_rich=False)
            if sketch is not None:
                return sketch

        # No trustworthy contour: the vision interpretation must carry the part.
        analysis = drawing_to_spec.analysis_from_interpretation(
            interp, units=units or None, thickness_mm=thickness_mm, family=family,
            notes=notes) if interp is not None else None
        if interp is None or not interp.generatable_with_assumptions():
            # Provider failure/weak reading is NOT a job failure: the
            # deterministic best-effort fallback (segmented main contour /
            # flange family) generates a REVIEW-flagged model from the
            # linework. Only no-usable-geometry (or a kernel failure after all
            # fallbacks) still lands in generated=false.
            fallback = _deterministic_fallback_response(
                db, user, ingested, interp, thickness_mm, notes, stage)
            if fallback is not None:
                return fallback
            return {"generated": False,
                    "analysis": analysis.model_dump(mode="json") if analysis else None,
                    "design": None,
                    "message": ((interp.provider_error or interp.unsupported_reason
                                 if interp is not None else None)
                                or "This doesn't look like a readable mechanical drawing. "
                                   "Add a note describing the part and its key dimensions, "
                                   "then try again.")}
        if analysis.inferred_depth_mm and not any(
                "thick" in k.lower() or "depth" in k.lower() or "height" in k.lower()
                for k in interp.overall_dimensions):
            interp.overall_dimensions["thickness"] = analysis.inferred_depth_mm
        stage("building_cad")
        design = _generate_from_interpretation(db, interp, user, on_stage=stage,
                                               image_hash=image_hash)
        stage("validating")
        _annotate_from_analysis(db, design, analysis)
        stage("exporting")
        log_event("drawing_to_cad", file_type=ingested.file_type, source="vision",
                  design_id=design.id, confidence=interp.overall_confidence)
        return {"generated": True, "analysis": analysis.model_dump(mode="json"),
                "design": _to_dto(design, user).model_dump(mode="json"),
                "message": None}
    finally:
        db.close()


@router.post("/to-cad", dependencies=[rate_limit("drawing")])
@alias_router.post("/api/drawing-to-cad", dependencies=[rate_limit("drawing")])
async def drawing_to_cad(
    file: UploadFile = File(...),
    notes: str | None = Form(default=None),
    units: str | None = Form(default=None),
    thickness_mm: float | None = Form(default=None),
    family: str | None = Form(default=None),
    sync: bool = Form(default=False),
    user: User = Depends(get_current_user),
):
    """Drawing → CAD for ALL supported file types (PNG/JPG/JPEG/WEBP/PDF/SVG/DXF),
    as an ASYNC JOB.

    Fast validations (type/size/params) run inline — an unsupported file is an
    immediate 415, never a queued failure. The pipeline itself (DXF/SVG
    deterministic parsing, or vision + assumption-first engine for rasters)
    runs on a worker thread: the endpoint returns 202 + ``{job_id, poll}`` and
    the client polls GET /api/drawings/jobs/{id}; the finished job carries the
    same ``{generated, analysis, design, message}`` payload the sync form
    returns. ``sync=true`` (opt-in, scripts/tests) runs inline with status 200."""
    from fastapi.responses import JSONResponse

    from app.services import drawing_jobs
    from app.services.drawing_ingest import UnsupportedDrawingFile, detect_file_type

    if units not in (None, "", "mm", "inch"):
        raise HTTPException(status_code=422, detail="units must be 'mm' or 'inch'")
    if thickness_mm is not None and not 0 < thickness_mm <= 5000:
        raise HTTPException(status_code=422,
                            detail="thickness_mm must be between 0 and 5000")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_DRAWING_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")
    try:
        detect_file_type(data, file.filename, file.content_type)
    except UnsupportedDrawingFile as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    if sync:
        job = drawing_jobs.DrawingJob(id="sync", user_id=user.id)
        return _run_to_cad_pipeline(job, data, file.filename, file.content_type,
                                    notes, units, thickness_mm, family, user.id)

    job = drawing_jobs.create_job(user.id)
    drawing_jobs.run_job(
        job, lambda j: _run_to_cad_pipeline(
            j, data, file.filename, file.content_type, notes, units,
            thickness_mm, family, user.id))
    return JSONResponse(status_code=202, content={
        "job_id": job.id, "status": "queued",
        "poll": f"/api/drawings/jobs/{job.id}",
    })


def _profile_prompt(plan, notes: str | None) -> str:
    exp = plan.expected.bbox_mm or {}
    n = int(plan.expected.hole_count or 0)
    body = (f"[from drawing outline] extruded 2D profile "
            f"{exp.get('x', '?')}×{exp.get('y', '?')}mm, {exp.get('z', '?')}mm deep, "
            + (f"{n} holes" if n else "no holes"))
    if notes:
        body += f". Notes: {notes}"
    return body


def _sheet_texts(ingested, notes: str | None) -> list[str]:
    """Text sources that may carry printed dimension labels (PDF text layer,
    user notes) — used for best-effort scale estimation."""
    return [t for t in (ingested.text_context, notes) if t]


_SLOT_CUT_KINDS = {"rectangular_slot", "rounded_slot", "arc_slot"}
# Cut kinds a plain raster_profile extrusion CANNOT preserve faithfully: it
# approximates every non-circular loop as a circle/rectangle and drops
# concentric groups. When the Sketch IR carries any of these, it must win over
# the profile route (Part A: detected features are a contract).
_RICH_CUT_KINDS = _SLOT_CUT_KINDS | {"polygon_hole", "arbitrary_cutout"}


def _sketch_is_rich(ir) -> bool:
    """A drawing worth the full sketch reconstruction (over a plain contour
    extrusion): concentric groups (counterbores), slots, polygon holes, or
    arbitrary cutouts — features a plain hole-preserving profile extrusion
    cannot represent (an arc slot becomes a rectangle, a hexagon becomes a
    circle, a counterbore becomes a random second hole). Plain round-hole
    profiles stay on the deterministic profile route (object_type
    profile_extrusion) so simple profiles are unchanged."""
    return bool(ir.nested_features) or any(
        c.kind in _RICH_CUT_KINDS for c in ir.cut_features)


_RECESS_KINDS = {"recessed_channel", "blind_recess", "through_channel_cut"}


def _sketch_ir_meaningful_features(ir) -> dict | None:
    """The features a raster_profile candidate would DROP or mangle, or None if
    the IR carries nothing richer than plain circular holes. Used by the route
    guard so a rejection is observable in logs."""
    polygon = sum(1 for c in ir.cut_features if c.kind == "polygon_hole")
    slots = sum(1 for c in ir.cut_features if c.kind in _SLOT_CUT_KINDS)
    arbitrary = sum(1 for c in ir.cut_features if c.kind == "arbitrary_cutout")
    arched = sum(1 for c in ir.cut_features if c.kind == "arched_rectangular_cutout")
    recess = sum(1 for c in ir.cut_features if c.kind in _RECESS_KINDS)
    nested = len(ir.nested_features)
    if not (polygon or slots or arbitrary or arched or recess or nested):
        return None
    return {"polygon_holes": polygon, "slots": slots, "arched": arched,
            "recesses": recess, "arbitrary_cutouts": arbitrary,
            "nested_features": nested}


def _sketch_ir_feature_count(ir) -> int:
    """Total internal mechanical features the Sketch IR carries (holes + groups +
    every non-circular cut + recesses). This is what a plain profile extrusion is
    compared against: if the IR has MORE, the profile route drops features and
    must NOT be selected."""
    circ = sum(1 for c in ir.cut_features if c.kind == "circular_hole")
    other = sum(1 for c in ir.cut_features if c.kind != "circular_hole")
    return circ + other + len(getattr(ir, "concentric_groups", None) or [])


def _raster_feature_count(profile) -> int:
    return len(getattr(profile, "holes", None) or []) if profile is not None else 0


def _measured_holes(design) -> int:
    """Compiler-measured through-hole count on a built design (real subtractive
    ops, not plan metadata)."""
    report = (getattr(design, "semantic_json", None) or {}).get("dimension_report") or {}
    val = (report.get("measured") or {}).get("hole_count")
    return int(val) if isinstance(val, (int, float)) else 0


def _drawing_dimension_evidence(ingested, notes: str | None, interp):
    """Structured dimensional evidence from the sheet text/notes AND the vision
    reading (its Ø callouts / repeated-hole counts), for the silhouette gate."""
    from app.drawing.silhouette_gate import parse_dimension_evidence

    ev = parse_dimension_evidence(_sheet_texts(ingested, notes))
    if interp is not None:
        for h in getattr(interp, "holes", None) or []:
            dia = getattr(h, "diameter", None)
            cnt = int(getattr(h, "count", 1) or 1)
            if dia and float(dia) > 0:
                if cnt >= 2:
                    ev.repeated_diameters.append((cnt, float(dia)))
                else:
                    ev.diameters.append(float(dia))
        for k, v in (getattr(interp, "overall_dimensions", None) or {}).items():
            key = str(k).lower()
            if v and float(v) > 0 and ("diameter" in key or key.endswith("_od")
                                       or "bore" in key or "opening" in key):
                ev.diameters.append(float(v))
    return ev


_LOGO_ROUTES = {"logo_profile_extrusion", "simple_closed_profile_part"}


def _drawing_route_and_mechanical(evidence, *, ir=None, profile=None,
                                  is_pipe_flange: bool = False):
    """(route, is_mechanical). ``is_mechanical`` is True when profile_extrusion
    must NOT win a silhouette: the drawing has engineering callouts OR the Sketch
    IR carries internal mechanical features a plain outline would drop."""
    from app.drawing.silhouette_gate import classify_drawing_route

    dark = bool(ir and any(c.kind in _RECESS_KINDS for c in ir.cut_features))
    internal = _sketch_ir_feature_count(ir) if ir is not None \
        else _raster_feature_count(profile)
    route = classify_drawing_route(
        evidence, is_pipe_flange=is_pipe_flange,
        has_internal_dark_channel=dark, internal_region_count=internal)
    mechanical = route not in _LOGO_ROUTES
    # GEOMETRIC override: a Sketch IR carrying structured features a plain outline
    # extrusion cannot represent (concentric groups/bosses, slots, polygon holes,
    # a recess/channel) is a mechanical drawing even without legible text
    # callouts. NOT triggered by plain round holes alone — raster_profile
    # preserves those, so a simple 2-hole plate stays a profile extrusion.
    if not mechanical and ir is not None:
        if _sketch_ir_meaningful_features(ir) is not None or dark:
            mechanical, route = True, "dimensioned_mechanical_plate"
    return route, mechanical


def _apply_feature_coverage_gate(db: Session, design, ingested, notes, interp,
                                 *, ir=None, profile=None) -> None:
    """Silhouette-only rejection gate (Part 2/7): score the built model against
    the drawing's dimensional evidence; if a DIMENSIONED mechanical drawing was
    reduced to a feature-less silhouette, downgrade fidelity to REVIEW and record
    the missing features. Never blocks a build; never raises."""
    try:
        from app.drawing.silhouette_gate import (
            GeneratedShape,
            evaluate_feature_coverage,
        )

        ev = _drawing_dimension_evidence(ingested, notes, interp)
        dark = bool(ir and any(c.kind in ("recessed_channel", "blind_recess",
                                          "through_channel_cut")
                               for c in ir.cut_features))
        holes = _measured_holes(design)
        if ir is not None:
            circ = sum(1 for c in ir.cut_features if c.kind == "circular_hole")
            groups = len(getattr(ir, "concentric_groups", None) or [])
            distinct = circ + groups
            has_internal = bool(ir.cut_features or groups)
            outer_circle = bool(ir.outer and ir.outer.kind in ("symmetric_plate",)) \
                or (ir.outer is not None and abs(ir.outer.bbox_mm.get("w", 0)
                    - ir.outer.bbox_mm.get("h", 0))
                    <= 0.12 * max(1.0, ir.outer.bbox_mm.get("w", 0)))
            single_disk = outer_circle and not has_internal
        else:
            n = len(getattr(profile, "holes", None) or []) if profile else holes
            distinct = n
            has_internal = n > 0
            single_disk = n == 0
        gen = GeneratedShape(
            hole_count=holes, distinct_circular_features=distinct,
            has_internal_cutout=has_internal,
            has_recess_or_channel=dark, is_single_disk=single_disk)
        verdict = evaluate_feature_coverage(ev, gen, has_internal_dark_channel=dark,
                                            design_id=design.id)
        if not verdict.silhouette_only:
            return
        semantic = dict(design.semantic_json or {"checks": [], "passed": True})
        semantic["drawing_feature_coverage"] = verdict.to_dict()
        semantic.setdefault("checks", [])
        semantic["checks"] = list(semantic["checks"]) + [{
            "name": "drawing_feature_coverage", "passed": False,
            "expected": "engineering features preserved (holes / inner opening / "
                        "repeated pattern / recess)",
            "actual": "silhouette-only: missing " + ", ".join(verdict.missing[:6]),
            "severity": "warning"}]
        _attach_fidelity(design, semantic, {
            "source_drawing_confidence": 0.5,
            "drawing_fidelity_status": "review",
            "used_default_fallback": False})
        design.semantic_json = semantic
        db.commit()
        db.refresh(design)
    except Exception as exc:  # noqa: BLE001 - the gate must never break a build
        log_event("drawing_feature_coverage_gate_failed", reason=type(exc).__name__)


def _route_guard_sketch_over_profile(db: Session, user, ingested, interp,
                                     thickness_mm: float | None, notes: str | None,
                                     stage, is_pipe_flange: bool, profile=None):
    """Route guard: a Sketch IR that carries MORE internal features than the
    raster_profile candidate (polygon holes / slots / concentric groups / a
    larger circular-hole count) must win — profile_extrusion drops those. For a
    dimensioned MECHANICAL drawing the sketch wins even as REVIEW; it is NEVER
    downgraded to a silhouette profile_extrusion PASS. Returns the payload when
    the sketch route wins, or None only for a genuine logo/simple profile."""
    if is_pipe_flange:
        return None  # pipe/flange families have their own deterministic builders
    from app.drawing.vectorize import build_sketch_ir

    ir = build_sketch_ir(
        ingested.image_bytes, dimension_texts=_sheet_texts(ingested, notes),
        thickness_mm=thickness_mm,
        source="vision" if (interp and interp.generatable_with_assumptions())
        else "drawing_fallback")
    if ir is None or not ir.is_usable():
        return None

    evidence = _drawing_dimension_evidence(ingested, notes, interp)
    route, mechanical = _drawing_route_and_mechanical(
        evidence, ir=ir, profile=profile)
    meaningful = _sketch_ir_meaningful_features(ir)
    sketch_count = _sketch_ir_feature_count(ir)
    raster_count = _raster_feature_count(profile)
    richer = sketch_count > raster_count
    log_event("drawing_candidate_ranked", source="sketch_ir", route=route,
              feature_count=sketch_count, raster_features=raster_count,
              mechanical=mechanical, richer_than_raster=richer,
              meaningful=meaningful is not None)
    # profile_extrusion may only win when the sketch is NOT richer AND the drawing
    # is not mechanical — i.e. a genuine logo / simple round-hole profile.
    if meaningful is None and not richer and not mechanical:
        return None
    log_event("drawing_route_rejected", rejected_source="raster_profile",
              reason="sketch_ir_richer_or_mechanical",
              sketch_features=sketch_count, raster_features=raster_count,
              route=route, **(meaningful or {}))
    log_event("drawing_profile_fallback_blocked", route=route,
              reason="richer_sketch_ir_candidate_exists",
              detected_callouts=evidence.distinct_circular_callouts,
              sketch_ir_features=sketch_count, raster_features=raster_count,
              rejected_source="raster_profile")
    sketch = _try_sketch_reconstruction(
        db, user, ingested, interp, thickness_mm, notes, stage,
        require_rich=False, keep_as_review=mechanical or richer)
    if sketch is None:
        # The sketch could not be BUILT at all (not merely rejected). Only a
        # logo/simple profile may then fall back to raster; a mechanical drawing
        # must not become a silhouette PASS.
        log_event("drawing_route_guard_fallback", route=route, mechanical=mechanical,
                  reason="sketch_ir_unbuildable")
        if mechanical:
            log_event("drawing_final_candidate_guard_failed", route=route,
                      reason="mechanical_drawing_no_rich_candidate")
    return sketch


def _keep_rejected_sketch_as_review(db: Session, user, ingested, interp, ir,
                                    design, reason: str, missing: list[str],
                                    keep_as_review: bool):
    """A feature-rich sketch that failed the coverage/contract check is still a
    BETTER answer than a feature-dropped silhouette profile_extrusion PASS. For a
    dimensioned mechanical drawing keep it, mark it REVIEW with the exact missing
    features, and return the payload — never discard it so raster can win.
    Returns None (→ caller discards) only when ``keep_as_review`` is False."""
    if not keep_as_review:
        return None
    from app.drawing.feature_contract import generated_summary_from_ir
    from app.routers.designs import _to_dto
    from app.services import drawing_to_spec

    try:
        analysis = drawing_to_spec.analysis_from_sketch_ir(ir)
        _annotate_from_analysis(db, design, analysis, used_default_fallback=True)
        gen_summary = generated_summary_from_ir(ir)
        _attach_sketch_ir(db, design, ir, gen_summary)
        semantic = dict(design.semantic_json or {"checks": [], "passed": True})
        semantic.setdefault("checks", [])
        semantic["checks"] = list(semantic["checks"]) + [{
            "name": "drawing_feature_reconstruction", "passed": False,
            "expected": "all detected features rebuilt",
            "actual": f"{reason}: missing " + ", ".join(missing[:8]),
            "severity": "warning"}]
        semantic["drawing_reconstruction_review"] = {
            "reason": reason, "missing_features": missing[:16]}
        _attach_fidelity(design, semantic, {
            "source_drawing_confidence": 0.5,
            "drawing_fidelity_status": "review",
            "used_default_fallback": True})
        design.semantic_json = semantic
        db.commit()
        db.refresh(design)
        _save_sketch_debug(design.id, ingested.image_bytes, ir)
    except Exception as exc:  # noqa: BLE001 - keep must never break the response
        log_event("drawing_keep_review_failed", reason=type(exc).__name__)
    log_event("drawing_best_rich_candidate_selected_with_review",
              design_id=design.id, reason=reason, missing=missing[:12])
    return {"generated": True,
            "analysis": drawing_to_spec.analysis_from_sketch_ir(ir).model_dump(mode="json"),
            "design": _to_dto(design, user).model_dump(mode="json"),
            "message": ("Reconstructed the drawing's mechanical features, but some "
                        "could not be fully verified — REVIEW before manufacturing. "
                        "Missing/uncertain: " + ", ".join(missing[:6]))}


def _try_sketch_reconstruction(db: Session, user, ingested, interp,
                               thickness_mm: float | None, notes: str | None,
                               stage, require_rich: bool = True,
                               keep_as_review: bool = False):
    """Reconstruct + build a plate/bracket sketch (outer profile + classified
    cuts + concentric-grouped counterbores). Returns the finished response
    payload, or None to defer.

    ``require_rich=True`` (the early call) only takes over when the CV sketch
    carries STRUCTURED features a plain contour extrusion would get wrong.
    ``require_rich=False`` (the generic-replacement call) builds from ANY usable
    outer profile — this is what replaces the generic-prompt cube for a drawing
    the vision model read only vaguely."""
    from app.drawing.sketch_merge import merge_provider_interpretation_with_cv_ir
    from app.drawing.sketch_to_cad import generate_cad_from_sketch_ir
    from app.drawing.topology import topology_coverage
    from app.drawing.vectorize import build_sketch_ir
    from app.routers.designs import _to_dto
    from app.services import drawing_to_spec

    ir = build_sketch_ir(
        ingested.image_bytes, dimension_texts=_sheet_texts(ingested, notes),
        thickness_mm=thickness_mm,
        source="vision" if (interp and interp.generatable_with_assumptions())
        else "drawing_fallback")
    if ir is None or not ir.is_usable():
        return None
    if require_rich and not _sketch_is_rich(ir):
        return None

    if interp is not None:
        ir = merge_provider_interpretation_with_cv_ir(ir, interp)
    plan = generate_cad_from_sketch_ir(ir, default_thickness_mm=thickness_mm or 6.0)
    if plan is None or not plan.is_buildable():
        return None

    # KEY RECESS GUARD (Part 6): a detected internal dark region must build as a
    # BLIND recess (floor kept), never a through cut. This can't hard-fail the
    # deterministic path (it builds recesses blind by construction) but catches a
    # regression and flags the exact violation.
    from app.drawing.feature_contract import check_recess_integrity

    recess_ok, recess_reason = check_recess_integrity(ir, plan)
    if not recess_ok:
        log_event("drawing_key_recess_guard_failed", reason=recess_reason)

    stage("building_cad")
    try:
        design = design_service.create_design_from_plan(
            db, plan, _sketch_prompt(ir), None,
            "Reconstructed 2D sketch (from drawing)", user.id)
    except CadGenerationError:
        return None  # let the plain-profile / vision routes try
    # TOPOLOGY COVERAGE: the built model must carry the IR's features (a sketch
    # with 5 cuts can never compile to 0 holes). A gross mismatch rejects the
    # design so a downstream route (or a clean failure) takes over — never a
    # silently wrong model.
    cov = topology_coverage(ir, design)
    log_event("drawing_topology_coverage_score", design_id=design.id,
              score=cov.score, expected=cov.expected_cuts, actual=cov.actual_cuts)
    if not cov.acceptable:
        log_event("drawing_generation_rejected_reason", design_id=design.id,
                  reason="topology_coverage", detail=cov.reason)
        kept = _keep_rejected_sketch_as_review(
            db, user, ingested, interp, ir, design, "topology_coverage",
            [cov.reason], keep_as_review)
        if kept is not None:
            return kept
        db.delete(design)
        db.commit()
        return None
    # FEATURE-PRESERVATION CONTRACT (Part B): per-feature-id proof that every
    # detected cut (polygon hole, rectangular cutout, slot, counterbore group)
    # actually reached the built plan — a valid mesh is NOT sufficient. A
    # candidate that dropped required features is rejected so the honest
    # generated=false / repair path takes over instead of a silently-wrong model.
    from app.drawing.feature_contract import (
        enforce_sketch_feature_contract,
        generated_summary_from_ir,
    )

    fcov = enforce_sketch_feature_contract(
        ir, plan, _measured_holes(design), design_id=design.id)
    if not fcov.acceptable:
        log_event("drawing_candidate_rejected", design_id=design.id,
                  reason="missing_required_features", detail=fcov.reason,
                  missing=fcov.missing[:16])
        log_event("drawing_missing_features", design_id=design.id,
                  missing=fcov.missing[:16])
        kept = _keep_rejected_sketch_as_review(
            db, user, ingested, interp, ir, design, fcov.reason,
            fcov.missing, keep_as_review)
        if kept is not None:
            return kept
        db.delete(design)
        db.commit()
        return None
    stage("validating")
    # The contract passed, so every detected feature reached the build: the
    # named/positional summary reported here is the GENERATED feature set.
    gen_summary = generated_summary_from_ir(ir)
    log_event("drawing_candidate_selected", design_id=design.id,
              source="sketch_ir", coverage=fcov.score, **gen_summary)
    analysis = drawing_to_spec.analysis_from_sketch_ir(ir)
    estimated = bool(ir.scale is None or ir.scale.estimated)
    _annotate_from_analysis(db, design, analysis, used_default_fallback=estimated)
    _apply_feature_coverage_gate(db, design, ingested, notes, interp, ir=ir)
    _attach_sketch_ir(db, design, ir, gen_summary)
    _save_sketch_debug(design.id, ingested.image_bytes, ir)
    stage("exporting")
    log_event("drawing_to_cad", file_type=ingested.file_type, source="sketch_ir",
              design_id=design.id, require_rich=require_rich, **ir.feature_summary())
    message = None
    if estimated:
        message = ("Reconstructed the drawing's features (outer profile, holes, "
                   "slots, counterbores). Some dimensions were estimated — REVIEW "
                   "before manufacturing.")
    return {"generated": True, "analysis": analysis.model_dump(mode="json"),
            "design": _to_dto(design, user).model_dump(mode="json"),
            "message": message}


def _sketch_prompt(ir) -> str:
    s = ir.feature_summary()
    outer = (ir.outer.kind.replace("_", " ") if ir.outer else "plate")
    bb = ir.outer.bbox_mm if ir.outer else {}
    body = (f"[from drawing] reconstructed 2D mechanical sketch: {outer} "
            f"{bb.get('w', '?')}×{bb.get('h', '?')}mm, "
            f"{ir.default_thickness_mm:g}mm thick, with {s['through_holes']} "
            f"through features ({s['counterbores']} counterbores)")
    return body + ". All dimensions in mm."


def _attach_sketch_ir(db: Session, design, ir, generated_summary: dict | None = None) -> None:
    """Store the reconstructed sketch summary + IR on the design (metadata +
    debug). ``generated_summary`` is the named/positional generated-feature
    summary (present once the feature contract has accepted the candidate)."""
    semantic = dict(design.semantic_json or {})
    semantic["sketch_ir"] = {"summary": ir.feature_summary(),
                             "generated_feature_summary": generated_summary,
                             "ir": ir.to_dict()}
    if generated_summary is not None:
        semantic["drawing_generated_feature_summary"] = generated_summary
    design.semantic_json = semantic
    db.commit()
    db.refresh(design)


def _sketch_debug_dir(design_id: str):
    from pathlib import Path

    from app.config import settings

    return Path(settings.eval_report_dir) / "drawing_debug" / design_id


def _save_sketch_debug(design_id: str, image_bytes: bytes, ir) -> None:
    """Dev-only: write the colour-coded overlay + IR JSON so a developer can see
    what was detected/ignored. Best-effort; never blocks generation."""
    from app.config import settings

    if not settings.dev_mode:
        return
    try:
        import json

        from app.drawing.debug_overlay import render_debug_overlay

        d = _sketch_debug_dir(design_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "sketch_ir.json").write_text(json.dumps(ir.to_dict(), indent=2))
        overlay = render_debug_overlay(image_bytes, ir)
        if overlay:
            (d / "overlay.png").write_bytes(overlay)
        log_event("drawing_debug_saved", design_id=design_id)
    except Exception as exc:  # noqa: BLE001 - debug artifact never blocks
        log_event("drawing_debug_save_failed", reason=type(exc).__name__)


def _deterministic_fallback_response(db: Session, user: User, ingested, interp,
                                     thickness_mm: float | None,
                                     notes: str | None, stage):
    """Best-effort deterministic generation after a provider failure/weak read.

    Returns the finished response payload (generated=True, REVIEW-flagged) or
    None when no usable linework was found / the kernel failed — the caller
    then returns the honest generated=false payload."""
    from app.config import settings
    from app.routers.designs import _to_dto
    from app.services import drawing_best_effort, drawing_to_spec

    if not settings.drawing_enable_deterministic_fallback:
        return None
    if ingested.image_bytes is None:
        return None
    err = (interp.provider_error if interp is not None else None) or ""
    log_event("drawing_fallback_started", provider_error=bool(err),
              file_type=ingested.file_type)
    stage("fallback_generating")
    outcome = drawing_best_effort.generate_best_effort_from_drawing(
        ingested.image_bytes, provider_result=interp,
        thickness_mm=thickness_mm,
        dimension_texts=_sheet_texts(ingested, notes))
    if outcome is None:
        log_event("drawing_fallback_warning", reason="no_usable_linework")
        return None
    log_event("drawing_fallback_route", route=outcome.route)

    design = None
    analysis = None
    try:
        if outcome.plan is not None:
            stage("building_cad")
            design = design_service.create_design_from_plan(
                db, outcome.plan, _profile_prompt(outcome.plan, notes), None,
                outcome.plan.name, user.id)
            analysis = drawing_to_spec.analysis_from_profile(
                outcome.profile, outcome.plan, outcome.confidence)
        elif outcome.interp is not None:
            design = _generate_from_interpretation(db, outcome.interp, user,
                                                   on_stage=stage)
            analysis = drawing_to_spec.analysis_from_interpretation(
                outcome.interp, thickness_mm=thickness_mm, notes=notes)
    except (CadGenerationError, HTTPException) as exc:
        log_event("drawing_fallback_warning", reason="build_failed",
                  detail=str(getattr(exc, "detail", exc))[:200])
        return None
    if design is None:
        return None
    stage("validating")
    if analysis is not None:
        seen = set(analysis.assumptions)
        analysis.assumptions.extend(
            n for n in outcome.notes + outcome.warnings if n not in seen)
        _annotate_from_analysis(db, design, analysis, used_default_fallback=True)
    stage("exporting")
    timed_out = any(t in err.lower()
                    for t in ("timeout", "timed out", "took too long", "unavailable"))
    message = ("Generated best-effort CAD from drawing geometry. Some dimensions "
               "were estimated because "
               + ("provider interpretation timed out."
                  if timed_out else
                  "the drawing could not be fully read automatically."))
    log_event("drawing_fallback_generated", design_id=design.id,
              route=outcome.route)
    log_event("drawing_to_cad", file_type=ingested.file_type, source="fallback",
              route=outcome.route, design_id=design.id)
    return {"generated": True,
            "analysis": analysis.model_dump(mode="json") if analysis else None,
            "design": _to_dto(design, user).model_dump(mode="json"),
            "message": message}


def _vector_prompt(analysis, notes: str | None) -> str:
    """Human-readable provenance prompt for a vector-built design (stored on the
    design; also drives the feature audit's keyword matching)."""
    p = analysis.outer_profile
    if p.kind == "circle" and p.diameter_mm:
        shape = f"Ø{p.diameter_mm:g}mm circular part"
    else:
        shape = f"{p.width_mm:g}×{p.height_mm:g}mm plate" if p.width_mm and p.height_mm \
            else "part"
    fam = (analysis.recommended_family or "part").replace("_", " ")
    body = (f"[from {analysis.source.upper()} drawing] {fam}: {shape}, "
            f"{analysis.inferred_depth_mm:g}mm thick")
    n = analysis.features.hole_count()
    if n:
        body += f", {n} holes"
    if notes:
        body += f". Notes: {notes}"
    return body


def _combined_hint(notes: str | None, pdf_text: str | None, family: str | None,
                   units: str | None, thickness_mm: float | None) -> str | None:
    parts = []
    if notes:
        parts.append(notes)
    if family:
        parts.append(f"The part is a {family}.")
    if units == "inch":
        parts.append("Dimensions on the drawing are in inches.")
    if thickness_mm:
        parts.append(f"The part thickness/depth is {thickness_mm:g}mm.")
    if pdf_text:
        parts.append(f"Text extracted from the PDF: {pdf_text[:1500]}")
    return " ".join(parts) or None


def _apply_user_overrides(interp: DrawingInterpretationSpec, units: str | None,
                          thickness_mm: float | None, family: str | None) -> None:
    """User-supplied facts beat the vision output — they are ground truth."""
    from app.schemas.drawing_spec import normalize_drawing_type

    if family:
        forced = normalize_drawing_type(family)
        if forced:
            interp.suggested_object_type = forced
            interp.unsupported_reason = None
            interp.overall_confidence = max(interp.overall_confidence, 0.6)
    if units in ("mm", "inch"):
        interp.units = units
        interp.drawing_units_confidence = 1.0
    if thickness_mm:
        interp.overall_dimensions["thickness"] = thickness_mm


def _fidelity_report(confidence: float, *, provider_error: bool = False,
                     used_default_fallback: bool = False) -> dict:
    """Drawing-fidelity classification stored on every drawing-built design.

    * ok      — the drawing was read confidently and drove the geometry;
    * review  — generated, but from weakened evidence (low confidence, provider
                trouble, hint classification, assumed scale) — never PASS;
    * failed  — the drawing was effectively not read — export is blocked.
    """
    if confidence < GENERATE_WITH_ASSUMPTIONS_CONFIDENCE:
        status = "failed"
    elif confidence < CONFIDENCE_THRESHOLD or provider_error or used_default_fallback:
        status = "review"
    else:
        status = "ok"
    return {
        "source_drawing_confidence": round(float(confidence), 3),
        "drawing_fidelity_status": status,
        "used_default_fallback": bool(used_default_fallback),
    }


def _force_review(db: Session, design, reason: str) -> None:
    """Downgrade a built design to REVIEW (never a silent PASS) with a warning.
    Used by the final silhouette guard. Best-effort; never raises."""
    try:
        semantic = dict(design.semantic_json or {"checks": [], "passed": True})
        semantic.setdefault("checks", [])
        semantic["checks"] = list(semantic["checks"]) + [{
            "name": "drawing_final_candidate_guard", "passed": False,
            "expected": "feature-rich mechanical reconstruction",
            "actual": reason, "severity": "warning"}]
        _attach_fidelity(design, semantic, {
            "source_drawing_confidence": 0.5,
            "drawing_fidelity_status": "review", "used_default_fallback": True})
        design.semantic_json = semantic
        db.commit()
        db.refresh(design)
    except Exception as exc:  # noqa: BLE001
        log_event("drawing_force_review_failed", reason=type(exc).__name__)


_FIDELITY_RANK = {"ok": 0, "review": 1, "failed": 2}


def _attach_fidelity(design, semantic: dict, fidelity: dict) -> None:
    """Store the fidelity block; a FAILED fidelity is a production-blocking
    validation failure (export gated), REVIEW is downgraded from PASS by
    reconciled_validation_status.

    Annotation may run more than once on a design (interpretation pass +
    analysis pass) — the verdict only ever gets STRICTER: keep the worse
    status, OR the fallback flag, and the lower confidence."""
    prev = semantic.get("drawing_fidelity")
    if prev:
        fidelity = {
            "source_drawing_confidence": min(
                prev.get("source_drawing_confidence", 1.0),
                fidelity["source_drawing_confidence"]),
            "drawing_fidelity_status": max(
                prev.get("drawing_fidelity_status", "ok"),
                fidelity["drawing_fidelity_status"],
                key=lambda s: _FIDELITY_RANK.get(s, 1)),
            "used_default_fallback": bool(prev.get("used_default_fallback"))
            or fidelity["used_default_fallback"],
        }
    semantic["drawing_fidelity"] = fidelity
    if fidelity["drawing_fidelity_status"] == "failed":
        report = semantic.setdefault("dimension_report", {})
        validation = report.setdefault("validation", {})
        crit = list(validation.get("critical_failures") or [])
        crit.append("The source drawing could not be read confidently enough to "
                    "trust this model — re-upload a clearer drawing.")
        validation["critical_failures"] = crit
        validation["status"] = "critical_failure"


def _annotate_from_analysis(db: Session, design, analysis,
                            used_default_fallback: bool = False) -> None:
    """Carry the analysis' assumptions/ambiguities onto the design as visible,
    non-blocking metadata (same contract as _annotate_from_drawing)."""
    if design.clarification_question:
        return
    seen = set(design.assumptions or [])
    design.assumptions = (design.assumptions or []) + [
        a for a in analysis.assumptions if a not in seen]
    semantic = dict(design.semantic_json or {"checks": [], "passed": True})
    semantic.setdefault("checks", [])
    semantic["checks"] = list(semantic["checks"]) + [
        {"name": "drawing_ambiguity", "passed": False,
         "expected": None, "actual": amb, "severity": "warning"}
        for amb in analysis.ambiguities
    ]
    semantic["drawing_analysis"] = {
        "source": analysis.source,
        "family": analysis.recommended_family,
        "confidence": analysis.confidence_score,
        "hole_count": analysis.features.hole_count(),
    }
    _attach_fidelity(design, semantic, _fidelity_report(
        analysis.confidence_score, used_default_fallback=used_default_fallback))
    design.semantic_json = semantic
    db.commit()
    db.refresh(design)


def _generate_from_interpretation(db: Session, interp: DrawingInterpretationSpec,
                                  user: User, on_stage=None, image_hash=None):
    """Shared confirm/generate path with HARD drawing-mode acceptance.
    ``on_stage(name)`` (optional) reports coarse progress to the async job UI.

    In drawing mode the required-feature audit gates the result: the planner's
    model (which already gets one internal repair pass on a failed audit) must
    pass, otherwise we REBUILD from the deterministic fallback built on the
    structured drawing data. Only if that also fails do we refuse — a wrong
    model is never shown as a drawing's "success"."""
    stage = on_stage or (lambda s: None)
    scaled = infer_scale(interp)
    prompt = _drawing_to_prompt(interp, scaled)
    try:
        stage("building_cad")
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
        # ROUTE LOCK (Part E): a drawing detected as a KNOWN deterministic
        # family (flanged pipe branch / tee) is built ONLY by its deterministic
        # builder. It must NEVER fall through to the LLM cad_plan path — that was
        # the source of the stacked GPT-5.5/5.1 timeouts → GPT-4.1 invalid
        # geometry → critical_failure. If the deterministic build fails we
        # REPAIR the deterministic builder (sanitized spec); we do not call the
        # LLM for geometry topology.
        if design is None and interp.suggested_object_type in \
                KNOWN_DETERMINISTIC_DRAWING_ROUTES:
            log_event("drawing_route_lock", route_lock=interp.suggested_object_type,
                      source="drawing")
            design = _build_locked_deterministic(db, interp, scaled, prompt, user,
                                                 image_hash=image_hash)
        if design is None:
            # DETERMINISTIC-FIRST: a drawing recognized as a supported family is
            # built directly from the structured drawing data — the LLM cad_plan
            # step is SKIPPED entirely (it was the source of stacked timeouts).
            plan = _deterministic_drawing_plan(interp, scaled, prompt)
            if plan is not None:
                try:
                    design = design_service.create_design_from_plan(
                        db, plan, prompt, None,
                        interp.title or interp.suggested_object_type, user.id)
                    design.route_reason = (
                        "Recognized drawing family — built deterministically from "
                        "the drawing dimensions (no LLM planning step).")
                    db.commit()
                    log_event("drawing_deterministic_build",
                              design_id=design.id, object_type=plan.object_type)
                except CadGenerationError:
                    design = None  # fall through to the assumption-first engine
        if design is None:
            # Feature graph (generic parts and any partially specified drawing —
            # generates with assumptions, never blocks). A drawing recognized as
            # a pipe/flange part carries a route lock so the generic prompt can
            # never be hijacked into a wheel/rim/tire family ("flange rim
            # diameter" → Wheel rim was the field bug).
            from app.schemas.drawing_spec import is_pipe_flange_detection

            route_lock = ("pipe_flange" if is_pipe_flange_detection(
                interp.suggested_object_type, interp.detected_object_type)
                else None)
            design = design_service.create_design(
                db, prompt, None,
                interp.title or interp.suggested_object_type, user.id,
                route_lock=route_lock,
            )
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    stage("validating")
    _require_drawing_accuracy(db, design, interp, scaled, prompt)
    _annotate_from_drawing(db, design, interp, scaled)
    _annotate_pipe_branch(db, design)
    return design


def _annotate_pipe_branch(db: Session, design) -> None:
    """Record explicit side-branch topology metadata on a flanged-branch/tee
    design (queryable by the UI/tests) so a branch is never silently reported as
    a straight spool. Derived from the BUILT features, not just intent."""
    if design.object_type not in ("flanged_pipe_branch", "pipe_tee"):
        return
    feats = {f.get("id"): f for f in (design.features_json or [])}
    flanges = [fid for fid in ("top_flange", "bottom_flange", "branch_flange")
               if fid in feats]
    branch = feats.get("branch_pipe") or {}
    detail = {
        "family": design.object_type,
        "side_branch_present": "branch_pipe" in feats,
        "branch_flange_present": "branch_flange" in feats,
        "flange_count": len(flanges),
        "pipe_axis_count": sum(1 for fid in ("main_pipe", "branch_pipe") if fid in feats),
        "flanges": flanges,
    }
    bb = design.bounding_box or {}
    if bb:
        detail["branch_reach_mm"] = round(float(bb.get("x") or 0), 2)
    semantic = dict(design.semantic_json or {})
    semantic["pipe_branch_detail"] = detail
    design.semantic_json = semantic
    db.commit()
    db.refresh(design)


def _deterministic_drawing_plan(interp: DrawingInterpretationSpec,
                                scaled: ScaledDrawing, prompt: str):
    """Buildable CadPlan for a drawing recognized as a supported family, or
    None when the family needs the LLM engine.

    flanged_pipe_branch / pipe_tee use the dedicated parametric builder
    (structured drawing spec → union-then-bore geometry); other recognized
    families use the offline deterministic planner on the synthesized
    full-geometry prompt. Clarification plans return None — drawing mode is
    assumption-first, so the caller's engine (which fills gaps with defaults)
    takes over instead of blocking."""
    from app.cad.pipe_branch import PIPE_BRANCH_TYPES
    from app.drawing.fallback import (
        plan_from_spec,
        plan_pipe_spool_from_interp,
        spec_from_interpretation,
    )

    if interp.suggested_object_type in PIPE_BRANCH_TYPES:
        return plan_from_spec(spec_from_interpretation(interp, scaled))
    # A straight flanged pipe spool builds structurally from the drawing dims
    # (the prose planner too often clarifies on drawing-synthesized prompts).
    if interp.suggested_object_type == "pipe_spool":
        try:
            return plan_pipe_spool_from_interp(interp, scaled)
        except Exception:  # noqa: BLE001 - fall through to the prose planner
            pass
    from app.cad.plan import deterministic

    plan = deterministic.plan(prompt.lower())
    if plan is not None and plan.is_buildable():
        return plan
    return None


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
    _attach_fidelity(design, semantic, _fidelity_report(
        interp.overall_confidence,
        provider_error=bool(interp.provider_error),
        # Anything not read off the drawing itself (text classification,
        # assumed scale, user-stated notes) is REVIEW at best, never PASS.
        used_default_fallback=interp.interp_source in ("hint", "raster_assumed",
                                                       "notes")))
    design.semantic_json = semantic
    if design.route == "cad_plan" and \
            "deterministically" not in (design.route_reason or ""):
        design.route_reason = "Drawing → feature-graph CAD (interpreted 2D drawing)."
    db.commit()
    db.refresh(design)


# Drawing families with a trusted deterministic builder. A drawing detected as
# one of these is route-locked: it is built ONLY by that builder and can never
# fall through to the LLM cad_plan path (Part E). The pipe branch/tee is the
# family that was timing out through the LLM.
KNOWN_DETERMINISTIC_DRAWING_ROUTES = {"flanged_pipe_branch", "pipe_tee"}

_CUTOUT_WORDS = ("cutout", "cut out", "cut-out", "notch", "slot", "window",
                 "keyway", "pocket", "recess")


def _mentions_cutout(notes: str | None, interp) -> bool:
    """True when the notes or vision reading explicitly NAME a cutout/slot — the
    only case where a planar cut on a round pipe wall is intentional."""
    parts = [notes or ""]
    if interp is not None:
        parts += [interp.title or "", interp.detected_object_type or "",
                  interp.suggested_object_type or ""]
        for a in getattr(interp, "assumptions", None) or []:
            parts.append(getattr(a, "assumption", "") if not isinstance(a, dict)
                         else a.get("assumption", ""))
    hay = " ".join(parts).lower()
    return any(w in hay for w in _CUTOUT_WORDS)


def _build_locked_deterministic(db: Session, interp: DrawingInterpretationSpec,
                                scaled: ScaledDrawing, prompt: str, user: User,
                                image_hash: str | None = None):
    """Build a route-locked deterministic family (pipe branch/tee), REPAIRING the
    deterministic builder on failure instead of ever calling the LLM.

    Dimensions first go through the DETERMINISTIC scale resolver (same image →
    same size, with a family plausibility floor so a unit-ambiguous Ø14.8-flange/
    12-bolt reading is scaled to a Ø120 envelope rather than built as degenerate
    geometry). Then:

    Attempt 1: the resolved drawing spec.
    Attempt 2 (repair): a topology-sound spec with any drifted/degenerate
      dimensions clamped to plausible proportions — a valid REVIEW solid always
      beats a critical_failure or an LLM guess.
    Raises HTTPException only when a topologically-correct pipe branch genuinely
    cannot be built (never falls back to the LLM)."""
    from app.cad.pipe_branch import build_plan
    from app.drawing.dimension_scale import resolve_pipe_branch_scale
    from app.drawing.fallback import spec_from_interpretation, to_pipe_branch_spec

    name = interp.title or interp.suggested_object_type
    raw_spec = spec_from_interpretation(interp, scaled)
    # Deterministic scale resolution. Dimensions are "confident" when they came
    # from legible drawing labels — a ×10 drawing-scale UNIT conversion still
    # counts as confident; only INFERRED/ASSUMED proportions or a defaulted
    # envelope make them uncertain (same keyword contract as the REVIEW gate).
    parsed_confident = not any(
        kw in a.lower() for a in raw_spec.assumptions
        for kw in ("inferred", "assumed", "estimated", "envelope", "default"))
    drawing_spec, resolved = resolve_pipe_branch_scale(
        raw_spec, image_hash=image_hash, parsed_confident=parsed_confident)
    scale_estimated = resolved.estimated
    attempts = [("as_read", to_pipe_branch_spec(drawing_spec)),
                ("repaired", _repair_pipe_branch_spec(
                    to_pipe_branch_spec(drawing_spec)))]
    last_err: Exception | None = None
    graded_fallback = None  # a compiled-but-critical design, kept as last resort
    # A cutout is only honored on a round pipe wall when the drawing/notes NAME
    # one; otherwise a rectangular notch is a mis-read of a section/centre line.
    from app.drawing.feature_normalizer import strip_unbacked_pipe_wall_cutouts
    cutout_named = _mentions_cutout(prompt, interp)
    for label, pbspec in attempts:
        try:
            plan = build_plan(pbspec)
            strip_unbacked_pipe_wall_cutouts(plan, has_cutout_callout=cutout_named)
        except Exception as exc:  # noqa: BLE001 - spec math guard
            last_err = exc
            continue
        try:
            design = design_service.create_design_from_plan(
                db, plan, prompt, None, name, user.id)
        except CadGenerationError as exc:
            last_err = exc
            log_event("drawing_pipe_branch_build_failed", attempt=label,
                      detail=str(exc)[:200])
            continue
        design.route_reason = (
            "Detected flanged pipe branch drawing — built deterministically by "
            "the route-locked family builder (no LLM planning step).")
        # PART G: a deterministic family built from INFERRED/ASSUMED dimensions
        # (pipe branches almost always infer wall / PCD / flange thickness /
        # branch length) is REVIEW, never a clean PASS — even on a confident
        # vision read. Only a fully-parsed drawing may PASS.
        estimated = label == "repaired" or scale_estimated or any(
            w in a.lower() for a in drawing_spec.assumptions
            for w in ("inferred", "assumed", "estimated", "envelope", "default"))
        _mark_drawing_estimated_fidelity(db, design, interp, estimated)
        db.commit()
        log_event("drawing_deterministic_family_build", design_id=design.id,
                  attempt=label, object_type=plan.object_type,
                  estimated_dimensions=estimated,
                  status=design_service.reconciled_validation_status(design))
        if label == "repaired":
            log_event("drawing_candidate_repair_started",
                      family="flanged_pipe_branch", reason="initial_build_unfaithful")
        # A valid (non-critical) solid wins immediately; a critical_failure one
        # is kept only as a last resort so the repair attempt can supersede it.
        if design_service.reconciled_validation_status(design) != "critical_failure":
            log_event("drawing_candidate_selected", design_id=design.id,
                      source="deterministic_family_builder",
                      family="flanged_pipe_branch", attempt=label,
                      repeatability_key=resolved.repeatability_key,
                      estimated_dimensions=estimated)
            return design
        if graded_fallback is not None and graded_fallback.id != design.id:
            db.delete(graded_fallback)
            db.commit()
        graded_fallback = design
    if graded_fallback is not None:
        return graded_fallback  # compiled but unfaithful — REVIEW/critical, not LLM
    # No deterministic attempt COMPILED — a clean, honest failure. Still no LLM:
    # the family is route-locked.
    log_event("drawing_route_locked_build_unrecoverable",
              detail=str(last_err)[:200] if last_err else None)
    raise HTTPException(
        status_code=422,
        detail=("Detected a flanged pipe branch but could not build a valid "
                "solid from the drawing's dimensions even after repair. "
                "Re-upload a clearer drawing or state the pipe/flange/bolt "
                "dimensions in the notes."))


def _mark_drawing_estimated_fidelity(db: Session, design,
                                     interp: DrawingInterpretationSpec,
                                     estimated: bool) -> None:
    """Persist a drawing-fidelity block so an estimated-dimension drawing build
    is REVIEW (never a clean PASS). ``_attach_fidelity`` only ever tightens the
    verdict, so a later confident annotation can't upgrade this back to PASS."""
    semantic = dict(design.semantic_json or {"checks": [], "passed": True})
    _attach_fidelity(design, semantic, _fidelity_report(
        interp.overall_confidence,
        provider_error=bool(interp.provider_error),
        used_default_fallback=estimated))
    design.semantic_json = semantic
    db.commit()
    db.refresh(design)


def _repair_pipe_branch_spec(spec):
    """Clamp a pipe-branch spec to a topologically-buildable one: bores strictly
    inside their walls, flanges larger than their pipes, non-degenerate lengths.
    Keeps the drawing's proportions where they are already valid."""
    import dataclasses

    s = dataclasses.replace(spec)
    s.main_od = max(4.0, s.main_od)
    s.branch_od = max(3.0, min(s.branch_od or s.main_od * 0.6, s.main_od))
    # Bore must leave a real wall (>= ~8% of OD, min 0.5mm) on both pipes.
    s.main_id = _clamp_bore(s.main_id, s.main_od)
    s.branch_id = _clamp_bore(s.branch_id, s.branch_od)
    s.main_len = max(s.main_od, s.main_len or s.main_od * 2)
    s.branch_len = max(s.main_od / 2 + 5.0, s.branch_len or s.main_od)
    s.flange_od = max(s.main_od + 4.0, s.flange_od or s.main_od + 40)
    s.branch_flange_od = max(s.branch_od + 4.0,
                             s.branch_flange_od or s.branch_od * 1.4)
    s.flange_thk = max(1.0, s.flange_thk or 8.0)
    s.branch_flange_thk = max(1.0, s.branch_flange_thk or s.flange_thk)
    s.bolt_count = max(4, int(s.bolt_count or 8))
    s.bolt_dia = max(0.5, min(s.bolt_dia or 4.0, s.flange_od / 6))
    # PCDs clamped between the bore and the rim so bolt holes land in metal.
    s.pcd = _clamp_pcd(s.pcd, s.flange_id or s.main_id, s.flange_od, s.bolt_dia)
    s.branch_pcd = _clamp_pcd(s.branch_pcd, s.branch_id, s.branch_flange_od,
                              s.bolt_dia)
    return s


def _clamp_bore(bore: float, od: float) -> float:
    max_bore = od - 2 * max(0.5, od * 0.08)
    if not bore or bore <= 0 or bore >= max_bore:
        return round(max(0.5, max_bore), 2)
    return bore


def _clamp_pcd(pcd: float, bore: float, od: float, bolt_dia: float) -> float:
    lo = (bore or 0) + bolt_dia + 1.0
    hi = od - bolt_dia - 1.0
    if hi <= lo:
        return round((od + (bore or 0)) / 2, 1)
    if not pcd or pcd < lo or pcd > hi:
        return round(od - 2.5 * bolt_dia, 1) if lo <= od - 2.5 * bolt_dia <= hi \
            else round((lo + hi) / 2, 1)
    return pcd


# Part families whose hole callouts describe a per-flange bolt circle.
_FLANGED_FAMILIES = {"flanged_pipe_branch", "pipe_tee", "pipe_spool",
                     "blind_flange", "flange", "pipe_fitting", "pipe_elbow"}
# Pipe/flange families a side-branch drawing may be RE-LABELED from: vision
# reads a branch's circular flanges as a straight spool / single flange, so when
# side-branch topology is detected we promote these to flanged_pipe_branch.
_PIPE_FLANGE_SPOOL_TYPES = {"pipe_spool", "blind_flange", "flange",
                            "pipe_fitting", "generic_mechanical_part"}


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
