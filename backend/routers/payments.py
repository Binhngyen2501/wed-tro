"""
Payments router – CRUD hóa đơn + thanh toán MoMo
Admin: full CRUD, xác nhận thanh toán
User: xem hóa đơn của mình, self-confirm MoMo
"""
from __future__ import annotations

import os
import urllib.parse
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from dependencies import get_db, get_current_user, require_admin
from models import Contract, Payment, Tenant, User, Room
from schemas import PaymentOut, PaymentCreate, PaymentUpdate, MarkPaidRequest
from services.notification_service import create_notification, notify_payment_paid

router = APIRouter()

MOMO_PHONE = os.getenv("MOMO_PHONE", "0909000000")
MOMO_NAME = os.getenv("MOMO_NAME", "Chủ Trọ")


def _load_payment(db: Session, payment_id: int) -> Payment:
    p = db.execute(
        select(Payment)
        .options(joinedload(Payment.contract).joinedload(Contract.room))
        .where(Payment.payment_id == payment_id)
    ).unique().scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Không tìm thấy hóa đơn")
    return p


# ── Admin ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[PaymentOut], summary="Danh sách hóa đơn (Admin)")
def list_payments(
    status: Optional[str] = Query(None),
    contract_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = select(Payment).order_by(Payment.payment_id.desc())
    if status:
        q = q.where(Payment.status == status)
    if contract_id:
        q = q.where(Payment.contract_id == contract_id)
    return db.execute(q).scalars().all()


@router.post("", response_model=PaymentOut, status_code=201, summary="Tạo hóa đơn (Admin)")
def create_payment(
    data: PaymentCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    contract = db.get(Contract, data.contract_id)
    if not contract:
        raise HTTPException(404, "Không tìm thấy hợp đồng")
    payment = Payment(**data.model_dump())
    db.add(payment)
    db.flush()
    db.refresh(payment)
    return payment


@router.put("/{payment_id}", response_model=PaymentOut, summary="Cập nhật hóa đơn (Admin)")
def update_payment(
    payment_id: int,
    data: PaymentUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(404, "Không tìm thấy hóa đơn")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(p, field, value)
    db.flush()
    db.refresh(p)
    return p


@router.delete("/{payment_id}", status_code=204, summary="Xoá hóa đơn (Admin)")
def delete_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(404, "Không tìm thấy hóa đơn")
    db.delete(p)
    db.flush()


@router.post("/{payment_id}/mark-paid", response_model=PaymentOut, summary="Xác nhận đã thanh toán (Admin)")
def admin_mark_paid(
    payment_id: int,
    body: MarkPaidRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(404, "Không tìm thấy hóa đơn")
    p.status = "paid"
    p.paid_date = date.today()
    p.method = body.method
    db.flush()
    db.refresh(p)
    return p


@router.post("/{payment_id}/mark-pending", response_model=PaymentOut, summary="Đánh dấu chờ xác nhận (Admin)")
def admin_mark_pending(
    payment_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    p = db.get(Payment, payment_id)
    if not p:
        raise HTTPException(404, "Không tìm thấy hóa đơn")
    p.status = "pending_verification"
    db.flush()
    db.refresh(p)
    return p


# ── User ──────────────────────────────────────────────────────────────────────

@router.get("/my", response_model=List[PaymentOut], summary="Hóa đơn của tôi")
def my_payments(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant = db.execute(
        select(Tenant).where(Tenant.user_id == current_user.user_id)
    ).scalar_one_or_none()
    if not tenant:
        return []
    contracts = db.execute(
        select(Contract).where(Contract.tenant_id == tenant.tenant_id)
    ).scalars().all()
    contract_ids = [c.contract_id for c in contracts]
    if not contract_ids:
        return []
    q = select(Payment).where(Payment.contract_id.in_(contract_ids)).order_by(Payment.payment_id.desc())
    if status:
        q = q.where(Payment.status == status)
    return db.execute(q).scalars().all()


@router.get("/{payment_id}", response_model=PaymentOut, summary="Chi tiết hóa đơn")
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = _load_payment(db, payment_id)
    if current_user.role != "admin":
        tenant = db.execute(
            select(Tenant).where(Tenant.user_id == current_user.user_id)
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(403, "Bạn không có quyền xem hóa đơn này")
        c = db.get(Contract, p.contract_id)
        if not c or c.tenant_id != tenant.tenant_id:
            raise HTTPException(403, "Bạn không có quyền xem hóa đơn này")
    return p


@router.post("/{payment_id}/confirm-momo", response_model=PaymentOut, summary="Xác nhận đã thanh toán MoMo (User)")
def user_confirm_momo(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """User tự xác nhận đã quét QR MoMo — trạng thái chuyển sang pending_verification"""
    p = _load_payment(db, payment_id)
    # Kiểm tra quyền
    tenant = db.execute(
        select(Tenant).where(Tenant.user_id == current_user.user_id)
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(403, "Bạn không có quyền thực hiện thao tác này")
    c = db.get(Contract, p.contract_id)
    if not c or c.tenant_id != tenant.tenant_id:
        raise HTTPException(403, "Bạn không có quyền thực hiện thao tác này")
    if p.status == "paid":
        raise HTTPException(400, "Hóa đơn đã được thanh toán")

    p.status = "pending_verification"
    p.method = "momo_qr"
    db.flush()

    # Thông báo admin
    admin = db.execute(select(User).where(User.role == "admin").limit(1)).scalar_one_or_none()
    if admin:
        room_code = p.contract.room.room_code if p.contract and p.contract.room else "?"
        create_notification(
            db,
            sender_id=current_user.user_id,
            recipient_id=admin.user_id,
            title=f"Xác nhận thanh toán MoMo – Phòng {room_code}",
            message=f"Khách {current_user.full_name} đã xác nhận chuyển khoản MoMo "
                    f"cho hóa đơn #{payment_id} (kỳ {p.period}). Vui lòng kiểm tra và xác nhận.",
            notification_type="payment",
            related_entity_type="payment",
            related_entity_id=payment_id,
        )
    db.refresh(p)
    return p


@router.get("/{payment_id}/momo-qr-data", summary="Lấy thông tin QR MoMo cho hóa đơn")
def get_momo_qr_data(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trả về chuỗi MoMo deeplink để frontend tự vẽ QR"""
    p = _load_payment(db, payment_id)
    room_code = p.contract.room.room_code if p.contract and p.contract.room else str(p.contract_id)
    note = f"Phong {room_code} ky {p.period} HD{p.contract_id}"
    encoded_note = urllib.parse.quote(note)
    momo_url = f"2|99|{MOMO_PHONE}|{MOMO_NAME}||0|0|{int(p.amount)}|{encoded_note}"
    return {
        "momo_url": momo_url,
        "phone": MOMO_PHONE,
        "name": MOMO_NAME,
        "amount": int(p.amount),
        "note": note,
    }
