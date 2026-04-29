"""
Users router – quản lý tài khoản (Admin only)
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from dependencies import get_db, require_admin, get_current_user
from models import User
from schemas import UserOut, UserStatusUpdate

router = APIRouter()


@router.get("", response_model=List[UserOut], summary="Danh sách tài khoản (Admin)")
def list_users(
    role: Optional[str] = Query(None, description="admin | user"),
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = select(User).order_by(User.user_id.desc())
    if role:
        q = q.where(User.role == role)
    rows = db.execute(q).scalars().all()
    if keyword:
        kw = keyword.lower()
        rows = [u for u in rows if kw in u.username.lower() or kw in u.full_name.lower()]
    return rows


@router.get("/me", response_model=UserOut, summary="Thông tin tài khoản hiện tại")
def current_user_info(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/{user_id}", response_model=UserOut, summary="Chi tiết tài khoản (Admin)")
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Không tìm thấy tài khoản")
    return user


@router.patch("/{user_id}/status", response_model=UserOut, summary="Cập nhật trạng thái tài khoản (Admin)")
def update_user_status(
    user_id: int,
    data: UserStatusUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.user_id:
        raise HTTPException(400, "Không thể thay đổi trạng thái tài khoản của chính mình")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Không tìm thấy tài khoản")
    user.status = data.status
    db.flush()
    db.refresh(user)
    return user
