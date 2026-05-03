"""
Users router – quản lý tài khoản (Admin only)
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import delete, func, or_, select, update

from dependencies import get_db, require_admin, get_current_user
from models import AuditLog, Contract, Notification, Room, Tenant, User
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


@router.delete("/{user_id}", summary="XoÃ¡ tÃ i khoáº£n (Admin)")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.user_id:
        raise HTTPException(400, "KhÃ´ng thá»ƒ xÃ³a tÃ i khoáº£n cá»§a chÃ­nh mÃ¬nh")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "KhÃ´ng tÃ¬m tháº¥y tÃ i khoáº£n")
    if user.role == "admin":
        raise HTTPException(400, "KhÃ´ng cho phÃ©p xÃ³a tÃ i khoáº£n admin")

    owned_room_count = db.scalar(select(func.count(Room.room_id)).where(Room.owner_id == user_id)) or 0
    if owned_room_count > 0:
        raise HTTPException(409, f"KhÃ´ng thá»ƒ xÃ³a: user Ä‘ang sá»Ÿ há»¯u {owned_room_count} phÃ²ng")

    tenant = db.execute(select(Tenant).where(Tenant.user_id == user_id)).scalar_one_or_none()
    if tenant:
        active_contracts = db.scalar(
            select(func.count(Contract.contract_id)).where(
                Contract.tenant_id == tenant.tenant_id,
                Contract.status == "active",
            )
        ) or 0
        if active_contracts > 0:
            raise HTTPException(409, "KhÃ´ng thá»ƒ xÃ³a: user Ä‘ang liÃªn káº¿t ngÆ°á»i thuÃª cÃ³ há»£p Ä‘á»“ng active")
        tenant.user_id = None

    db.execute(
        update(AuditLog)
        .where(AuditLog.actor_user_id == user_id)
        .values(actor_user_id=None)
    )
    db.execute(
        delete(Notification).where(
            or_(Notification.sender_id == user_id, Notification.recipient_id == user_id)
        )
    )
    db.delete(user)
    db.flush()
    return {"message": "ÄÃ£ xÃ³a tÃ i khoáº£n", "user_id": user_id}
