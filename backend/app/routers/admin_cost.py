"""Administrative cost-control endpoints (docs/operations/cost-control-
architecture.md Step 6). Every route requires app.auth.deps.get_current_admin_user
(a real per-user role, User.is_admin) and every state-changing action is
written to AdminAuditLog by app.cost_control.service -- never silent.

No undocumented capability lives here beyond what this docstring and
docs/operations/cost-control-architecture.md describe.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_admin_user
from app.cost_control import service as cost_service
from app.database import get_db
from app.models import BudgetReservation, Job, User
from app.rate_limit import rate_limit

router = APIRouter(prefix="/api/admin/cost", tags=["admin"])


@router.get("/overview", dependencies=[rate_limit("read")])
def cost_overview(db: Session = Depends(get_db), admin: User = Depends(get_current_admin_user)):
    """Aggregate cost/quota metrics -- system-wide, not any one account."""
    global_usage = cost_service.get_global_usage(db)
    reservations_by_status = dict(db.execute(
        select(BudgetReservation.status, func.count())
        .group_by(BudgetReservation.status)).all())
    return {**global_usage, "reservations_by_status": reservations_by_status}


@router.get("/accounts/{user_id}", dependencies=[rate_limit("read")])
def account_usage(user_id: str, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_admin_user)):
    """One account's quota state -- current usage vs. its EFFECTIVE
    (override-applied) policy."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such account")
    return cost_service.get_account_usage(db, user_id)


@router.get("/accounts/{user_id}/abnormal-usage", dependencies=[rate_limit("read")])
def account_abnormal_usage(user_id: str, db: Session = Depends(get_db),
                           admin: User = Depends(get_current_admin_user)):
    """A basic investigation view: recent reservations and their outcomes
    for one account, so an admin can see WHY a limit tripped (or spot a
    pattern -- e.g. many rapid `released` reservations from repeated
    failures) without querying the database directly."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such account")
    recent = list(db.scalars(
        select(BudgetReservation).where(BudgetReservation.user_id == user_id)
        .order_by(BudgetReservation.created_at.desc()).limit(50)))
    return {
        "user_id": user_id,
        "recent_reservations": [
            {
                "id": r.id, "operation_type": r.operation_type, "status": r.status,
                "estimated_cost_cents": r.estimated_cost_cents,
                "actual_cost_cents": r.actual_cost_cents,
                "release_reason": r.release_reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent
        ],
    }


class DisableGenerationRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/accounts/{user_id}/disable", dependencies=[rate_limit("default")])
def disable_generation(user_id: str, req: DisableGenerationRequest,
                       db: Session = Depends(get_db),
                       admin: User = Depends(get_current_admin_user)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such account")
    cost_service.set_generation_disabled(
        db, admin_user_id=admin.id, target_user_id=user_id, disabled=True,
        reason=req.reason)
    return {"user_id": user_id, "generation_disabled": True}


@router.post("/accounts/{user_id}/enable", dependencies=[rate_limit("default")])
def enable_generation(user_id: str, db: Session = Depends(get_db),
                      admin: User = Depends(get_current_admin_user)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such account")
    cost_service.set_generation_disabled(
        db, admin_user_id=admin.id, target_user_id=user_id, disabled=False)
    return {"user_id": user_id, "generation_disabled": False}


class AdjustLimitsRequest(BaseModel):
    daily_generation_limit: Optional[int] = None
    monthly_generation_limit: Optional[int] = None
    daily_drawing_limit: Optional[int] = None
    concurrent_job_limit: Optional[int] = None
    daily_cost_cap_cents: Optional[int] = None
    storage_quota_mb: Optional[int] = None
    max_design_versions: Optional[int] = None


@router.patch("/accounts/{user_id}/limits", dependencies=[rate_limit("default")])
def adjust_limits(user_id: str, req: AdjustLimitsRequest, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_admin_user)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No such account")
    fields = req.model_dump(exclude_unset=True)
    override = cost_service.adjust_account_limits(
        db, admin_user_id=admin.id, target_user_id=user_id, **fields)
    return {"user_id": user_id, "overrides": {
        f: getattr(override, f) for f in fields
    }}


class EmergencyStopRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/emergency-stop", dependencies=[rate_limit("default")])
def activate_emergency_stop(req: EmergencyStopRequest, db: Session = Depends(get_db),
                            admin: User = Depends(get_current_admin_user)):
    cost_service.set_emergency_stop(db, admin_user_id=admin.id, active=True, reason=req.reason)
    return {"active": True, "reason": req.reason}


@router.delete("/emergency-stop", dependencies=[rate_limit("default")])
def deactivate_emergency_stop(db: Session = Depends(get_db),
                              admin: User = Depends(get_current_admin_user)):
    cost_service.set_emergency_stop(db, admin_user_id=admin.id, active=False)
    return {"active": False}


@router.post("/reservations/release-stale", dependencies=[rate_limit("default")])
def release_stale_reservations(db: Session = Depends(get_db),
                               admin: User = Depends(get_current_admin_user)):
    count = cost_service.admin_release_stale_reservations(db, admin_user_id=admin.id)
    return {"released": count}
