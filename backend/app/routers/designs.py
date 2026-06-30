"""Design API routes (all owner-scoped).

Every route requires authentication and only ever touches designs that belong
to the current user's projects. Non-owned ids return 404 (no existence leak).

POST /api/designs/create          — prompt -> spec -> geometry (or clarification)
POST /api/designs/{id}/regenerate — deterministic rebuild from edited params
POST /api/designs/{id}/modify     — plain-English edit -> DesignModification
POST /api/designs/{id}/export     — (re)materialize export files
POST /api/designs/{id}/checks     — re-run manufacturability checks
GET  /api/designs/{id}/files/{fmt}— owner-checked download (local stream / S3 redirect)
POST /api/designs/{id}/feedback   — thumbs up/down + categories + comment
GET  /api/designs/{id}/feedback   — this user's feedback for the design
GET  /api/designs/{id}            — full design
GET  /api/designs                 — list this user's designs
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from app.auth.deps import get_current_user
from app.cad.base import CadGenerationError
from app.config import settings
from app.database import get_db
from app.drawing import STANDARD_VIEWS
from app.drawing.render import render_view
from app.editing.localized import (
    UnsupportedLocalizedEdit,
    apply_localized,
    apply_localized_request,
)
from app.manufacturability.checks import run_checks
from app.models import Design, ExportFile, Feedback, Project, User
from app.rate_limit import rate_limit
from app.schemas.editing_spec import LocalizedEditRequest, LocalizedModificationSpec
from app.services.package_service import build_package_zip
from app.schemas.api import (
    CheckDTO,
    CreateDesignRequest,
    DesignDTO,
    DesignSummaryDTO,
    ExportDTO,
    FeedbackDTO,
    FeedbackRequest,
    ModifyRequest,
    PreviewMeshDTO,
    RegenerateRequest,
)
from app.schemas.design_spec import DesignSpec
from app.services import design_service
from app.storage.storage import StorageError, get_storage

router = APIRouter(prefix="/api/designs", tags=["designs"])


def _feedback_dto(fb: Feedback) -> FeedbackDTO:
    return FeedbackDTO(
        id=fb.id,
        design_id=fb.design_id,
        rating=fb.rating,
        categories=fb.categories or [],
        comment=fb.comment,
        created_at=fb.created_at.isoformat(),
    )


def _dim_validation(design: Design) -> dict:
    """The dimension report's validation block ({status, critical_failures,
    warnings}), or an empty dict when no report exists."""
    report = (design.semantic_json or {}).get("dimension_report") or {}
    return report.get("validation") or {}


def _to_dto(design: Design, user: User) -> DesignDTO:
    spec = DesignSpec(**design.spec_json) if design.spec_json else None
    preview = PreviewMeshDTO(**design.preview_json) if design.preview_json else None
    editable = design_service._editable_parameters(spec) if spec else {}
    mine = next((f for f in design.feedback if f.user_id == user.id), None)
    return DesignDTO(
        id=design.id,
        project_id=design.project_id,
        prompt=design.prompt,
        object_type=design.object_type,
        title=design_service._display_title(design),
        spec=spec,
        assumptions=design.assumptions or [],
        explanation=design.explanation,
        clarification_question=design.clarification_question,
        needs_clarification=spec is None and design.clarification_question is not None,
        preview=preview,
        bounding_box_mm=design.bounding_box,
        spec_hash=design.spec_hash,
        exports=[
            ExportDTO(fmt=e.fmt, url=e.url, size_bytes=e.size_bytes)
            for e in design.exports
        ],
        checks=[
            CheckDTO(check=c.check, severity=c.severity, passed=c.passed, message=c.message)
            for c in design.checks
        ],
        editable_parameters=editable,
        provider=design.provider,
        generation_ms=design.generation_ms,
        created_at=design.created_at.isoformat(),
        updated_at=design.updated_at.isoformat(),
        my_feedback=_feedback_dto(mine) if mine else None,
        features=design.features_json or [],
        default_assumptions=[
            a for a in (design.assumptions or []) if a.startswith("Used sensible defaults")
        ],
        can_generate_with_defaults=bool(design.can_generate_with_defaults),
        missing_required=design.missing_required or [],
        # CadPlan clarifications are full-sentence questions stored in
        # missing_required; surface them as a list for the UI.
        clarification_questions=(
            design.missing_required or [] if design.route == "cad_plan" else []
        ),
        feature_graph_ops=[
            o.get("op")
            for o in (((design.spec_json or {}).get("feature_graph") or {}).get("operations") or [])
            if o.get("op")
        ],
        route=design.route,
        route_reason=design.route_reason,
        auto_repaired=bool(design.auto_repaired),
        export_formats=design.export_formats or ["stl", "step"],
        semantic_checks=(design.semantic_json or {}).get("checks", []),
        semantic_passed=(design.semantic_json or {}).get("passed"),
        repair_attempts=int(design.repair_attempts or 0),
        has_program=bool(design.program_code),
        warnings=[
            (f"{c['name'].replace('_', ' ')}: expected {c['expected']}, got {c['actual']}"
             if c.get("expected") is not None
             else (f"{c['name'].replace('_', ' ')}: {c['actual']}" if c.get("actual")
                   else c["name"].replace("_", " ")))
            for c in (design.semantic_json or {}).get("checks", [])
            if not c.get("passed") and c.get("severity") == "warning"
        ],
        feature_audit=((design.semantic_json or {}).get("feature_audit") or {}).get("items", []),
        feature_audit_passed=((design.semantic_json or {}).get("feature_audit") or {}).get("passed"),
        dimension_report=(design.semantic_json or {}).get("dimension_report"),
        print_readiness=((design.semantic_json or {}).get("dimension_report") or {}).get("print_readiness"),
        dimensions_within_tolerance=((design.semantic_json or {}).get("dimension_report") or {}).get("within_tolerance"),
        validation_status=design_service.reconciled_validation_status(design),
        validation_critical_failures=_dim_validation(design).get("critical_failures", []),
        validation_warnings=_dim_validation(design).get("warnings", []),
        recovery_attempted=bool(design_service.recovery_info(design).get("attempted")),
        recovery_strategy=design_service.recovery_info(design).get("strategy"),
        recovery_succeeded=bool(design_service.recovery_info(design).get("succeeded")),
        download_blocked_reason=(
            design_service.DOWNLOAD_BLOCKED_MESSAGE
            if design_service.is_critical_failure(design) else None
        ),
        needs_decomposition=design.route == "needs_decomposition",
        decomposition=(design.semantic_json or {}).get("decomposition"),
        design_mode=(design.semantic_json or {}).get("design_mode"),
        classification=(design.semantic_json or {}).get("classification"),
        understanding=(design.semantic_json or {}).get("understanding"),
        generation_outcome=((design.semantic_json or {}).get("contract") or {}).get("outcome"),
        clarification_options=(design.semantic_json or {}).get("clarification_options") or [],
        telemetry=(design.semantic_json or {}).get("telemetry"),
        gear_debug=(design.semantic_json or {}).get("gear_debug"),
        hex_debug=(design.semantic_json or {}).get("hex_debug"),
        standard_part=(design.semantic_json or {}).get("standard_part"),
        part_family_contract=(design.semantic_json or {}).get("part_family_contract"),
        part_family_detail=(design.semantic_json or {}).get("part_family_detail"),
        device_enclosure_validation=(design.semantic_json or {}).get("device_enclosure_validation"),
        object_intelligence=(design.semantic_json or {}).get("object_intelligence"),
        presentation=design_service.presentation_descriptor(design),
    )


def _owned_or_404(db: Session, design_id: str, user: User) -> Design:
    """Fetch a design only if it belongs to the current user, else 404."""
    design = db.get(Design, design_id)
    if design is None or not design_service.user_owns_design(db, design, user.id):
        raise HTTPException(status_code=404, detail="Design not found")
    return design


def _block_export_if_critical(design: Design, allow_failed: bool = False) -> None:
    """Refuse to hand out a manufacturable file for a critical-failure design.

    The design stays fully inspectable (GET /{id}, preview, drawing views); only
    the STEP/STL/package exports are gated. A dev-only override (`?allow_failed=
    true`, honored solely when DEV_MODE is on) lets engineers pull the broken file
    for debugging — never available in staging/production."""
    if not design_service.is_critical_failure(design):
        return
    if allow_failed and settings.dev_mode:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=design_service.DOWNLOAD_BLOCKED_MESSAGE,
    )


@router.post("/create", response_model=DesignDTO, dependencies=[rate_limit("create")])
def create_design(
    req: CreateDesignRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    try:
        design = design_service.create_design(
            db, req.prompt, req.project_id, req.name, user.id
        )
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_dto(design, user)


@router.post("/{design_id}/regenerate", response_model=DesignDTO,
             dependencies=[rate_limit("regenerate")])
def regenerate_design(
    design_id: str,
    req: RegenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    design = _owned_or_404(db, design_id, user)
    try:
        design = design_service.regenerate_design(
            db,
            design,
            dimensions=req.dimensions,
            holes=req.holes,
            fillet_radius=req.fillet_radius,
            manufacturing_method=req.manufacturing_method,
            material=req.material,
        )
    except (CadGenerationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_dto(design, user)


@router.post("/{design_id}/modify", response_model=DesignDTO,
             dependencies=[rate_limit("modify")])
def modify_design(
    design_id: str,
    req: ModifyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    design = _owned_or_404(db, design_id, user)
    try:
        design, clarification = design_service.modify_design(db, design, req.prompt)
    except (CadGenerationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dto = _to_dto(design, user)
    if clarification:
        dto.clarification_question = clarification
    return dto


@router.post("/{design_id}/export", response_model=DesignDTO,
             dependencies=[rate_limit("package")])
def export_design(
    design_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    design = _owned_or_404(db, design_id, user)
    if not design.spec_json:
        raise HTTPException(status_code=409, detail="Nothing to export yet")
    _block_export_if_critical(design)
    if not design.exports:
        design_service._regenerate_geometry(db, design, DesignSpec(**design.spec_json))
        db.commit()
        db.refresh(design)
    return _to_dto(design, user)


@router.get("/{design_id}/files/{fmt}")
def download_file(
    design_id: str,
    fmt: str,
    allow_failed: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Owner-checked download. Streams from local storage or redirects to a
    short-lived S3 presigned URL. Private files are never publicly addressable.

    Blocked (409) for critical-failure designs so a failed result is never handed
    out as a manufacturable file."""
    design = _owned_or_404(db, design_id, user)
    _block_export_if_critical(design, allow_failed)
    export = next((e for e in design.exports if e.fmt == fmt), None)
    if export is None:
        raise HTTPException(status_code=404, detail="No such export")
    design_service.log_design_telemetry(
        design, "design_exported", export_clicked=True, export_format=fmt,
        export_kind=design_service.presentation_descriptor(design)["export_kind"])
    storage = get_storage()
    signed = storage.signed_url(export.storage_key)
    if signed:
        return RedirectResponse(signed)
    try:
        data = storage.read(export.storage_key)
    except StorageError as exc:
        raise HTTPException(status_code=404, detail="File missing") from exc
    media = "model/stl" if fmt == "stl" else "application/step"
    filename = f"{design.object_type or 'part'}.{fmt}"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{design_id}/views/{view}", dependencies=[rate_limit("package")])
