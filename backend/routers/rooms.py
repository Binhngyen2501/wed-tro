"""
Rooms router – CRUD phòng trọ
Admin: full CRUD
User: chỉ xem
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from dependencies import get_db, get_current_user, require_admin
from models import Room, RoomImage, User
from schemas import RoomOut, RoomCreate, RoomUpdate

router = APIRouter()


@router.get("", response_model=List[RoomOut], summary="Danh sách phòng")
def list_rooms(
    status: Optional[str] = Query(None, description="available | occupied"),
    khu_vuc: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = select(Room).options(joinedload(Room.images)).order_by(Room.room_id.desc())
    if status:
        q = q.where(Room.status == status)
    if khu_vuc:
        q = q.where(Room.khu_vuc == khu_vuc)
    return db.execute(q).unique().scalars().all()


@router.get("/{room_id}", response_model=RoomOut, summary="Chi tiết phòng")
def get_room(
    room_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    room = db.execute(
        select(Room).options(joinedload(Room.images)).where(Room.room_id == room_id)
    ).unique().scalar_one_or_none()
    if not room:
        raise HTTPException(404, "Không tìm thấy phòng")
    return room


@router.post("", response_model=RoomOut, status_code=201, summary="Tạo phòng mới (Admin)")
def create_room(
    data: RoomCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    existing = db.execute(select(Room).where(Room.room_code == data.room_code)).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Mã phòng '{data.room_code}' đã tồn tại")
    room = Room(owner_id=admin.user_id, **data.model_dump())
    db.add(room)
    db.flush()
    db.refresh(room)
    return room


@router.put("/{room_id}", response_model=RoomOut, summary="Cập nhật phòng (Admin)")
def update_room(
    room_id: int,
    data: RoomUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "Không tìm thấy phòng")
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(room, field, value)
    db.flush()
    db.refresh(room)
    return room


@router.delete("/{room_id}", status_code=204, summary="Xoá phòng (Admin)")
def delete_room(
    room_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "Không tìm thấy phòng")
    db.delete(room)
    db.flush()
