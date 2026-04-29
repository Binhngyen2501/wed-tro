from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session, joinedload

from models import Notification, User, Payment, Contract, Tenant


def create_notification(
    db: Session,
    *,
    recipient_id: int,
    title: str,
    message: str,
    sender_id: Optional[int] = None,
    notification_type: str = "general",
    related_entity_type: Optional[str] = None,
    related_entity_id: Optional[int] = None,
    is_system: bool = False,
) -> Notification:
    """Tạo thông báo mới"""
    notification = Notification(
        recipient_id=recipient_id,
        sender_id=sender_id,
        title=title,
        message=message,
        notification_type=notification_type,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        is_system=is_system,
    )
    db.add(notification)
    db.flush()
    return notification


def get_user_notifications(
    db: Session,
    user_id: int,
    *,
    unread_only: bool = False,
    limit: int = 50,
    include_system: bool = True,
) -> List[Notification]:
    """Lấy danh sách thông báo của user"""
    stmt = (
        select(Notification)
        .options(joinedload(Notification.sender))
        .where(Notification.recipient_id == user_id)
    )

    if unread_only:
        stmt = stmt.where(Notification.is_read == False)

    if not include_system:
        stmt = stmt.where(Notification.is_system == False)

    stmt = stmt.order_by(desc(Notification.created_at)).limit(limit)
    return db.execute(stmt).scalars().all()


def get_unread_count(db: Session, user_id: int) -> int:
    """Đếm số thông báo chưa đọc"""
    stmt = (
        select(func.count())
        .where(Notification.recipient_id == user_id)
        .where(Notification.is_read == False)
    )
    return db.execute(stmt).scalar() or 0


def mark_as_read(db: Session, notification_id: int, user_id: int) -> Optional[Notification]:
    """Đánh dấu thông báo đã đọc"""
    notification = db.get(Notification, notification_id)
    if notification and notification.recipient_id == user_id:
        notification.is_read = True
        db.flush()
        return notification
    return None


def mark_all_as_read(db: Session, user_id: int) -> int:
    """Đánh dấu tất cả thông báo đã đọc, trả về số lượng đã cập nhật"""
    notifications = db.execute(
        select(Notification)
        .where(Notification.recipient_id == user_id)
        .where(Notification.is_read == False)
    ).scalars().all()

    count = 0
    for n in notifications:
        n.is_read = True
        count += 1

    if count > 0:
        db.flush()
    return count


def delete_notification(db: Session, notification_id: int, user_id: int) -> bool:
    """Xóa thông báo (chỉ recipient mới được xóa)"""
    notification = db.get(Notification, notification_id)
    if notification and notification.recipient_id == user_id:
        db.delete(notification)
        db.flush()
        return True
    return False


# ===================== AUTO NOTIFICATIONS =====================

def notify_payment_created(db: Session, payment: Payment) -> Optional[Notification]:
    """Tự động thông báo khi tạo hóa đơn mới"""
    tenant = payment.contract.tenant
    if not tenant or not tenant.user_id:
        return None

    return create_notification(
        db,
        recipient_id=tenant.user_id,
        title=f"🧾 Hóa đơn mới - Kỳ {payment.period}",
        message=f"Bạn có hóa đơn mới cho phòng {payment.contract.room.room_code}. "
                f"Tổng tiền: {payment.amount:,.0f} VNĐ. "
                f"Vui lòng thanh toán trước ngày 5 hàng tháng.",
        notification_type="payment",
        related_entity_type="payment",
        related_entity_id=payment.payment_id,
        is_system=True,
    )


def notify_payment_paid(db: Session, payment: Payment, confirmed_by_admin: bool = False) -> Optional[Notification]:
    """Tự động thông báo khi thanh toán được xác nhận"""
    tenant = payment.contract.tenant
    if not tenant or not tenant.user_id:
        return None

    if confirmed_by_admin:
        return create_notification(
            db,
            recipient_id=tenant.user_id,
            title="✅ Thanh toán đã được xác nhận",
            message=f"Hóa đơn kỳ {payment.period} đã được Admin xác nhận thanh toán. "
                    f"Cảm ơn bạn đã thanh toán đúng hạn!",
            notification_type="payment",
            related_entity_type="payment",
            related_entity_id=payment.payment_id,
            is_system=True,
        )
    return None


