"""
Notifications router
Admin: gửi thông báo, xem tin nhắn từ user
User: xem thông báo của mình, nhắn tin với admin, đánh dấu đã đọc
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from dependencies import get_db, get_current_user, require_admin
from models import Notification, User
from schemas import NotificationOut, SendNotificationRequest, SendMessageRequest
from services.notification_service import (
    create_notification,
    get_user_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
    send_admin_to_user_message,
    send_user_to_admin_message,
)

router = APIRouter()


# ── User ──────────────────────────────────────────────────────────────────────

@router.get("/my", response_model=List[NotificationOut], summary="Thông báo của tôi")
def my_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_user_notifications(db, current_user.user_id, unread_only=unread_only, limit=limit)


@router.get("/my/unread-count", summary="Số thông báo chưa đọc")
def my_unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"unread_count": get_unread_count(db, current_user.user_id)}


@router.post("/my/{notification_id}/read", response_model=NotificationOut, summary="Đánh dấu đã đọc")
def read_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = db.get(Notification, notification_id)
    if not n or n.recipient_id != current_user.user_id:
        raise HTTPException(404, "Không tìm thấy thông báo")
    n.is_read = True
    db.flush()
    db.refresh(n)
    return n


@router.post("/my/mark-all-read", summary="Đánh dấu tất cả đã đọc")
def read_all(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mark_all_as_read(db, current_user.user_id)
    return {"message": "Đã đánh dấu tất cả đã đọc"}


@router.post("/message-admin", summary="Nhắn tin đến Admin")
def message_admin(
    data: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
    if not admin:
        raise HTTPException(500, "Không tìm thấy Admin")
    notif = send_user_to_admin_message(
        db,
        user_id=current_user.user_id,
        admin_user_id=admin.user_id,
        title=f"Tin nhắn từ {current_user.full_name}",
        message=data.message,
        notification_type="message",
        related_entity_type="room" if data.room_id else None,
        related_entity_id=data.room_id,
    )
    return {"message": "Đã gửi tin nhắn đến Admin", "notification_id": notif.notification_id}


# ── Admin ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[NotificationOut], summary="Tất cả thông báo (Admin)")
def all_notifications(
    recipient_id: Optional[int] = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if recipient_id:
        return get_user_notifications(db, recipient_id, unread_only=unread_only, limit=limit)
    q = select(Notification).order_by(Notification.notification_id.desc()).limit(limit)
    if unread_only:
        q = q.where(Notification.is_read == False)
    return db.execute(q).scalars().all()


@router.get("/inbox", response_model=List[NotificationOut], summary="Tin nhắn từ User (Admin inbox)")
def admin_inbox(
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Thông báo/tin nhắn mà user gửi đến admin"""
    q = (
        select(Notification)
        .where(Notification.recipient_id == admin.user_id)
        .order_by(Notification.notification_id.desc())
        .limit(limit)
    )
    return db.execute(q).scalars().all()


@router.post("/send", summary="Gửi thông báo (Admin)")
def send_notification(
    data: SendNotificationRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if data.recipient_id:
        target = db.get(User, data.recipient_id)
        if not target:
            raise HTTPException(404, "Không tìm thấy người nhận")
        send_admin_to_user_message(
            db,
            admin_user_id=admin.user_id,
            recipient_user_id=data.recipient_id,
            title=data.title,
            message=data.message,
            notification_type=data.notification_type,
        )
        return {"message": f"Đã gửi đến {target.full_name}", "sent": 1}
    else:
        # Gửi tất cả user
        users = db.execute(select(User).where(User.role == "user")).scalars().all()
        for u in users:
            send_admin_to_user_message(
                db,
                admin_user_id=admin.user_id,
                recipient_user_id=u.user_id,
                title=data.title,
                message=data.message,
                notification_type=data.notification_type,
            )
        return {"message": f"Đã gửi đến {len(users)} người dùng", "sent": len(users)}


@router.post("/{notification_id}/read", summary="Đánh dấu đã đọc (Admin)")
def admin_mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    n = db.get(Notification, notification_id)
    if not n:
        raise HTTPException(404, "Không tìm thấy thông báo")
    n.is_read = True
    db.flush()
    return {"message": "Đã đánh dấu đã đọc"}
