"""Signup / Login (email + password) and current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import create_access_token, get_current_user_id, hash_password, verify_password
from app.db import create_user, get_user_by_email, get_user_by_id

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str


class MeResponse(BaseModel):
    user_id: str
    email: str


@router.post("/signup", response_model=TokenResponse)
async def signup(body: SignupRequest):
    email = (body.email or "").strip().lower()
    password = body.password or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = create_user(email=email, password_hash=hash_password(password))
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user_id=str(user.id), email=user.email)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    email = (body.email or "").strip().lower()
    password = body.password or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")
    user = get_user_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user_id=str(user.id), email=user.email)


@router.get("/me", response_model=MeResponse)
async def me(user_id: str = Depends(get_current_user_id)):
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return MeResponse(user_id=str(user.id), email=user.email)
