"""Audit trail.

Reading the trail is itself audited, but only for filtered or targeted queries -
the audit page polls the unfiltered view, and recording that would bury the
operational entries the page exists to show.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import client_ip, require_permission
from ..mappers import audit_to_schema
from ..models import AuditLog
from ..schemas import AuditOut
from ..services import audit_service
from ..services.audit_service import AuditAction, ResourceType
from ..services.normalization import to_utc

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=list[AuditOut], summary="Read the audit trail")
async def list_audit(
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
    username: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    department: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AuditOut]:
    stmt = select(AuditLog).order_by(AuditLog.timestamp_utc.desc())
    if username:
        stmt = stmt.where(AuditLog.username == username)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if department:
        stmt = stmt.where(AuditLog.department == department)
    if outcome:
        stmt = stmt.where(AuditLog.outcome == outcome)
    if start:
        stmt = stmt.where(AuditLog.timestamp_utc >= to_utc(start))
    if end:
        stmt = stmt.where(AuditLog.timestamp_utc <= to_utc(end))

    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()

    # A departmental auditor sees their own department's trail, plus the
    # platform-level entries that carry no department at all.
    if not user.is_statewide:
        rows = [row for row in rows if row.department in (None, user.department)]

    targeted = any(
        [username, action, resource_type, resource_id, department, outcome, start, end]
    )
    if targeted:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.AUDIT_VIEWED,
            resource_type=ResourceType.AUDIT_LOG,
            resource_id=resource_id,
            department=user.department,
            client_ip=client_ip(request),
            details={"returned": len(rows), "filtered": True},
        )

    return [audit_to_schema(row) for row in rows]
