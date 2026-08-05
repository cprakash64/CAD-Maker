"""Physical calibration profile schema (versioned).

A calibration profile is a claim about how a SPECIFIC printer/firmware/nozzle
+ material + slicer/process combination actually behaves, backed by physical
measurements — never a claim about LunaiCAD's geometry in the abstract. This
module is the single source of truth for that claim's shape.

Honesty rules enforced here (docs/calibration.md):
  * A profile with ``source_type=generic_estimate`` can NEVER reach
    ``status=validated`` — see ``CalibrationProfile.eligible_for_validation()``.
  * ``status=validated`` requires >=1 reviewed measurement with real raw
    samples — a profile is never marked validated by declaration alone.
  * Every measurement is explicitly typed as radial clearance, diametral
    clearance, a dimensional correction, or a functional-fit recommendation —
    these are never interchangeable and must never be silently conflated.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SCHEMA_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CalibrationSourceType(str, Enum):
    """Where a profile's numbers came from — never a maturity/trust ranking by
    itself; ``status`` (draft/validated) is the trust gate, this is provenance."""

    generic_estimate = "generic_estimate"        # a built-in, undated guess
    lunaicad_tested = "lunaicad_tested"           # measured in-house by LunaiCAD
    vendor_supplied = "vendor_supplied"           # filament/printer vendor's own data
    community_submitted = "community_submitted"   # submitted by another user
    user_calibrated = "user_calibrated"           # this user's own printer/material


class CalibrationProfileStatus(str, Enum):
    draft = "draft"          # incomplete, unreviewed, or not yet measured
    validated = "validated"  # complete, measured, and reviewed
    rejected = "rejected"    # reviewed and found unreliable — kept for history


class CalibrationMeasurementType(str, Enum):
    """The four distinct kinds of number a calibration test produces. Never
    interchange these: a diametral clearance is not "twice as precise" a
    radial clearance, a dimensional correction is a systematic offset (not a
    fit), and a functional-fit recommendation is a qualitative judgment call
    that happens to carry a number, not a raw measurement."""

    # Per-side (single-face) gap between a hole and a mating pin/shaft, mm.
    radial_clearance = "radial_clearance"
    # Total (diameter-to-diameter) gap between a hole and a mating pin, mm —
    # for a round feature this is close to but not always exactly 2x radial
    # (ovality/first-layer squish differ by axis), so it is measured, not derived.
    diametral_clearance = "diametral_clearance"
    # A systematic printer offset applied to a NOMINAL/designed dimension to
    # get the ACTUAL printed dimension (e.g. XY hole/pin compensation), mm.
    # Signed: negative shrinks the feature, positive grows it.
    dimensional_correction = "dimensional_correction"
    # A qualitative recommendation (press / snug / normal / loose) for a named
    # functional use, carrying the clearance range measured to achieve it.
    functional_fit_recommendation = "functional_fit_recommendation"


class FitClass(str, Enum):
    press = "press"    # interference fit — assembled with force, stays put
    snug = "snug"      # light hand-force, then self-retains
    normal = "normal"  # free hand assembly, some play
    loose = "loose"    # runs/rotates freely


class CalibrationMeasurement(BaseModel):
    """One measured quantity within a profile: what was measured, on what
    feature, how many samples, and the derived statistics.

    ``median_mm``/``range_mm``/``stddev_mm``/``sample_count`` are always
    SERVER-DERIVED from ``raw_samples_mm`` (see
    ``CalibrationMeasurement.recompute_statistics``) — a client can submit raw
    samples but never the aggregate statistics directly, so a profile can
    never claim a median that doesn't match its own raw data.
    """

    model_config = {"extra": "forbid"}

    measurement_type: CalibrationMeasurementType
    # What was measured, e.g. "hole_pin_clearance", "M4_clearance_hole",
    # "bearing_seat_od", "xy_hole_compensation", "min_wall_thickness".
    feature: str = Field(min_length=1, max_length=128)
    fit_class: Optional[FitClass] = None
    # The designed/nominal value this measurement is relative to, mm. Required
    # for dimensional_correction (the correction IS nominal-minus-actual) and
    # for clearance measurements where the intended nominal fit is known.
    nominal_mm: Optional[float] = Field(default=None)
    raw_samples_mm: list[float] = Field(default_factory=list, max_length=200)
    sample_count: int = Field(default=0, ge=0)
    median_mm: Optional[float] = None
    range_mm: Optional[float] = None
    stddev_mm: Optional[float] = None
    # 0..1, derived from sample size + spread (see recompute_statistics) unless
    # explicitly overridden with a reason in notes — never a bare guess.
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = Field(default="", max_length=2000)

    @field_validator("raw_samples_mm")
    @classmethod
    def _samples_finite(cls, v: list[float]) -> list[float]:
        for x in v:
            if x != x or x in (float("inf"), float("-inf")):  # noqa: PLR0124 - NaN check
                raise ValueError("raw sample values must be finite numbers")
        return v

    @model_validator(mode="after")
    def _type_specific_requirements(self) -> "CalibrationMeasurement":
        # nominal_mm is required whenever the correction is measured against
        # ONE specific test feature (typical). It may be left None only for a
        # blanket/global correction that isn't diameter-dependent — such a
        # value should be rare and always explained in `notes`.
        if self.measurement_type == CalibrationMeasurementType.dimensional_correction \
                and self.nominal_mm is None and not self.notes:
            raise ValueError(
                "dimensional_correction measurements require either nominal_mm "
                "(the correction is nominal minus actual for that test feature) "
                "or, for a blanket/global correction, a note explaining why "
                "no single nominal applies")
        if self.measurement_type == CalibrationMeasurementType.functional_fit_recommendation \
                and self.fit_class is None:
            raise ValueError(
                "functional_fit_recommendation measurements require fit_class")
        return self

    def recompute_statistics(self) -> "CalibrationMeasurement":
        """Recompute sample_count/median/range/stddev/confidence FROM
        raw_samples_mm. Call this on every write path — never trust a client-
        submitted aggregate over the raw data it's supposed to summarize."""
        n = len(self.raw_samples_mm)
        self.sample_count = n
        if n == 0:
            self.median_mm = None
            self.range_mm = None
            self.stddev_mm = None
            self.confidence = 0.0
            return self
        self.median_mm = round(statistics.median(self.raw_samples_mm), 4)
        self.range_mm = round(max(self.raw_samples_mm) - min(self.raw_samples_mm), 4)
        self.stddev_mm = round(statistics.stdev(self.raw_samples_mm), 4) if n >= 2 else 0.0
        # Confidence: rewards more samples, penalizes spread relative to the
        # median magnitude. Deliberately conservative — never > 0.95 from
        # statistics alone; a human reviewer is still required for `validated`.
        sample_term = min(n / 10.0, 1.0)            # saturates at 10 samples
        spread_ref = max(abs(self.median_mm), 0.05)  # avoid div-by-~0 for tiny values
        spread_penalty = min((self.stddev_mm or 0.0) / spread_ref, 1.0)
        self.confidence = round(max(0.0, min(0.95, sample_term * (1.0 - spread_penalty))), 3)
        return self