def notify_payment_reminder(db: Session, payment: Payment, days_until_due: int = 0) -> Optional[Notification]:
    """Nhắc nhở thanh toán"""
    tenant = payment.contract.tenant
    if not tenant or not tenant.user_id:
        return None

    if days_until_due > 0:
        title = f"⏰ Nhắc nhở: Còn {days_until_due} ngày để thanh toán"
        message = (f"Hóa đơn kỳ {payment.period} cho phòng {payment.contract.room.room_code} "
                   f"còn {days_until_due} ngày đến hạn. Số tiền: {payment.amount:,.0f} VNĐ.")
    else:
        title = "⚠️ Hóa đơn đã quá hạn"
        message = (f"Hóa đơn kỳ {payment.period} cho phòng {payment.contract.room.room_code} "
                   f"đã quá hạn {-days_until_due} ngày. Vui lòng thanh toán ngay để tránh phí phạt.")

    return create_notification(
        db,
        recipient_id=tenant.user_id,
        title=title,
        message=message,
        notification_type="reminder",
        related_entity_type="payment",
        related_entity_id=payment.payment_id,
        is_system=True,
    )


def notify_contract_ending_soon(db: Session, contract: Contract, days_remaining: int) -> Optional[Notification]:
    """Thông báo hợp đồng sắp hết hạn"""
    tenant = contract.tenant
    if not tenant or not tenant.user_id:
        return None

    return create_notification(
        db,
        recipient_id=tenant.user_id,
        title=f"📄 Hợp đồng sắp hết hạn ({days_remaining} ngày)",
        message=f"Hợp đồng thuê phòng {contract.room.room_code} sẽ hết hạn vào ngày {contract.end_date}. "
                f"Vui lòng liên hệ Admin để gia hạn nếu cần.",
        notification_type="contract",
        related_entity_type="contract",
        related_entity_id=contract.contract_id,
        is_system=True,
    )


def notify_admin_new_payment_pending(db: Session, payment: Payment, admin_user_id: int) -> Notification:
    """Thông báo cho Admin khi có thanh toán chờ xác nhận"""
    tenant_name = payment.contract.tenant.full_name if payment.contract.tenant else "Unknown"
    method_display = {
        "momo": "MoMo",
        "bank": "Chuyển khoản ngân hàng",
        "cash": "Tiền mặt",
        "qr": "QR Code"
    }.get(payment.method or "", payment.method or "Unknown")

    return create_notification(
        db,
        recipient_id=admin_user_id,
        title=f"💰 Thanh toán chờ xác nhận - {method_display}",
        message=f"Người thuê {tenant_name} vừa thanh toán hóa đơn kỳ {payment.period} "
                f"cho phòng {payment.contract.room.room_code} ({method_display}). "
                f"Số tiền: {payment.amount:,.0f} VNĐ. Vui lòng kiểm tra và xác nhận.",
        notification_type="payment",
        related_entity_type="payment",
        related_entity_id=payment.payment_id,
        is_system=True,
    )


def send_admin_to_user_message(
    db: Session,
    admin_user_id: int,
    recipient_user_id: int,
    title: str,
    message: str,
    notification_type: str = "general",
) -> Notification:
    """Admin gửi thông báo trực tiếp cho user"""
    return create_notification(
        db,
        sender_id=admin_user_id,
        recipient_id=recipient_user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        is_system=False,
    )


def send_user_to_admin_message(
    db: Session,
    user_id: int,
    admin_user_id: int,
    title: str,
    message: str,
    notification_type: str = "general",
    related_entity_type: Optional[str] = None,
    related_entity_id: Optional[int] = None,
) -> Notification:
    """User gửi thông báo/tin nhắn cho Admin"""
    return create_notification(
        db,
        sender_id=user_id,
        recipient_id=admin_user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        is_system=False,
    )
