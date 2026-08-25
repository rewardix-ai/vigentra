"""Normalised generic events.

Motion, line-crossing, tamper, device-health. Module 1 scope: no ANPR, plate,
vehicle, face or identity fields — Phase 2 detection modules extend
``Event.payload`` and add their own tables against the same registry.

Also serves the cross-camera correlation view: pairs of events at
geographically nearby cameras within a small time window, useful for spotting
that the same disturbance was picked up by two neighbouring cameras.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.base import AdapterError, SurveillanceAdapter
from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import AdaptersDep, client_ip, require_permission
from ..models import Camera as CameraRow, Event as EventRow
from ..schemas import CorrelationPair, CorrelationResponse, EventOut
from ..services import audit_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.normalization import to_utc
from ..services.policy_service import may_read_detections

logger = logging.getLogger("sentinel.events")

router = APIRouter(prefix="/api/v1/events", tags=["events"])


# ---------------------------------------------------------------------------
# Refresh helpers
# ---------------------------------------------------------------------------

async def _pull_source(
    db: AsyncSession, adapter: SurveillanceAdapter, cameras: list[CameraRow]
) -> int:
    """Pull the latest events for one department, upsert deterministically.

    Adapter failures are logged and swallowed so one broken source cannot
    stall the read for the other one.
    """
    ingested = 0
    for camera in cameras:
        if camera.source_system != adapter.source_system:
            continue
        try:
            events = await adapter.fetch_events(camera.external_camera_id)
        except AdapterError as exc:
            logger.warning(
                "event pull failed for %s: %s", camera.camera_id, exc
            )
            continue
        for event in events:
            row = (
                await db.execute(select(EventRow).where(EventRow.event_id == event.event_id))
            ).scalar_one_or_none()
            if row is None:
                row = EventRow(event_id=event.event_id)
                db.add(row)
                ingested += 1
            row.source_system = event.source_system
            row.external_event_id = event.external_event_id
            row.camera_id = event.camera_id
            row.event_type = event.event_type
            row.severity = event.severity
            row.timestamp_utc = to_utc(event.timestamp_utc)  # type: ignore[assignment]
            row.payload = dict(event.payload)
            row.provenance = dict(event.provenance)
    return ingested


async def _refresh(
    db: AsyncSession,
    adapters: dict[str, SurveillanceAdapter],
    user: DemoUser,
) -> None:
    cameras = (await db.execute(select(CameraRow))).scalars().all()
    permitted = [
        adapter
        for source_system, adapter in adapters.items()
        if any(
            user.may_access_department(camera.owning_department)
            for camera in cameras
            if camera.source_system == source_system
        )
    ]
    await asyncio.gather(*(_pull_source(db, adapter, list(cameras)) for adapter in permitted))
    await db.commit()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[EventOut], summary="List normalised generic events")
async def list_events(
    request: Request,
    adapters: AdaptersDep,
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
    camera_id: str | None = Query(default=None),
    source_system: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    since_hours: int = Query(default=6, ge=1, le=168),
    refresh: bool = Query(default=True, description="Pull the latest from every department"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[EventOut]:
    if refresh:
        await _refresh(db, adapters, user)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = select(EventRow).where(EventRow.timestamp_utc >= cutoff).order_by(
        EventRow.timestamp_utc.desc()
    )
    if camera_id:
        stmt = stmt.where(EventRow.camera_id == camera_id)
    if source_system:
        stmt = stmt.where(EventRow.source_system == source_system)
    if event_type:
        stmt = stmt.where(EventRow.event_type == event_type)
    rows = (await db.execute(stmt.limit(limit))).scalars().all()

    cameras = {
        row.camera_id: row for row in (await db.execute(select(CameraRow))).scalars().all()
    }
    rows = [row for row in rows if row.camera_id in cameras and may_read_detections(user, cameras[row.camera_id])]

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.CAMERA_REGISTRY_VIEWED,
        resource_type=ResourceType.CAMERA,
        department=user.department,
        client_ip=client_ip(request),
        details={"scope": "events", "returned": len(rows), "since_hours": since_hours},
    )

    return [
        EventOut(
            event_id=row.event_id,
            source_system=row.source_system,
            external_event_id=row.external_event_id,
            camera_id=row.camera_id,
            event_type=row.event_type,
            severity=row.severity,
            timestamp_utc=row.timestamp_utc,
            payload=row.payload or {},
            provenance=row.provenance or {},
            camera_name=cameras[row.camera_id].name,
            owning_department=cameras[row.camera_id].owning_department,
            district=cameras[row.camera_id].district,
            ingested_at=row.ingested_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres. Precise enough for hundred-metre bands."""
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


@router.get(
    "/correlation",
    response_model=CorrelationResponse,
    summary="Events at nearby cameras within a small time window",
)
async def correlation(
    request: Request,
    adapters: AdaptersDep,
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
    window_seconds: int = Query(default=180, ge=5, le=3600),
    radius_m: int = Query(default=1500, ge=10, le=20_000),
    since_hours: int = Query(default=6, ge=1, le=168),
    refresh: bool = Query(default=True),
) -> CorrelationResponse:
    """Pair up events likely describing the same underlying disturbance."""
    if refresh:
        await _refresh(db, adapters, user)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    cameras = {
        row.camera_id: row for row in (await db.execute(select(CameraRow))).scalars().all()
    }

    stmt = (
        select(EventRow)
        .where(EventRow.timestamp_utc >= cutoff)
        .order_by(EventRow.timestamp_utc.asc())
    )
    rows = [
        row
        for row in (await db.execute(stmt)).scalars().all()
        if row.camera_id in cameras and may_read_detections(user, cameras[row.camera_id])
    ]

    def to_out(row: EventRow) -> EventOut:
        cam = cameras[row.camera_id]
        return EventOut(
            event_id=row.event_id,
            source_system=row.source_system,
            external_event_id=row.external_event_id,
            camera_id=row.camera_id,
            event_type=row.event_type,
            severity=row.severity,
            timestamp_utc=row.timestamp_utc,
            payload=row.payload or {},
            provenance=row.provenance or {},
            camera_name=cam.name,
            owning_department=cam.owning_department,
            district=cam.district,
            ingested_at=row.ingested_at,
        )

    pairs: list[CorrelationPair] = []
    for i, a in enumerate(rows):
        cam_a = cameras[a.camera_id]
        if cam_a.latitude is None or cam_a.longitude is None:
            continue
        for b in rows[i + 1 :]:
            delta = (b.timestamp_utc - a.timestamp_utc).total_seconds()
            if delta > window_seconds:
                break  # rows are time-ordered
            if a.camera_id == b.camera_id:
                continue
            cam_b = cameras[b.camera_id]
            if cam_b.latitude is None or cam_b.longitude is None:
                continue
            distance = _haversine_m(cam_a.latitude, cam_a.longitude, cam_b.latitude, cam_b.longitude)
            if distance > radius_m:
                continue
            pairs.append(
                CorrelationPair(
                    a=to_out(a),
                    b=to_out(b),
                    distance_m=round(distance, 1),
                    delta_seconds=round(delta, 1),
                    cross_department=cam_a.owning_department != cam_b.owning_department,
                )
            )

    pairs.sort(key=lambda pair: (pair.distance_m, pair.delta_seconds))

    return CorrelationResponse(
        window_seconds=window_seconds,
        radius_m=radius_m,
        considered=len(rows),
        pairs=pairs,
    )
