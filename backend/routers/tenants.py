"""
Tenants router - CRUD ho so nguoi thue (Admin)
User: xem ho so cua chinh minh
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from dependencies import get_db, get_current_user, require_admin
from models import Contract, Tenant, User
from schemas import TenantCreate, TenantOut, TenantUpdate

router = APIRouter()
LATE_FEE_MARKER = "[late_fee_applied]"


def _overdue_days(period: str, due_day: int) -> int:
    try:
        due_date = datetime.strptime(period + "-01", "%Y-%m-%d").date().replace(day=due_day)
    except ValueError:
        return 0
    return max((date.today() - due_date).days, 0)


@router.get("", response_model=List[TenantOut], summary="Danh sach nguoi thue (Admin)")
def list_tenants(
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = select(Tenant).order_by(Tenant.tenant_id.desc())
    rows = db.execute(q).scalars().all()
    if keyword:
        kw = keyword.lower()
        rows = [t for t in rows if kw in (t.full_name or "").lower() or kw in (t.phone or "").lower()]
    return rows


@router.get("/me", response_model=Optional[TenantOut], summary="Ho so nguoi thue cua toi")
def my_tenant(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant = db.execute(
        select(Tenant).where(Tenant.user_id == current_user.user_id)
    ).scalar_one_or_none()
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut, summary="Chi tiet nguoi thue (Admin)")
def get_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Khong tim thay nguoi thue")
    return tenant


@router.post("", response_model=TenantOut, status_code=201, summary="Them nguoi thue (Admin)")
def create_tenant(
    data: TenantCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if data.user_id:
        dup = db.execute(select(Tenant).where(Tenant.user_id == data.user_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "User nay da co ho so nguoi thue")
    tenant = Tenant(**data.model_dump())
    db.add(tenant)
    db.flush()
    db.refresh(tenant)
    return tenant


@router.put("/{tenant_id}", response_model=TenantOut, summary="Cap nhat nguoi thue (Admin)")
def update_tenant(
    tenant_id: int,
    data: TenantUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Khong tim thay nguoi thue")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)
    db.flush()
    db.refresh(tenant)
    return tenant


@router.delete("/{tenant_id}", status_code=204, summary="Xoa nguoi thue (Admin)")
def delete_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Khong tim thay nguoi thue")
    db.delete(tenant)
    db.flush()


@router.post(
    "/{tenant_id}/overdue-penalty-lock",
    summary="Phat qua han va khoa tai khoan nguoi thue (Admin)",
)
def overdue_penalty_lock(
    tenant_id: int,
    overdue_days: int = Query(5, ge=1, le=120),
    due_day: int = Query(5, ge=1, le=28),
    late_fee: float = Query(0, ge=0),
    lock_user: bool = Query(True),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.execute(
        select(Tenant)
        .options(
            joinedload(Tenant.user),
            joinedload(Tenant.contracts).joinedload(Contract.payments),
        )
        .where(Tenant.tenant_id == tenant_id)
    ).unique().scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Khong tim thay nguoi thue")

    late_fee_dec = Decimal(str(late_fee))
    affected_count = 0
    fee_count = 0
    total_fee = Decimal("0")

    for contract in tenant.contracts:
        for payment in contract.payments:
            if payment.status not in {"unpaid", "overdue"}:
                continue
            if _overdue_days(payment.period, due_day) < overdue_days:
                continue

            changed = False
            if payment.status == "unpaid":
                payment.status = "overdue"
                changed = True

            if late_fee_dec > 0 and LATE_FEE_MARKER not in (payment.note or ""):
                payment.amount = Decimal(str(payment.amount)) + late_fee_dec
                current_note = (payment.note or "").strip()
                marker = f"{LATE_FEE_MARKER} {date.today().isoformat()} +{int(late_fee_dec)}"
                payment.note = f"{current_note}\n{marker}".strip() if current_note else marker
                fee_count += 1
                total_fee += late_fee_dec
                changed = True

            if changed:
                affected_count += 1

    locked_now = False
    if lock_user and tenant.user and tenant.user.status != "locked" and affected_count > 0:
        tenant.user.status = "locked"
        locked_now = True

    db.flush()
    return {
        "tenant_id": tenant_id,
        "affected_payments": affected_count,
        "fee_applied_count": fee_count,
        "total_fee": float(total_fee),
        "locked_user": locked_now,
    }

