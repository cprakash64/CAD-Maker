"""FastAPI auth dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.database import get_db
from app.models import User

# auto_error=False so we can return a clean 401 with our own message.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token(creds.credentials)
    if not user_id:
        from app.metrics import auth_failures_total
        from app.observability import log_event

        log_event("auth_invalid_token")
        auth_failures_total.labels(reason="invalid_token").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists"
        )
    return user


def get_current_admin_user(user: User = Depends(get_current_user)) -> User:
    """Gates app.routers.admin_cost -- a real per-user role (User.is_admin),
    not the separate OPS_API_TOKEN (app.routers.ops), which is a monitoring
    credential with no notion of "which human did this." Every admin action
    behind this dependency is additionally written to AdminAuditLog by
    app.cost_control.service -- this dependency only proves WHO is allowed
    to ask, not that the action happened silently."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Admin access required")
    return user
