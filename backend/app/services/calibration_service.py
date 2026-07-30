"""Calibration profile data workflow: CRUD, manual/CSV measurement entry,
review/activation gating, provenance-aware resolution, and comparison.

This is the ONLY place that writes app.models.CalibrationProfile/
CalibrationMeasurement rows — callers (the router, other services) always go
through here so the "never mark validated without review", "raw samples are
the source of truth for statistics", and "only one active profile per
printer/material/nozzle" invariants are enforced in exactly one place.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models import CalibrationMeasurement as MeasurementRow
from app.models import CalibrationProfile as ProfileRow
from app.schemas.calibration import (
    CalibrationMeasurement,
    CalibrationMeasurementType,
    CalibrationProfile,
    CalibrationProfileStatus,
    CalibrationSourceType,
    FitClass,
)


class CalibrationError(Exception):
    """Raised for a calibration-workflow rule violation (never a bare 500)."""


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------- convert

def _measurement_to_pydantic(row: MeasurementRow) -> CalibrationMeasurement:
    return CalibrationMeasurement(
        measurement_type=row.measurement_type,
        feature=row.feature,
        fit_class=row.fit_class,
        nominal_mm=row.nominal_mm,
        raw_samples_mm=list(row.raw_samples_mm or []),
        sample_count=row.sample_count,
        median_mm=row.median_mm,
        range_mm=row.range_mm,
        stddev_mm=row.stddev_mm,
        confidence=row.confidence,
        notes=row.notes or "",
    )


def to_pydantic(row: ProfileRow) -> CalibrationProfile:
    """Convert a DB profile (+ its measurements) into the validated,
    versioned Pydantic shape used by the resolver and the API response."""
    return CalibrationProfile(
        id=row.id,
        version=row.version,
        label=row.label,
        source_type=row.source_type,
        status=row.status,
        printer=row.printer,
        firmware=row.firmware,
        nozzle_mm=row.nozzle_mm,
        material_type=row.material_type,
        material_brand=row.material_brand,
        material_product=row.material_product,
        material_color=row.material_color,
        slicer=row.slicer,
        slicer_version=row.slicer_version,
        line_width_mm=row.line_width_mm,
        layer_height_mm=row.layer_height_mm,
        nozzle_temp_c=row.nozzle_temp_c,
        bed_temp_c=row.bed_temp_c,
        flow_pct=row.flow_pct,
        wall_count=row.wall_count,
        cooling_pct=row.cooling_pct,
        test_date=row.test_date,
        measurements=[_measurement_to_pydantic(m) for m in row.measurements],
        notes=row.notes or "",
        created_by_user_id=row.created_by_user_id,
        reviewed=row.reviewed,
        reviewed_by_user_id=row.reviewed_by_user_id,
        reviewed_at=row.reviewed_at,
        active=row.active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


_PROCESS_FIELDS = (
    "label", "printer", "firmware", "nozzle_mm", "material_type", "material_brand",
    "material_product", "material_color", "slicer", "slicer_version", "line_width_mm",
    "layer_height_mm", "nozzle_temp_c", "bed_temp_c", "flow_pct", "wall_count",
    "cooling_pct", "test_date", "notes",
)


# --------------------------------------------------------------------- CRUD

def create_profile(db: Session, *, user_id: str, source_type: str, fields: dict) -> ProfileRow:
    try:
        CalibrationSourceType(source_type)
    except ValueError as exc:
        raise CalibrationError(f"unknown source_type '{source_type}'") from exc
    row = ProfileRow(
        id=_uuid(), created_by_user_id=user_id, version=1,
        label=fields.get("label") or "Untitled calibration profile",
        source_type=source_type, status=CalibrationProfileStatus.draft.value,
    )
    for f in _PROCESS_FIELDS:
        if f in fields and fields[f] is not None:
            setattr(row, f, fields[f])
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_profile(db: Session, profile_id: str) -> ProfileRow | None:
    return db.get(ProfileRow, profile_id)


def list_profiles(
    db: Session, *, printer: str | None = None, material_type: str | None = None,
    status: str | None = None, source_type: str | None = None,
) -> list[ProfileRow]:
    q = db.query(ProfileRow)
    if printer:
        q = q.filter(ProfileRow.printer == printer)
    if material_type:
        q = q.filter(ProfileRow.material_type == material_type)
    if status:
        q = q.filter(ProfileRow.status == status)
    if source_type:
        q = q.filter(ProfileRow.source_type == source_type)
    return q.order_by(ProfileRow.created_at.desc()).all()


def update_profile(db: Session, profile_id: str, fields: dict) -> ProfileRow:
    row = get_profile(db, profile_id)
    if row is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    if row.status == CalibrationProfileStatus.validated.value:
        raise CalibrationError(
            "a validated profile is immutable — its numbers must not drift out "
            "from under a design that already used them; create a new draft "
            "version instead (see fork_profile)")
    for f in _PROCESS_FIELDS:
        if f in fields and fields[f] is not None:
            setattr(row, f, fields[f])
    db.commit()
    db.refresh(row)
    return row


def fork_profile(db: Session, profile_id: str, *, user_id: str) -> ProfileRow:
    """Create a new DRAFT version derived from an existing (typically
    validated) profile — the only way to 'edit' a validated profile."""
    src = get_profile(db, profile_id)
    if src is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    row = ProfileRow(
        id=_uuid(), created_by_user_id=user_id, derived_from_id=src.id,
        version=src.version + 1, label=src.label, source_type=src.source_type,
        status=CalibrationProfileStatus.draft.value,
    )
    for f in _PROCESS_FIELDS:
        setattr(row, f, getattr(src, f))
    db.add(row)
    db.flush()
    for m in src.measurements:
        db.add(MeasurementRow(
            id=_uuid(), profile_id=row.id, measurement_type=m.measurement_type,
            feature=m.feature, fit_class=m.fit_class, nominal_mm=m.nominal_mm,
            raw_samples_mm=list(m.raw_samples_mm or []), sample_count=m.sample_count,
            median_mm=m.median_mm, range_mm=m.range_mm, stddev_mm=m.stddev_mm,
            confidence=m.confidence, notes=m.notes))
    db.commit()
    db.refresh(row)
    return row


def delete_profile(db: Session, profile_id: str) -> None:
    row = get_profile(db, profile_id)
    if row is None:
        return
    if row.active:
        raise CalibrationError("cannot delete an active profile — deactivate it first")
    db.delete(row)
    db.commit()


# --------------------------------------------------------------- measurements

def _validate_measurement_fields(
    measurement_type: str, feature: str, fit_class: str | None, nominal_mm: float | None,
    raw_samples_mm: list[float],
) -> CalibrationMeasurement:
    """Route every measurement write through the Pydantic model's own
    validators (type-specific requirements, finite-sample check) so a bad
    write is rejected with the same rules the schema documents, not a
    second, drifting copy of them."""
    try:
        m = CalibrationMeasurement(
            measurement_type=measurement_type, feature=feature, fit_class=fit_class,
            nominal_mm=nominal_mm, raw_samples_mm=list(raw_samples_mm or []),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean 422 by the router
        raise CalibrationError(str(exc)) from exc
    m.recompute_statistics()
    return m


def add_measurement(
    db: Session, profile_id: str, *, measurement_type: str, feature: str,
    fit_class: str | None, nominal_mm: float | None, raw_samples_mm: list[float],
    notes: str = "",
) -> MeasurementRow:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    if profile.status == CalibrationProfileStatus.validated.value:
        raise CalibrationError(
            "cannot add measurements to a validated profile — fork it first")
    m = _validate_measurement_fields(
        measurement_type, feature, fit_class, nominal_mm, raw_samples_mm)
    row = MeasurementRow(
        id=_uuid(), profile_id=profile_id, measurement_type=m.measurement_type.value,
        feature=m.feature, fit_class=m.fit_class.value if m.fit_class else None,
        nominal_mm=m.nominal_mm, raw_samples_mm=list(m.raw_samples_mm),
        sample_count=m.sample_count, median_mm=m.median_mm, range_mm=m.range_mm,
        stddev_mm=m.stddev_mm, confidence=m.confidence, notes=notes or m.notes)
    db.add(row)
    # A profile whose measurements changed is no longer implicitly reviewed.
    profile.reviewed = False
    db.commit()
    db.refresh(row)
    return row


_CSV_REQUIRED_COLUMNS = {"measurement_type", "feature", "samples"}


def import_measurements_csv(db: Session, profile_id: str, csv_text: str) -> list[MeasurementRow]:
    """Structured-data import. Expected columns (header row required):
    ``measurement_type, feature, fit_class, nominal_mm, samples, notes`` —
    ``samples`` is a semicolon-separated list of raw mm values, e.g.
    ``-0.03;-0.025;-0.028``. ``fit_class``/``nominal_mm``/``notes`` may be
    blank. Every row is validated the same way a manual entry is (see
    add_measurement) — a malformed row rejects the whole import with a
    precise error rather than silently skipping or half-applying it."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise CalibrationError("CSV has no header row")
    missing = _CSV_REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing:
        raise CalibrationError(f"CSV is missing required column(s): {sorted(missing)}")

    parsed: list[dict] = []
    for i, raw_row in enumerate(reader, start=2):  # header is line 1
        samples_text = (raw_row.get("samples") or "").strip()
        try:
            samples = [float(x) for x in samples_text.replace(",", ";").split(";") if x.strip()]
        except ValueError as exc:
            raise CalibrationError(f"CSV row {i}: could not parse samples '{samples_text}'") from exc
        nominal = raw_row.get("nominal_mm") or ""
        parsed.append({
            "measurement_type": (raw_row.get("measurement_type") or "").strip(),
            "feature": (raw_row.get("feature") or "").strip(),
            "fit_class": (raw_row.get("fit_class") or "").strip() or None,
            "nominal_mm": float(nominal) if nominal.strip() else None,
            "raw_samples_mm": samples,
            "notes": (raw_row.get("notes") or "").strip(),
        })

    # Validate everything BEFORE writing anything — an import is all-or-nothing.
    profile = get_profile(db, profile_id)
    if profile is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    if profile.status == CalibrationProfileStatus.validated.value:
        raise CalibrationError(
            "cannot import measurements into a validated profile — fork it first")
    for i, p in enumerate(parsed, start=2):
        try:
            _validate_measurement_fields(
                p["measurement_type"], p["feature"], p["fit_class"], p["nominal_mm"],
                p["raw_samples_mm"])
        except CalibrationError as exc:
            raise CalibrationError(f"CSV row {i}: {exc}") from exc

    rows = [
        add_measurement(
            db, profile_id, measurement_type=p["measurement_type"], feature=p["feature"],
            fit_class=p["fit_class"], nominal_mm=p["nominal_mm"],
            raw_samples_mm=p["raw_samples_mm"], notes=p["notes"])
        for p in parsed
    ]
    return rows


