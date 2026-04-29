from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models import AuditLog


def write_audit_log(
    db: Session,
    actor_user_id: int | None,
    entity_name: str,
    entity_id: str,
    action: str,
    old_data: dict[str, Any] | None = None,
    new_data: dict[str, Any] | None = None,
) -> None:
    log = AuditLog(
        actor_user_id=actor_user_id,
        entity_name=entity_name,
        entity_id=entity_id,
        action=action,
        old_data=old_data,
        new_data=new_data,
    )
    db.add(log)
