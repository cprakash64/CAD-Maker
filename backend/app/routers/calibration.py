"""Physical calibration API (docs/calibration.md).

Profile CRUD + measurement entry/import + review/activation workflow, plus
generation of the ten deterministic calibration coupons through the SAME
validated export path every other part uses (app.export.exporter.generate,
via app.services.design_service.create_design_from_spec).

Every route is owner-scoped except comparison and coupon-type listing, which
are read-only, cross-account reference data (a profile is visible to the
account that created it; nothing here leaks another user's raw samples).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.cad.base import CadGenerationError
from app.cad.families import all_families
from app.database import get_db
from app.models import User
from app.rate_limit import rate_limit
from app.schemas.design_spec import DesignSpec
from app.services import calibration_service as svc
from app.services import design_service

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


# --------------------------------------------------------------------- I/O models

class ProfileFields(BaseModel):
    model_config = {"extra": "forbid"}

    label: Optional[str] = Field(default=None, max_length=200)
    printer: Optional[str] = Field(default=None, max_length=128)
    firmware: Optional[str] = Field(default=None, max_length=128)
    nozzle_mm: Optional[float] = Field(default=None, gt=0, le=2.0)
    material_type: Optional[str] = Field(default=None, max_length=32)
    material_brand: Optional[str] = Field(default=None, max_length=128)
    material_product: Optional[str] = Field(default=None, max_length=128)
    material_color: Optional[str] = Field(default=None, max_length=64)
    slicer: Optional[str] = Field(default=None, max_length=64)
    slicer_version: Optional[str] = Field(default=None, max_length=32)
    line_width_mm: Optional[float] = Field(default=None, gt=0, le=2.0)
    layer_height_mm: Optional[float] = Field(default=None, gt=0, le=1.0)
    nozzle_temp_c: Optional[float] = Field(default=None, gt=0, le=500)
    bed_temp_c: Optional[float] = Field(default=None, ge=0, le=200)
    flow_pct: Optional[float] = Field(default=None, gt=0, le=200)
    wall_count: Optional[int] = Field(default=None, ge=1, le=20)
    cooling_pct: Optional[float] = Field(default=None, ge=0, le=100)
    test_date: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=4000)


class CreateProfileRequest(ProfileFields):
    source_type: str = Field(max_length=32)


class AddMeasurementRequest(BaseModel):
    model_config = {"extra": "forbid"}

    measurement_type: str = Field(max_length=32)
    feature: str = Field(min_length=1, max_length=128)
    fit_class: Optional[str] = Field(default=None, max_length=16)
    nominal_mm: Optional[float] = None
    raw_samples_mm: list[float] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=2000)


class ImportCsvRequest(BaseModel):
    model_config = {"extra": "forbid"}

    csv_text: str = Field(min_length=1, max_length=200_000)


class RejectRequest(BaseModel):
    model_config = {"extra": "forbid"}

    reason: str = Field(min_length=1, max_length=2000)


class GenerateCouponRequest(BaseModel):
    model_config = {"extra": "forbid"}

    dimensions: dict[str, float] = Field(default_factory=dict)
    name: Optional[str] = Field(default=None, max_length=200)
    project_id: Optional[str] = Field(default=None, max_length=32)
    # Printer/material context for coupons whose geometry is calibration-
    # profile-driven (currently calibration_mechanism_coupon). When given,
    # a matching active, VALIDATED profile's measured clearance is used
    # instead of the generic estimate — the one real, DB-backed profile
    # lookup in the generation path (docs/calibration.md).
    printer: Optional[str] = Field(default=None, max_length=128)
    material_type: Optional[str] = Field(default=None, max_length=32)
    nozzle_mm: Optional[float] = Field(default=None, gt=0, le=2.0)


def _err(exc: svc.CalibrationError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _owned_or_404(db: Session, profile_id: str, user: User):
    row = svc.get_profile(db, profile_id)
    if row is None or row.created_by_user_id not in (user.id, None):
        raise HTTPException(status_code=404, detail="Calibration profile not found")
    return row


def _profile_dto(row) -> dict:
    return svc.to_pydantic(row).model_dump(mode="json")


# --------------------------------------------------------------------- profiles

@router.post("/profiles", dependencies=[rate_limit("create")])
def create_profile(
    req: CreateProfileRequest, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        row = svc.create_profile(
            db, user_id=user.id, source_type=req.source_type,
            fields=req.model_dump(exclude={"source_type"}, exclude_none=True))
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return _profile_dto(row)


@router.get("/profiles", dependencies=[rate_limit("read")])
def list_profiles(
    printer: Optional[str] = None, material_type: Optional[str] = None,
    status_filter: Optional[str] = None, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    rows = svc.list_profiles(db, printer=printer, material_type=material_type,
                             status=status_filter)
    return [_profile_dto(r) for r in rows if r.created_by_user_id in (user.id, None)]


@router.get("/profiles/{profile_id}", dependencies=[rate_limit("read")])
def get_profile(
    profile_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    row = _owned_or_404(db, profile_id, user)
    return _profile_dto(row)


@router.patch("/profiles/{profile_id}", dependencies=[rate_limit("create")])
def update_profile(
    profile_id: str, req: ProfileFields, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        row = svc.update_profile(db, profile_id, req.model_dump(exclude_none=True))
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return _profile_dto(row)


@router.post("/profiles/{profile_id}/fork", dependencies=[rate_limit("create")])
def fork_profile(
    profile_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        row = svc.fork_profile(db, profile_id, user_id=user.id)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return _profile_dto(row)


@router.delete("/profiles/{profile_id}", dependencies=[rate_limit("create")])
def delete_profile(
    profile_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        svc.delete_profile(db, profile_id)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return {"deleted": True}


# ----------------------------------------------------------------- measurements

@router.post("/profiles/{profile_id}/measurements", dependencies=[rate_limit("create")])
def add_measurement(
    profile_id: str, req: AddMeasurementRequest, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        row = svc.add_measurement(
            db, profile_id, measurement_type=req.measurement_type, feature=req.feature,
            fit_class=req.fit_class, nominal_mm=req.nominal_mm,
            raw_samples_mm=req.raw_samples_mm, notes=req.notes)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return {"id": row.id, "profile_id": row.profile_id,
           "measurement_type": row.measurement_type, "feature": row.feature,
           "fit_class": row.fit_class, "nominal_mm": row.nominal_mm,
           "raw_samples_mm": row.raw_samples_mm, "sample_count": row.sample_count,
           "median_mm": row.median_mm, "range_mm": row.range_mm,
           "stddev_mm": row.stddev_mm, "confidence": row.confidence, "notes": row.notes}


@router.post("/profiles/{profile_id}/measurements/import-csv",
             dependencies=[rate_limit("create")])
def import_measurements_csv(
    profile_id: str, req: ImportCsvRequest, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        rows = svc.import_measurements_csv(db, profile_id, req.csv_text)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return {"imported": len(rows), "measurement_ids": [r.id for r in rows]}


@router.post("/profiles/{profile_id}/measurements/import-csv-file",
             dependencies=[rate_limit("create")])
async def import_measurements_csv_file(
    profile_id: str, file: UploadFile, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    data = await file.read()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must be UTF-8 text") from exc
    try:
        rows = svc.import_measurements_csv(db, profile_id, text)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return {"imported": len(rows), "measurement_ids": [r.id for r in rows]}


@router.delete("/profiles/{profile_id}/measurements/{measurement_id}",
               dependencies=[rate_limit("create")])
def delete_measurement(
    profile_id: str, measurement_id: str, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        svc.delete_measurement(db, profile_id, measurement_id)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return {"deleted": True}


# ------------------------------------------------------------- review/activate

@router.post("/profiles/{profile_id}/review", dependencies=[rate_limit("create")])
def review_and_validate(
    profile_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        row = svc.review_and_validate(db, profile_id, reviewer_user_id=user.id)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return _profile_dto(row)


@router.post("/profiles/{profile_id}/reject", dependencies=[rate_limit("create")])
def reject_profile(
    profile_id: str, req: RejectRequest, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    row = svc.reject_profile(db, profile_id, reviewer_user_id=user.id, reason=req.reason)
    return _profile_dto(row)


@router.post("/profiles/{profile_id}/activate", dependencies=[rate_limit("create")])
def activate_profile(
    profile_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    try:
        row = svc.activate_profile(db, profile_id)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc
    return _profile_dto(row)


@router.post("/profiles/{profile_id}/deactivate", dependencies=[rate_limit("create")])
def deactivate_profile(
    profile_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    _owned_or_404(db, profile_id, user)
    row = svc.deactivate_profile(db, profile_id)
    return _profile_dto(row)


# --------------------------------------------------------------------- compare

@router.get("/compare", dependencies=[rate_limit("read")])
def compare_profiles(
    ids: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    """``ids`` is a comma-separated list of profile ids."""
    profile_ids = [p.strip() for p in ids.split(",") if p.strip()]
    if not profile_ids:
        raise HTTPException(status_code=400, detail="ids must list at least one profile id")
    for pid in profile_ids:
        _owned_or_404(db, pid, user)
    try:
        return svc.compare_profiles(db, profile_ids)
    except svc.CalibrationError as exc:
        raise _err(exc) from exc


# --------------------------------------------------------------------- coupons

_COUPON_FAMILY_PREFIX = "calibration_"


@router.get("/coupons", dependencies=[rate_limit("read")])
def list_coupon_types(user: User = Depends(get_current_user)) -> list[dict]:
    return [
        {"object_type": fam.object_types[0], "display_name": fam.display_name,
         "description": next(iter(fam.default_assumptions), "")}
        for fam in all_families()
        if fam.family_id.startswith(_COUPON_FAMILY_PREFIX)
    ]


def _resolve_mechanism_coupon_clearance(
    db: Session, dimensions: dict, *, printer: str | None, material_type: str | None,
    nozzle_mm: float | None,
) -> tuple[dict, dict | None]:
    """The one real, DB-backed profile lookup in the generation path: unless
    the caller already gave an explicit bore_clearance_override, resolve it
    from an active VALIDATED profile matching (printer, material, nozzle) —
    falling back to the generic estimate, same as everywhere else, when none
    matches. Returns (dimensions with the override filled in, provenance)."""
    from app.schemas.calibration import CalibrationMeasurementType, FitClass

    if dimensions.get("bore_clearance_override"):
        return dimensions, None  # caller gave an explicit value; leave it alone
    resolved = svc.resolve_for_generation(
        db, measurement_type=CalibrationMeasurementType.diametral_clearance,
        feature="holder_case_clearance", fit_class=FitClass.normal,
        printer=printer, material_type=material_type, nozzle_mm=nozzle_mm)
    return {**dimensions, "bore_clearance_override": resolved.value_mm}, {
        "value_mm": resolved.value_mm, "is_estimate": resolved.is_estimate,
        "provenance": resolved.provenance,
    }


@router.post("/coupons/{coupon_type}/generate", dependencies=[rate_limit("create")])
def generate_coupon(
    coupon_type: str, req: GenerateCouponRequest, db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not coupon_type.startswith(_COUPON_FAMILY_PREFIX):
        raise HTTPException(status_code=404, detail=f"Unknown coupon type '{coupon_type}'")

    dimensions = req.dimensions
    resolution = None
    if coupon_type == "calibration_mechanism_coupon":
        dimensions, resolution = _resolve_mechanism_coupon_clearance(
            db, dimensions, printer=req.printer, material_type=req.material_type,
            nozzle_mm=req.nozzle_mm)

    try:
        spec = DesignSpec(object_type=coupon_type, dimensions=dimensions)
    except Exception as exc:  # noqa: BLE001 - a bad request, not a crash
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        design = design_service.create_design_from_spec(
            db, spec, prompt=f"[calibration coupon] {coupon_type}",
            user_id=user.id, name=req.name or coupon_type.replace("_", " "))
    except CadGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if resolution is not None:
        note = (
            f"Bore clearance {resolution['value_mm']:+g}mm from "
            + ("a generic estimate (not physically measured)." if resolution["is_estimate"]
               else f"validated calibration profile "
                    f"'{resolution['provenance']['label']}'.")
        )
        design.assumptions = (design.assumptions or []) + [note]
        db.commit()
        db.refresh(design)

    from app.routers.designs import _to_dto

    return _to_dto(design, user).model_dump(mode="json")