def delete_measurement(db: Session, profile_id: str, measurement_id: str) -> None:
    profile = get_profile(db, profile_id)
    if profile is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    if profile.status == CalibrationProfileStatus.validated.value:
        raise CalibrationError(
            "cannot remove measurements from a validated profile — fork it first")
    row = db.get(MeasurementRow, measurement_id)
    if row is None or row.profile_id != profile_id:
        raise CalibrationError(f"measurement '{measurement_id}' not found on this profile")
    db.delete(row)
    db.commit()


# ------------------------------------------------------------- review/activate

def review_and_validate(db: Session, profile_id: str, *, reviewer_user_id: str) -> ProfileRow:
    """Mark a profile ``validated`` — only when the Pydantic model's own
    ``eligible_for_validation()`` returns no errors. This is the ONE place a
    profile's status can become 'validated'; an incomplete or unmeasured
    profile is refused with the specific reasons, never silently accepted."""
    row = get_profile(db, profile_id)
    if row is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    profile = to_pydantic(row)
    row.reviewed = True
    row.reviewed_by_user_id = reviewer_user_id
    row.reviewed_at = _now()
    profile = to_pydantic(row)  # re-check WITH reviewed=True applied
    errors = profile.eligible_for_validation()
    if errors:
        db.rollback()
        raise CalibrationError(
            "profile is not eligible for validation: " + "; ".join(errors))
    row.status = CalibrationProfileStatus.validated.value
    db.commit()
    db.refresh(row)
    return row


