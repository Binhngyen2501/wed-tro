"""
Price suggestions router – gợi ý giá thuê AI
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from dependencies import get_db, get_current_user
from models import PriceSuggestion, Room, User
from schemas import PriceSuggestionOut

router = APIRouter()


@router.get("", response_model=List[PriceSuggestionOut], summary="Tất cả gợi ý giá (Admin/User)")
def list_suggestions(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return db.execute(
        select(PriceSuggestion).order_by(PriceSuggestion.suggestion_id.desc())
    ).scalars().all()


@router.get("/room/{room_id}", response_model=List[PriceSuggestionOut], summary="Gợi ý giá cho phòng")
def room_suggestions(
    room_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(404, "Không tìm thấy phòng")
    return db.execute(
        select(PriceSuggestion)
        .where(PriceSuggestion.room_id == room_id)
        .order_by(PriceSuggestion.suggestion_id.desc())
    ).scalars().all()
