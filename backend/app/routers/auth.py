"""Authentication routes: signup, login, current user.

Email/password with JWT bearer tokens. No secrets are returned or logged.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.auth.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.models import User
from app.observability import content_fingerprint, log_event
from app.rate_limit import rate_limit
from app.services import account_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserDTO(BaseModel):
    id: str
    email: str
    # Opt-IN, default False: see app.services.account_service.privacy_summary
    # and docs/legal/ai-model-data-usage.md.
    data_improvement_opt_in: bool = False


class DataUsagePreferenceRequest(BaseModel):
    opt_in: bool


class DeleteAccountRequest(BaseModel):
    # Password reconfirmation on a destructive, irreversible action -- a
    # bearer token alone (e.g. left logged in on a shared machine) should not
    # be sufficient to permanently delete every design in the account.
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserDTO


@router.post("/signup", response_model=TokenResponse, status_code=201,
             dependencies=[rate_limit("auth")])
def signup(req: SignupRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = req.email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        from app.metrics import auth_failures_total

        # email_hash, never the raw address -- lets abuse-pattern detection
        # correlate repeated attempts without logging PII.
        log_event("auth_signup_conflict", email_hash=content_fingerprint(email))
        auth_failures_total.labels(reason="signup_conflict").inc()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )
    user = User(email=email, password_hash=hash_password(req.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id),
        user=UserDTO(id=user.id, email=user.email,
                     data_improvement_opt_in=user.data_improvement_opt_in),
    )


@router.post("/login", response_model=TokenResponse, dependencies=[rate_limit("auth")])
def login(req: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == req.email.lower()))
    if user is None or not verify_password(req.password, user.password_hash):
        from app.metrics import auth_failures_total

        log_event("auth_login_failed", email_hash=content_fingerprint(req.email.lower()))
        auth_failures_total.labels(reason="invalid_credentials").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    return TokenResponse(
        access_token=create_access_token(user.id),
        user=UserDTO(id=user.id, email=user.email,
                     data_improvement_opt_in=user.data_improvement_opt_in),
    )


@router.get("/me", response_model=UserDTO, dependencies=[rate_limit("read")])
def me(user: User = Depends(get_current_user)) -> UserDTO:
    return UserDTO(id=user.id, email=user.email,
                    data_improvement_opt_in=user.data_improvement_opt_in)


@router.put("/me/data-improvement-opt-in", response_model=UserDTO,
            dependencies=[rate_limit("read")])
def set_data_improvement_opt_in(
    req: DataUsagePreferenceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserDTO:
    """Opt in or out of prompts/designs being used to improve LunaiCAD's
    models. Opt-IN, defaults to false for every account -- this endpoint is
    the only way it ever becomes true."""
    user.data_improvement_opt_in = req.opt_in
    db.commit()
    db.refresh(user)
    log_event("data_improvement_opt_in_changed", user_id=user.id, opt_in=req.opt_in)
    return UserDTO(id=user.id, email=user.email,
                    data_improvement_opt_in=user.data_improvement_opt_in)


@router.get("/privacy-summary", dependencies=[rate_limit("read")])
def privacy_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """What's stored for this account, retention, current data-usage choice,
    and how to delete it -- the technical backing for the Privacy Policy's
    "what we store" section (docs/legal/privacy-policy.md)."""
    return account_service.privacy_summary(db, user)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
               dependencies=[rate_limit("auth")])
def delete_account(
    req: DeleteAccountRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Permanently delete this account and every project/design/export/
    calibration-profile/safety-acknowledgment it owns. Irreversible --
    requires the account password, not just the bearer token, as
    confirmation (see docs/legal/data-retention-policy.md)."""
    if not verify_password(req.password, user.password_hash):
        from app.metrics import auth_failures_total

        log_event("account_deletion_failed_auth", user_id=user.id)
        auth_failures_total.labels(reason="delete_account_invalid_password").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password",
        )
    account_service.delete_account(db, user)
