"""Generic job-status API (docs/adr/0001-job-queue-database-backed.md).

Every CAD-generation route (design create, drawing-to-cad) submits through
app.services.job_service and returns a job_id; this router is the ONE place
clients poll for progress and request cancellation, regardless of which
route created the job. Every response is the safe DTO
(app.services.job_service.safe_dto) -- status/stage/message/error are always
pre-sanitized strings, never a raw traceback.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database import get_db
from app.models import User
from app.rate_limit import rate_limit
from app.services import job_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", dependencies=[rate_limit("read")])
def list_my_jobs(
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> list[dict]:
    return [job_service.safe_dto(j) for j in job_service.list_jobs(db, user.id)]


@router.get("/{job_id}", dependencies=[rate_limit("poll")])
def get_job(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    job = job_service.get_owned_job(db, job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_service.safe_dto(job)


@router.post("/{job_id}/cancel", dependencies=[rate_limit("create")])
def cancel_job(
    job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> dict:
    job = job_service.get_owned_job(db, job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in job_service.TERMINAL_STATUSES:
        return job_service.safe_dto(job)  # already finished; cancelling is a no-op
    updated = job_service.request_cancel(db, job_id)
    return job_service.safe_dto(updated)