def reject_profile(db: Session, profile_id: str, *, reviewer_user_id: str, reason: str) -> ProfileRow:
    row = get_profile(db, profile_id)
    if row is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    row.reviewed = True
    row.reviewed_by_user_id = reviewer_user_id
    row.reviewed_at = _now()
    row.status = CalibrationProfileStatus.rejected.value
    row.active = False
    row.notes = (row.notes + f"\nRejected: {reason}").strip()
    db.commit()
    db.refresh(row)
    return row


def activate_profile(db: Session, profile_id: str) -> ProfileRow:
    """Activate a profile as THE tested profile for its (printer, material,
    nozzle) context. Refuses anything that isn't validated (see
    CalibrationProfile.eligible_for_activation) — an invalid or incomplete
    profile can never become a 'tested' profile the resolver picks up."""
    row = get_profile(db, profile_id)
    if row is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    profile = to_pydantic(row)
    errors = profile.eligible_for_activation()
    if errors:
        raise CalibrationError(
            "profile cannot be activated: " + "; ".join(errors))
    # Deactivate any other active profile for the same context so the
    # resolver never has two "the" active profiles to choose between.
    others = db.query(ProfileRow).filter(
        ProfileRow.active.is_(True), ProfileRow.id != row.id,
        ProfileRow.printer == row.printer, ProfileRow.material_type == row.material_type,
        ProfileRow.nozzle_mm == row.nozzle_mm,
    ).all()
    for o in others:
        o.active = False
    row.active = True
    db.commit()
    db.refresh(row)
    return row


