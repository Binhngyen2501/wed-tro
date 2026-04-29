"""
Dashboard router – thống kê tổng quan (Admin only)
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from dependencies import get_db, require_admin
from models import Room, Tenant, Contract, Payment, User
from schemas import DashboardStats

router = APIRouter()


@router.get("", response_model=DashboardStats, summary="Thống kê tổng quan (Admin)")
def get_dashboard(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    rooms = db.execute(select(Room)).scalars().all()
    total_rooms = len(rooms)
    occupied_rooms = sum(1 for r in rooms if r.status == "occupied")
    available_rooms = total_rooms - occupied_rooms
    occupancy_rate = round(occupied_rooms / total_rooms * 100, 1) if total_rooms else 0.0

    total_tenants = db.execute(select(func.count(Tenant.tenant_id))).scalar() or 0
    active_contracts = db.execute(
        select(func.count(Contract.contract_id)).where(Contract.status == "active")
    ).scalar() or 0

    unpaid_rows = db.execute(
        select(Payment).where(Payment.status.in_(["unpaid", "overdue"]))
    ).scalars().all()
    unpaid_count = len(unpaid_rows)
    unpaid_total = float(sum(p.amount for p in unpaid_rows))

    paid_total = float(
        db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")
        ).scalar() or 0
    )

    today = date.today()
    expiring = db.execute(
        select(func.count(Contract.contract_id)).where(
            Contract.status == "active",
            Contract.end_date != None,
        )
    ).scalar() or 0
    # Count those ending within 30 days (Python-side for simplicity)
    active_conts = db.execute(
        select(Contract).where(Contract.status == "active")
    ).scalars().all()
    expiring_30 = sum(
        1 for c in active_conts
        if c.end_date and 0 <= (c.end_date - today).days <= 30
    )

    return DashboardStats(
        total_rooms=total_rooms,
        occupied_rooms=occupied_rooms,
        available_rooms=available_rooms,
        occupancy_rate=occupancy_rate,
        total_tenants=total_tenants,
        active_contracts=active_contracts,
        unpaid_payments=unpaid_count,
        unpaid_total=unpaid_total,
        paid_total=paid_total,
        contracts_expiring_30d=expiring_30,
    )
