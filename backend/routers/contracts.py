"""
Contracts router – CRUD hợp đồng
Admin: full CRUD
User: xem hợp đồng của chính mình
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from dependencies import get_db, get_current_user, require_admin
from models import Contract, Tenant, Room, User
from schemas import ContractOut, ContractCreate, ContractUpdate

router = APIRouter()


def _load_contract(db: Session, contract_id: int) -> Contract:
    c = db.execute(
        select(Contract)
        .options(joinedload(Contract.room).joinedload(Room.images), joinedload(Contract.tenant))
        .where(Contract.contract_id == contract_id)
    ).unique().scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Không tìm thấy hợp đồng")
    return c


@router.get("", response_model=List[ContractOut], summary="Danh sách hợp đồng (Admin)")
def list_contracts(
    status: Optional[str] = Query(None, description="active | ended"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = (
        select(Contract)
        .options(joinedload(Contract.room).joinedload(Room.images), joinedload(Contract.tenant))
        .order_by(Contract.contract_id.desc())
    )
    if status:
        q = q.where(Contract.status == status)
    return db.execute(q).unique().scalars().all()


@router.get("/my", response_model=List[ContractOut], summary="Hợp đồng của tôi")
def my_contracts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tenant = db.execute(
        select(Tenant).where(Tenant.user_id == current_user.user_id)
    ).scalar_one_or_none()
    if not tenant:
        return []
    q = (
        select(Contract)
        .options(joinedload(Contract.room).joinedload(Room.images), joinedload(Contract.tenant))
        .where(Contract.tenant_id == tenant.tenant_id)
        .order_by(Contract.contract_id.desc())
    )
    return db.execute(q).unique().scalars().all()


@router.get("/{contract_id}", response_model=ContractOut, summary="Chi tiết hợp đồng")
def get_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = _load_contract(db, contract_id)
    # User chỉ xem được hợp đồng của mình
    if current_user.role != "admin":
        tenant = db.execute(
            select(Tenant).where(Tenant.user_id == current_user.user_id)
        ).scalar_one_or_none()
        if not tenant or c.tenant_id != tenant.tenant_id:
            raise HTTPException(403, "Bạn không có quyền xem hợp đồng này")
    return c


@router.post("", response_model=ContractOut, status_code=201, summary="Tạo hợp đồng (Admin)")
def create_contract(
    data: ContractCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    room = db.get(Room, data.room_id)
    if not room:
        raise HTTPException(404, "Không tìm thấy phòng")
    tenant = db.get(Tenant, data.tenant_id)
    if not tenant:
        raise HTTPException(404, "Không tìm thấy người thuê")

    contract = Contract(**data.model_dump())
    db.add(contract)
    # Cập nhật trạng thái phòng
    room.status = "occupied"
    db.flush()
    return _load_contract(db, contract.contract_id)


@router.put("/{contract_id}", response_model=ContractOut, summary="Cập nhật hợp đồng (Admin)")
def update_contract(
    contract_id: int,
    data: ContractUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    c = db.get(Contract, contract_id)
    if not c:
        raise HTTPException(404, "Không tìm thấy hợp đồng")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(c, field, value)
    # Nếu status chuyển sang ended, giải phóng phòng
    if data.status == "ended":
        room = db.get(Room, c.room_id)
        if room:
            room.status = "available"
    db.flush()
    return _load_contract(db, contract_id)


@router.delete("/{contract_id}", status_code=204, summary="Xoá hợp đồng (Admin)")
def delete_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    c = db.get(Contract, contract_id)
    if not c:
        raise HTTPException(404, "Không tìm thấy hợp đồng")
    db.delete(c)
    db.flush()