def get_view(
    design_id: str,
    view: str,
    fmt: str = "png",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Render a drawing view (top/front/right/left/iso) from the actual model."""
    if view not in STANDARD_VIEWS:
        raise HTTPException(status_code=404, detail=f"Unknown view '{view}'")
    if fmt not in ("png", "svg"):
        raise HTTPException(status_code=400, detail="fmt must be png or svg")
    design = _owned_or_404(db, design_id, user)
    if not design.spec_json:
        raise HTTPException(status_code=409, detail="No model to draw yet")
    try:
        data = render_view(DesignSpec(**design.spec_json), view, fmt)
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    media = "image/png" if fmt == "png" else "image/svg+xml"
    return Response(content=data, media_type=media)


@router.get("/{design_id}/package", dependencies=[rate_limit("package")])
def download_package(
    design_id: str,
    allow_failed: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download a complete CAD package (STEP, STL, spec, report, drawings) as ZIP.

    Blocked (409) for critical-failure designs (it bundles manufacturable files)."""
    design = _owned_or_404(db, design_id, user)
    _block_export_if_critical(design, allow_failed)
    base = design.object_type or "part"
    if design.spec_json:
        try:
            data = build_package_zip(DesignSpec(**design.spec_json), design.id)
        except CadGenerationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        # CadPlan feature-graph parts and concept assemblies have no DesignSpec —
        # bundle their stored STEP/STL plus metadata, BOM/cut-list and caveat README.
        import json as _json

        from app.services.package_service import (
            assembly_cut_list_csv, assembly_plate_list_csv, build_files_package,
        )

        storage = get_storage()
        files: dict[str, bytes] = {}
        for e in design.exports:
            try:
                files[e.fmt] = storage.read(e.storage_key)
            except StorageError:
                continue
        if not files:
            raise HTTPException(status_code=409, detail="Nothing to package yet")
        report = (design.semantic_json or {}).get("dimension_report") or {}
        components = report.get("components") or []
        snap = report.get("snapshot") or {}
        rmeas = report.get("measured") or {}
        metadata = {
            "object_type": base,
            "route": design.route,
            "design_mode": (design.semantic_json or {}).get("design_mode", "single_part"),
            "style": rmeas.get("chassis_style") or snap.get("chassis_style"),
            "envelope_mm": (report.get("requested") or {}).get("envelope_mm"),
            "bounding_box_mm": design.bounding_box,
            "tube_outer_diameter_mm": (report.get("spec") or {}).get("tube_outer_diameter_mm"),
            "tube_wall_thickness_mm": (report.get("spec") or {}).get("tube_wall_thickness_mm"),
            "tube_count": rmeas.get("tube_count"),
            "plate_count": rmeas.get("plate_count"),
            "component_count": len(components),
            "hole_feature_count": rmeas.get("hole_feature_count"),
            "slot_feature_count": rmeas.get("slot_feature_count"),
            "symmetry_pairs": snap.get("symmetry_pairs"),
            "assumptions": design.assumptions or [],
            "spec": report.get("spec"),
            "recommended_material": report.get("recommended_material"),
            "validation": report.get("validation"),
            "zones": report.get("zones") or report.get("sections"),
            "systems": report.get("systems"),
        }
        is_assembly = (design.semantic_json or {}).get("design_mode") == "assembly"
        extra: dict[str, str] = {}
        if is_assembly:
            extra["assembly_metadata.json"] = _json.dumps(metadata, indent=2, default=str)
            extra["component_list.json"] = _json.dumps(components, indent=2, default=str)
            extra["tube_cut_list.csv"] = assembly_cut_list_csv(components)
            extra["plate_list.csv"] = assembly_plate_list_csv(components)
        readme = (
            "CAD Maker — CAD Package\n=======================\n"
            f"Part: {base}\n\n"
            "Contents: STEP + STL solids and metadata.json"
            + (", assembly_metadata.json, component_list.json, tube_cut_list.csv, "
               "plate_list.csv" if is_assembly else "")
            + ".\n\n"
            + ("NOTE: Detailed concept CAD only. Not FEA analyzed. Not structurally "
               "certified. Requires engineering review before fabrication. Tubes are "
               "exported as solid cylinders; use tube_cut_list.csv for OD / wall / "
               "cut-length fabrication data.\n"
               if is_assembly else "")
        )
        data = build_files_package(base, files, metadata, readme, extra)
    name = f"{base}_package.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/{design_id}/localized-edit", response_model=DesignDTO,
             dependencies=[rate_limit("modify")])
