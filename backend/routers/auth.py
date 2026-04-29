"""
Auth router – đăng nhập, đăng ký, thông tin tài khoản
"""
from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import select

from dependencies import get_db, get_current_user, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from models import User
from schemas import LoginRequest, TokenResponse, RegisterRequest, UserOut

from passlib.context import CryptContext

router = APIRouter()
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@123")


def _verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def _hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def _authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if not user:
        return None
    if not _verify_password(password, user.password_hash):
        return None
    return user


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse, summary="Đăng nhập")
def login(form: LoginRequest, db: Session = Depends(get_db)):
    user = _authenticate(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")
    if user.status == "locked":
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")
    token = create_access_token({"sub": str(user.user_id)})
    return TokenResponse(
        access_token=token,
        user_id=user.user_id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
    )


@router.post("/login/form", response_model=TokenResponse, summary="Đăng nhập (OAuth2 form)")
def login_form(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Endpoint tương thích OAuth2 – dùng cho Swagger UI 'Authorize'"""
    user = _authenticate(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu")
    if user.status == "locked":
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")
    token = create_access_token({"sub": str(user.user_id)})
    return TokenResponse(
        access_token=token,
        user_id=user.user_id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
    )


@router.post("/register", response_model=UserOut, status_code=201, summary="Đăng ký tài khoản")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.execute(select(User).where(User.username == data.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Tên đăng nhập đã tồn tại")
    if data.email:
        dup = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail="Email đã được sử dụng")
    user = User(
        username=data.username,
        password_hash=_hash_password(data.password),
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        role="user",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


@router.get("/me", response_model=UserOut, summary="Thông tin tài khoản hiện tại")
def me(current_user: User = Depends(get_current_user)):
    return current_user