def deactivate_profile(db: Session, profile_id: str) -> ProfileRow:
    row = get_profile(db, profile_id)
    if row is None:
        raise CalibrationError(f"profile '{profile_id}' not found")
    row.active = False
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------- resolution

def get_active_profiles(
    db: Session, *, printer: str | None = None, material_type: str | None = None,
) -> list[CalibrationProfile]:
    """Active, validated profiles visible to the resolver (never draft/
    rejected — those are never eligible for automatic use in generation)."""
    rows = list_profiles(db, printer=printer, material_type=material_type,
                         status=CalibrationProfileStatus.validated.value)
    return [to_pydantic(r) for r in rows if r.active]


def resolve_for_generation(
    db: Session, *, measurement_type: CalibrationMeasurementType, feature: str,
    fit_class: FitClass | None = None, printer: str | None = None,
    material_type: str | None = None, nozzle_mm: float | None = None,
):
    from app.cad.calibration.resolver import resolve_measurement

    profiles = get_active_profiles(db, printer=printer, material_type=material_type)
    return resolve_measurement(
        profiles, measurement_type=measurement_type, feature=feature, fit_class=fit_class,
        printer=printer, material_type=material_type, nozzle_mm=nozzle_mm)


# --------------------------------------------------------------- comparison

def compare_profiles(db: Session, profile_ids: list[str]) -> dict:
    """Aligned, per-(measurement_type, feature, fit_class) side-by-side
    comparison across the given profiles, for the data-workflow's
    "profile comparison" requirement."""
    profiles = []
    for pid in profile_ids:
        row = get_profile(db, pid)
        if row is None:
            raise CalibrationError(f"profile '{pid}' not found")
        profiles.append(to_pydantic(row))

    keys: list[tuple[str, str, str | None]] = []
    seen = set()
    for p in profiles:
        for m in p.measurements:
            key = (m.measurement_type.value, m.feature, m.fit_class.value if m.fit_class else None)
            if key not in seen:
                seen.add(key)
                keys.append(key)

    rows = []
    for measurement_type, feature, fit_class in keys:
        entry = {"measurement_type": measurement_type, "feature": feature,
                 "fit_class": fit_class, "by_profile": {}}
        for p in profiles:
            match = next(
                (m for m in p.measurements
                 if m.measurement_type.value == measurement_type and m.feature == feature
                 and (m.fit_class.value if m.fit_class else None) == fit_class),
                None)
            entry["by_profile"][p.id] = (
                {"median_mm": match.median_mm, "range_mm": match.range_mm,
                 "stddev_mm": match.stddev_mm, "sample_count": match.sample_count,
                 "confidence": match.confidence}
                if match else None
            )
        rows.append(entry)

    return {
        "profiles": [p.provenance_summary() for p in profiles],
        "measurements": rows,
    }