def localized_edit(
    design_id: str,
    mod: LocalizedModificationSpec,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    """Modify only the selected feature via a constrained, validated operation.

    If the operation can't be applied to the selection, the geometry is left
    unchanged and a useful explanation is returned in clarification_question.
    """
    design = _owned_or_404(db, design_id, user)
    if not design.spec_json:
        raise HTTPException(status_code=409, detail="No model to edit yet")
    current = DesignSpec(**design.spec_json)
    try:
        new_spec, message = apply_localized(current, mod)
    except UnsupportedLocalizedEdit as exc:
        dto = _to_dto(design, user)
        dto.clarification_question = str(exc)
        return dto
    try:
        design = design_service.apply_spec_edit(db, design, new_spec, note=message)
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_dto(design, user)


@router.post("/{design_id}/generate-with-defaults", response_model=DesignDTO,
             dependencies=[rate_limit("create")])
def generate_with_defaults(
    design_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    """Build a clarification design from its defaults-filled candidate spec."""
    design = _owned_or_404(db, design_id, user)
    if not design.can_generate_with_defaults or not design.clarified_spec_candidate:
        raise HTTPException(
            status_code=409, detail="This design can't be generated with defaults."
        )
    spec = DesignSpec(**design.clarified_spec_candidate)
    try:
        design.clarification_question = None
        design = design_service.apply_spec_edit(db, design, spec, note="Generated with defaults")
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_dto(design, user)


@router.post("/{design_id}/circle-edit", response_model=DesignDTO,
             dependencies=[rate_limit("modify")])
def circle_edit(
    design_id: str,
    req: LocalizedEditRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    """Circle-to-edit: apply an edit to a feature resolved from a circle/lasso
    selection. Validates the feature id; unsupported selections explain why and
    leave geometry unchanged."""
    design = _owned_or_404(db, design_id, user)
    if not design.spec_json:
        raise HTTPException(status_code=409, detail="No model to edit yet")
    current = DesignSpec(**design.spec_json)
    new_spec, result = apply_localized_request(current, req, design.bounding_box)
    if new_spec is None:
        dto = _to_dto(design, user)
        dto.clarification_question = result.message
        return dto
    try:
        design = design_service.apply_spec_edit(db, design, new_spec, note=result.message)
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_dto(design, user)


@router.post("/{design_id}/checks", response_model=list[CheckDTO])
def run_design_checks(
    design_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CheckDTO]:
    design = _owned_or_404(db, design_id, user)
    if not design.spec_json:
        raise HTTPException(status_code=409, detail="No spec to check")
    spec = DesignSpec(**design.spec_json)
    return [
        CheckDTO(check=c.check, severity=c.severity.value, passed=c.passed, message=c.message)
        for c in run_checks(spec)
    ]


@router.post("/{design_id}/feedback", response_model=FeedbackDTO)
def submit_feedback(
    design_id: str,
    req: FeedbackRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FeedbackDTO:
    design = _owned_or_404(db, design_id, user)
    fb = design_service.add_feedback(
        db, design, user.id, req.rating, req.categories, req.comment
    )
    return _feedback_dto(fb)


@router.get("/{design_id}/feedback", response_model=Optional[FeedbackDTO])
def get_feedback(
    design_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FeedbackDTO | None:
    design = _owned_or_404(db, design_id, user)
    mine = next((f for f in design.feedback if f.user_id == user.id), None)
    return _feedback_dto(mine) if mine else None


@router.get("/{design_id}", response_model=DesignDTO)
def get_design(
    design_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DesignDTO:
    return _to_dto(_owned_or_404(db, design_id, user), user)


@router.get("", response_model=list[DesignSummaryDTO])
def list_designs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[DesignSummaryDTO]:
    """Lightweight design list for this user (newest first).

    Performance: the response is a SUMMARY only — it never carries the heavy
    per-design payload (mesh preview, semantic/dimension report, feature graph,
    program code). Those JSON columns are DEFERRED at the query level so a long
    history doesn't drag megabytes of geometry through the list endpoint, and
    ``export_ready`` is resolved in ONE query instead of a per-row lazy load (the
    old N+1 that made this endpoint take 6–14s). Paginated via limit/offset.
    """
    # Load only the light columns needed for a summary; defer the big JSON blobs.
    designs = db.scalars(
        select(Design)
        .options(load_only(
            Design.id, Design.project_id, Design.prompt, Design.object_type,
            Design.route, Design.spec_json, Design.clarification_question,
            Design.created_at, Design.updated_at,
        ))
        .join(Project, Design.project_id == Project.id)
        .where(Project.user_id == user.id)
        .order_by(Design.updated_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    # Resolve export readiness for the whole page in a single query (no N+1).
    ids = [d.id for d in designs]
    with_exports: set[str] = set()
    if ids:
        with_exports = set(db.scalars(
            select(ExportFile.design_id)
            .where(ExportFile.design_id.in_(ids))
            .distinct()
        ).all())

    return [
        DesignSummaryDTO(
            id=d.id,
            project_id=d.project_id,
            prompt=d.prompt,
            object_type=d.object_type,
            title=design_service._display_title(d),
            created_at=d.created_at.isoformat(),
            updated_at=d.updated_at.isoformat(),
            needs_clarification=d.spec_json is None
            and d.clarification_question is not None,
            export_ready=d.id in with_exports,
        )
        for d in designs
    ]
