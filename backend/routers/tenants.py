"""
Tenants router – CRUD hồ sơ người thuê (Admin)
User: xem hồ sơ của chính mình
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from dependencies import get_db, get_current_user, require_admin
from models import Tenant, User
from schemas import TenantOut, TenantCreate, TenantUpdate

router = APIRouter()


@router.get("", response_model=List[TenantOut], summary="Danh sách người thuê (Admin)")
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


@router.get("/me", response_model=Optional[TenantOut], summary="Hồ sơ người thuê của tôi")
def my_tenant(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant = db.execute(
        select(Tenant).where(Tenant.user_id == current_user.user_id)
    ).scalar_one_or_none()
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut, summary="Chi tiết người thuê (Admin)")
def get_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Không tìm thấy người thuê")
    return tenant


@router.post("", response_model=TenantOut, status_code=201, summary="Thêm người thuê (Admin)")
def create_tenant(
    data: TenantCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    if data.user_id:
        dup = db.execute(select(Tenant).where(Tenant.user_id == data.user_id)).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "User này đã có hồ sơ người thuê")
    tenant = Tenant(**data.model_dump())
    db.add(tenant)
    db.flush()
    db.refresh(tenant)
    return tenant


@router.put("/{tenant_id}", response_model=TenantOut, summary="Cập nhật người thuê (Admin)")
def update_tenant(
    tenant_id: int,
    data: TenantUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Không tìm thấy người thuê")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)
    db.flush()
    db.refresh(tenant)
    return tenant


@router.delete("/{tenant_id}", status_code=204, summary="Xoá người thuê (Admin)")
def delete_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(404, "Không tìm thấy người thuê")
    db.delete(tenant)
    db.flush()
