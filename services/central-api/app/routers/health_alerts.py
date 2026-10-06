"""Camera-health alerts: list them, count them, and say someone is on one.

Raised and closed by the health monitor (`services/health_alerts.py`); this router only reads
them, scoped like the registry, and records a person acknowledging one. Acknowledging does not
close an alert: it stays open until the camera, or the department system, answers again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import HealthAlert
from ..schemas import HealthAlertAcknowledge, HealthAlertOut
from ..services import audit_service
from ..services.audit_service import AuditAction, ResourceType
from ..services.policy_service import may_read_camera

router = APIRouter(prefix="/api/v1/health-alerts", tags=["health alerts"])


async def _visible(db: AsyncSession, user: DemoUser, rows: list[HealthAlert]):
    """The alerts this account may see, with each camera's row: a camera alert follows the
    camera's visibility, a department-system alert the account's departments."""
    cameras = {row.camera_id: row for row in (await db.execute(select(CameraRow))).scalars().all()}
    out = []
    for row in rows:
        camera = cameras.get(row.camera_id) if row.camera_id else None
        if row.camera_id:
            if camera is None or not may_read_camera(user, camera):
                continue
        elif row.department and not user.may_access_department(row.department):
            continue
        out.append((row, camera))
    return out


def _out(row: HealthAlert, camera: CameraRow | None) -> HealthAlertOut:
    return HealthAlertOut(
        alert_id=row.alert_id, kind=row.kind, camera_id=row.camera_id,
        camera_name=camera.name if camera is not None else None, source_system=row.source_system,
        department=row.department, city=camera.city if camera is not None else None,
        district=camera.district if camera is not None else None, detail=row.detail,
        raised_at=row.raised_at, recovered_at=row.recovered_at, acknowledged=row.acknowledged,
        acknowledged_by=row.acknowledged_by, acknowledged_at=row.acknowledged_at, note=row.note,
    )


@router.get("", response_model=list[HealthAlertOut], summary="Cameras and department systems that stopped answering")
async def list_health_alerts(
    user: DemoUser = Depends(require_permission(Permission.HEALTH_READ)),
    db: AsyncSession = Depends(get_db),
    open_only: bool = Query(default=False, description="Only alerts whose camera or system is still down"),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[HealthAlertOut]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = select(HealthAlert).where(HealthAlert.raised_at >= cutoff).order_by(HealthAlert.raised_at.desc()).limit(limit)
    if open_only:
        stmt = stmt.where(HealthAlert.recovered_at.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    # Still down first, then the newest. Not an audited read: it discloses no plate and no footage.
    visible = sorted(await _visible(db, user, list(rows)), key=lambda pair: pair[0].recovered_at is not None)
    return [_out(row, camera) for row, camera in visible]


@router.get("/open-count", summary="How many cameras or systems are down and not yet taken up")
async def open_health_alert_count(
    user: DemoUser = Depends(require_permission(Permission.HEALTH_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    rows = (await db.execute(select(HealthAlert).where(
        HealthAlert.recovered_at.is_(None), HealthAlert.acknowledged.is_(False)))).scalars().all()
    return {"open": len(await _visible(db, user, list(rows)))}


@router.post("/{alert_id}/acknowledge", response_model=HealthAlertOut, summary="Someone is on it")
async def acknowledge_health_alert(
    alert_id: str,
    payload: HealthAlertAcknowledge,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.HEALTH_ACKNOWLEDGE)),
    db: AsyncSession = Depends(get_db),
) -> HealthAlertOut:
    row = (await db.execute(select(HealthAlert).where(HealthAlert.alert_id == alert_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such health alert.")
    visible = await _visible(db, user, [row])
    if not visible:
        await audit_service.record(
            db, username=user.username, role=user.role, action=AuditAction.HEALTH_ALERT_ACKNOWLEDGED,
            outcome=audit_service.AuditOutcome.DENIED, resource_type=ResourceType.HEALTH_ALERT,
            resource_id=alert_id, department=user.department, client_ip=client_ip(request),
            details={"code": "OUT_OF_SCOPE"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"code": "OUT_OF_SCOPE", "message": "This alert is outside your scope."})
    row.acknowledged = True
    row.acknowledged_by = user.username
    row.acknowledged_at = datetime.now(timezone.utc)
    row.note = payload.note
    await db.commit()
    await db.refresh(row)
    await audit_service.record(
        db, username=user.username, role=user.role, action=AuditAction.HEALTH_ALERT_ACKNOWLEDGED,
        resource_type=ResourceType.HEALTH_ALERT, resource_id=row.alert_id, department=user.department,
        source_system=row.source_system, case_or_reason=payload.note, client_ip=client_ip(request),
        details={"kind": row.kind, "camera_id": row.camera_id})
    return _out(row, visible[0][1])
