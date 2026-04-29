"""
Audit logs router – lịch sử thao tác (Admin only)
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from dependencies import get_db, require_admin
from models import AuditLog, User
from schemas import AuditLogOut

router = APIRouter()


@router.get("", response_model=List[AuditLogOut], summary="Lịch sử audit (Admin)")
def list_audit_logs(
    entity_name: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    actor_user_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    q = select(AuditLog).order_by(AuditLog.audit_id.desc()).limit(limit)
    if entity_name:
        q = q.where(AuditLog.entity_name == entity_name)
    if action:
        q = q.where(AuditLog.action == action)
    if actor_user_id:
        q = q.where(AuditLog.actor_user_id == actor_user_id)
    return db.execute(q).scalars().all()