class CalibrationProfile(BaseModel):
    """A versioned, provenance-tagged claim about one printer/material/
    slicer/process combination's real-world dimensional behavior.

    Distinguish clearly (never conflate): ``measurements`` entries typed
    radial_clearance vs diametral_clearance vs dimensional_correction vs
    functional_fit_recommendation (see CalibrationMeasurementType).
    """

    model_config = {"extra": "forbid"}

    schema_version: int = SCHEMA_VERSION
    id: str = Field(min_length=1, max_length=32)
    version: int = Field(default=1, ge=1)
    label: str = Field(min_length=1, max_length=200)
    source_type: CalibrationSourceType
    status: CalibrationProfileStatus = CalibrationProfileStatus.draft

    # --- process identity ---------------------------------------------------
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

    # --- test record ----------------------------------------------------------
    test_date: Optional[date] = None
    measurements: list[CalibrationMeasurement] = Field(default_factory=list, max_length=200)
    notes: str = Field(default="", max_length=4000)

    # --- lifecycle / review ---------------------------------------------------
    created_by_user_id: Optional[str] = Field(default=None, max_length=32)
    reviewed: bool = False
    reviewed_by_user_id: Optional[str] = Field(default=None, max_length=32)
    reviewed_at: Optional[datetime] = None
    active: bool = False  # this version is the one the resolver will pick
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    # --- required-completeness -------------------------------------------------
    _REQUIRED_PROCESS_FIELDS = (
        "printer", "nozzle_mm", "material_type", "layer_height_mm", "line_width_mm",
    )
    _MIN_SAMPLES_FOR_VALIDATION = 3

    def completeness_errors(self) -> list[str]:
        """Fields missing for this profile to even be considered complete,
        independent of whether it has been measured/reviewed yet."""
        errs = []
        for f in self._REQUIRED_PROCESS_FIELDS:
            if getattr(self, f) in (None, ""):
                errs.append(f"missing required process field '{f}'")
        return errs

    def measurement_errors(self) -> list[str]:
        """Reasons the measurement set is not yet trustworthy enough to
        validate — independent of the process-field completeness check."""
        errs = []
        if not self.measurements:
            errs.append("no measurements recorded")
        for m in self.measurements:
            if m.sample_count < self._MIN_SAMPLES_FOR_VALIDATION:
                errs.append(
                    f"measurement '{m.feature}' ({m.measurement_type.value}) has only "
                    f"{m.sample_count} sample(s); at least "
                    f"{self._MIN_SAMPLES_FOR_VALIDATION} are required to validate")
            if m.sample_count != len(m.raw_samples_mm):
                errs.append(
                    f"measurement '{m.feature}': sample_count does not match the "
                    "number of raw samples on record")
        return errs

    def eligible_for_validation(self) -> list[str]:
        """All reasons this profile CANNOT be marked ``validated`` right now.
        Empty list = eligible. A generic_estimate profile is NEVER eligible —
        by definition it carries no physical measurement of its own."""
        errs: list[str] = []
        if self.source_type == CalibrationSourceType.generic_estimate:
            errs.append(
                "generic_estimate profiles are built-in defaults, not physical "
                "measurements, and can never be marked validated")
        errs += self.completeness_errors()
        errs += self.measurement_errors()
        if not self.reviewed:
            errs.append("profile has not been reviewed by a human")
        return errs

    def eligible_for_activation(self) -> list[str]:
        """All reasons this profile cannot be ACTIVATED as the resolver's
        tested profile for its printer/material context. Only a validated
        profile may be activated as 'tested' — a draft, however complete,
        stays labeled an estimate/unreviewed input, never a trusted result."""
        if self.status != CalibrationProfileStatus.validated:
            return ["only a validated profile can be activated as a tested "
                    "profile"] + self.eligible_for_validation()
        return []

    def measurements_by_type(
        self, measurement_type: CalibrationMeasurementType,
    ) -> list[CalibrationMeasurement]:
        return [m for m in self.measurements if m.measurement_type == measurement_type]

    def provenance_summary(self) -> dict:
        """Compact, user-facing provenance block — this is what gets attached
        to any generation/validation result that used this profile."""
        return {
            "profile_id": self.id,
            "version": self.version,
            "label": self.label,
            "source_type": self.source_type.value,
            "status": self.status.value,
            "reviewed": self.reviewed,
            "sample_count": sum(m.sample_count for m in self.measurements),
            "test_date": self.test_date.isoformat() if self.test_date else None,
            "printer": self.printer,
            "material_type": self.material_type,
        }


__all__ = [
    "SCHEMA_VERSION",
    "CalibrationSourceType",
    "CalibrationProfileStatus",
    "CalibrationMeasurementType",
    "FitClass",
    "CalibrationMeasurement",
    "CalibrationProfile",
]
