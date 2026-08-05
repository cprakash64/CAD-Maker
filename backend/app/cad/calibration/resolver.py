"""Pure (no DB) calibration-value resolution.

Every "how much clearance / correction should this fit use?" decision in the
codebase should end here instead of a bare literal. This module knows nothing
about the database — callers with real user profiles pass them in; callers
with none (or that can't reach a DB session at that point in the call chain)
still get a resolved value, but it always comes from ``GENERIC_ESTIMATE``,
the one, named, explicitly-labeled fallback (never an un-attributed magic
number scattered per call site).

``GENERIC_ESTIMATE`` intentionally reproduces today's pre-calibration-system
literals — this migration is additive: behavior is unchanged for anyone who
hasn't measured and validated a real profile, but every value is now a typed,
provenance-carrying ``CalibrationMeasurement`` instead of a bare float.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.schemas.calibration import (
    CalibrationMeasurement,
    CalibrationMeasurementType,
    CalibrationProfile,
    CalibrationProfileStatus,
    CalibrationSourceType,
    FitClass,
)


def _est(measurement_type: CalibrationMeasurementType, feature: str, value_mm: float,
         *, fit_class: FitClass | None = None, nominal_mm: float | None = None,
         notes: str = "") -> CalibrationMeasurement:
    """One generic-estimate 'measurement' with zero real samples — confidence
    is always 0.0 (recompute_statistics with no samples), which is the
    signal every consumer checks to know this is NOT a physical measurement."""
    m = CalibrationMeasurement(
        measurement_type=measurement_type, feature=feature, fit_class=fit_class,
        nominal_mm=nominal_mm, raw_samples_mm=[],
        notes=notes or "Internal engineering default — not a physical measurement.")
    m.recompute_statistics()  # 0 samples -> confidence 0.0, median_mm stays None
    # The estimate's value isn't a statistic (there are no samples) — set it
    # directly, same field consumers already read for a measured value.
    m.median_mm = value_mm
    return m


# The generic-estimate profile: today's pre-calibration-system hardcoded
# values, now named and typed. NEVER eligible for `validated` status (see
# CalibrationProfile.eligible_for_validation) — always the estimate of last
# resort, always labeled as such wherever it's used.
GENERIC_ESTIMATE = CalibrationProfile(
    id="generic-estimate",
    version=1,
    label="Generic engineering estimate (no physical measurement)",
    source_type=CalibrationSourceType.generic_estimate,
    status=CalibrationProfileStatus.draft,
    active=True,
    test_date=None,
    notes="Built-in fallback used whenever no validated, physically-measured "
          "profile matches the current printer/material/nozzle context. Not "
          "ASME/ISO certified and not measured on any specific printer — see "
          "docs/calibration.md.",
    measurements=[
        _est(CalibrationMeasurementType.diametral_clearance, "bearing_seat",
             -0.02, fit_class=FitClass.press,
             notes="Interference fit for a pressed bearing OD seat."),
        _est(CalibrationMeasurementType.diametral_clearance, "bearing_seat",
             0.05, fit_class=FitClass.normal,
             notes="Slip fit for a removable bearing OD seat."),
        _est(CalibrationMeasurementType.diametral_clearance, "holder_case_clearance",
             1.5, fit_class=FitClass.normal,
             notes="General-purpose holder/cradle clearance around an object "
                   "(e.g. a phone, possibly cased)."),
        # Generic round hole-around-pin/shaft diametral clearance, by fit class
        # (used by the "change fit class" localized edit): new hole diameter =
        # pin/shaft nominal diameter + this value.
        _est(CalibrationMeasurementType.diametral_clearance, "hole_pin_clearance",
             -0.05, fit_class=FitClass.press,
             notes="Interference fit — the hole must be forced onto the pin/shaft."),
        _est(CalibrationMeasurementType.diametral_clearance, "hole_pin_clearance",
             0.05, fit_class=FitClass.snug,
             notes="Light hand-force assembly that then self-retains."),
        _est(CalibrationMeasurementType.diametral_clearance, "hole_pin_clearance",
             0.15, fit_class=FitClass.normal,
             notes="Free hand assembly with some play."),
        _est(CalibrationMeasurementType.diametral_clearance, "hole_pin_clearance",
             0.30, fit_class=FitClass.loose,
             notes="Runs or rotates freely."),
        _est(CalibrationMeasurementType.dimensional_correction, "printer_xy_compensation",
             0.0, notes="Blanket XY offset applied to hole/pin features; 0.0 "
                        "means requested dimensions are preserved exactly."),
    ],
)


@dataclass
class ResolvedCalibrationValue:
    value_mm: float
    is_estimate: bool
    provenance: dict = field(default_factory=dict)
    measurement: Optional[CalibrationMeasurement] = None


def _profile_matches(profile: CalibrationProfile, *, printer: str | None,
                     material_type: str | None, nozzle_mm: float | None) -> bool:
    if profile.status != CalibrationProfileStatus.validated or not profile.active:
        return profile is GENERIC_ESTIMATE
    if printer and profile.printer and profile.printer.strip().lower() != printer.strip().lower():
        return False
    if material_type and profile.material_type and \
            profile.material_type.strip().lower() != material_type.strip().lower():
        return False
    if nozzle_mm and profile.nozzle_mm and abs(profile.nozzle_mm - nozzle_mm) > 0.05:
        return False
    return True


def resolve_measurement(
    profiles: list[CalibrationProfile],
    *,
    measurement_type: CalibrationMeasurementType,
    feature: str,
    fit_class: FitClass | None = None,
    printer: str | None = None,
    material_type: str | None = None,
    nozzle_mm: float | None = None,
) -> ResolvedCalibrationValue:
    """Resolve one calibration value from the given profiles (which should
    already be filtered to this user's/account's visible profiles), falling
    back to ``GENERIC_ESTIMATE`` when nothing validated matches.

    Preference order among candidates: an exact printer+material+nozzle
    match beats a partial match; among ties, more raw samples wins; among
    ties on that, higher confidence wins. The generic estimate is always the
    last resort, never preferred over a real match.
    """
    candidates: list[tuple[int, CalibrationProfile, CalibrationMeasurement]] = []
    for profile in [*profiles, GENERIC_ESTIMATE]:
        if not _profile_matches(profile, printer=printer, material_type=material_type,
                                nozzle_mm=nozzle_mm):
            continue
        for m in profile.measurements:
            if m.measurement_type != measurement_type or m.feature != feature:
                continue
            if fit_class is not None and m.fit_class != fit_class:
                continue
            specificity = (
                (1 if printer and profile.printer and
                     profile.printer.strip().lower() == printer.strip().lower() else 0)
                + (1 if material_type and profile.material_type and
                        profile.material_type.strip().lower() == material_type.strip().lower()
                   else 0)
                + (1 if nozzle_mm and profile.nozzle_mm and
                        abs(profile.nozzle_mm - nozzle_mm) <= 0.05 else 0)
            )
            candidates.append((specificity, profile, m))

    if not candidates:
        raise LookupError(
            f"No calibration data (including the generic estimate) for "
            f"{measurement_type.value}/{feature}"
            + (f" ({fit_class.value})" if fit_class else ""))

    candidates.sort(key=lambda c: (
        c[1] is GENERIC_ESTIMATE,          # real profiles before the estimate
        -c[0],                              # more specific match first
        -c[2].sample_count,                 # more samples first
        -c[2].confidence,                   # higher confidence first
    ))
    _, profile, measurement = candidates[0]
    value = measurement.median_mm
    return ResolvedCalibrationValue(
        value_mm=float(value if value is not None else 0.0),
        is_estimate=profile is GENERIC_ESTIMATE,
        provenance=profile.provenance_summary(),
        measurement=measurement,
    )


__all__ = ["GENERIC_ESTIMATE", "ResolvedCalibrationValue", "resolve_measurement"]
