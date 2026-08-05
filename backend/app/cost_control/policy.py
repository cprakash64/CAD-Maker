"""Versioned quota policy: global defaults (env-configurable, app.config)
merged with an optional per-account override (app.models.AccountLimitOverride).

Every field is an integer -- counts, or US-cent fixed-point money -- never a
float, so a persisted/compared financial value is never subject to binary
floating-point rounding drift (docs/operations/cost-control-architecture.md).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import AccountLimitOverride


@dataclass(frozen=True)
class EffectivePolicy:
    """The policy actually in force for ONE account at ONE point in time --
    global defaults with any non-null per-account override applied. Every
    BudgetReservation stamps ``version`` at the moment it was checked, so a
    later policy change never retroactively changes what an old reservation
    proves was enforced (same pattern as SafetyAcknowledgment.notice_text)."""

    version: str
    daily_generation_limit: int
    monthly_generation_limit: int
    daily_drawing_limit: int
    concurrent_job_limit: int
    max_estimated_tokens_per_request: int
    max_estimated_cost_cents_per_request: int
    max_daily_account_cost_cents: int
    global_daily_budget_cents: int
    global_hourly_emergency_budget_cents: int
    storage_quota_mb: int
    max_retained_design_versions: int
    max_retries_per_request: int
    max_upload_frequency_per_hour: int
    generation_disabled: bool
    disabled_reason: str | None


def effective_policy(db: Session, user_id: str) -> EffectivePolicy:
    override = db.get(AccountLimitOverride, user_id)

    def pick(override_value: int | None, default: int) -> int:
        return override_value if override_value is not None else default

    return EffectivePolicy(
        version=settings.cost_control_policy_version,
        daily_generation_limit=pick(
            override.daily_generation_limit if override else None,
            settings.cost_daily_generation_limit),
        monthly_generation_limit=pick(
            override.monthly_generation_limit if override else None,
            settings.cost_monthly_generation_limit),
        daily_drawing_limit=pick(
            override.daily_drawing_limit if override else None,
            settings.cost_daily_drawing_limit),
        concurrent_job_limit=pick(
            override.concurrent_job_limit if override else None,
            settings.job_per_user_concurrent_limit),
        max_estimated_tokens_per_request=settings.cost_max_estimated_tokens_per_request,
        max_estimated_cost_cents_per_request=settings.cost_max_estimated_cost_cents_per_request,
        max_daily_account_cost_cents=pick(
            override.daily_cost_cap_cents if override else None,
            settings.cost_max_daily_account_cost_cents),
        global_daily_budget_cents=settings.cost_global_daily_budget_cents,
        global_hourly_emergency_budget_cents=settings.cost_global_hourly_emergency_budget_cents,
        storage_quota_mb=pick(
            override.storage_quota_mb if override else None,
            settings.storage_quota_mb_per_user),
        max_retained_design_versions=pick(
            override.max_design_versions if override else None,
            settings.cost_max_retained_design_versions),
        max_retries_per_request=settings.cost_max_retries_per_request,
        max_upload_frequency_per_hour=settings.cost_max_upload_frequency_per_hour,
        generation_disabled=bool(override.generation_disabled) if override else False,
        disabled_reason=override.disabled_reason if override else None,
    )
