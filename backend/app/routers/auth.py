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
from app.rate_limit import rate_limit

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
        user=UserDTO(id=user.id, email=user.email),
    )


@router.post("/login", response_model=TokenResponse, dependencies=[rate_limit("auth")])
def login(req: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == req.email.lower()))
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    return TokenResponse(
        access_token=create_access_token(user.id),
        user=UserDTO(id=user.id, email=user.email),
    )


@router.get("/me", response_model=UserDTO)
def me(user: User = Depends(get_current_user)) -> UserDTO:
    return UserDTO(id=user.id, email=user.email)
