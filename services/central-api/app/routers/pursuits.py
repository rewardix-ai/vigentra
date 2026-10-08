"""Pursuit: follow one designated vehicle across the cameras, now.

The event test names one registration and asks for its route. Reading plates open-ended on thirty
cameras at once gives a quiet camera a frame every few seconds, and a passing vehicle a frame or two.
A pursuit turns that around for one vehicle:

- the cameras where it was seen in the last HOT_MINUTES, and the NEIGHBOURS nearest each on the map,
  are "hot": edge readers poll GET /pursuits and give those cameras first claim on their frames;
- edge readers check every vehicle they close against the plate ("is this plate P?", which accepts a
  blurrier plate than reading one open-ended) and send matches back as POSSIBLE sightings, with the plate
  crop, for a person to confirm. They appear on the route marked as possible, never as confirmed.

Started and ended by a named operator with a reason; both audited, as is every look at the evidence.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import CurrentUser, client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import PlateSighting, Pursuit, PursuitEvidence
from ..services import audit_service, plate_matching
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.policy_service import may_read_detections
from ..services.track_service import haversine_km

router = APIRouter(prefix="/api/v1", tags=["pursuit"])

HOT_MINUTES = 20        # a camera stays hot this long after the vehicle was seen there
NEIGHBOURS = 3          # ...and so do this many nearest cameras on the map
MATCH_DISTANCE = 1.0    # a sighting this close to the plate (plate_matching) counts as the vehicle
EVIDENCE_MAX_BYTES = 256 * 1024


class PursuitStart(BaseModel):
    plate: str = Field(min_length=4, max_length=24)
    reason: str = Field(min_length=3, max_length=500)


class PossibleSighting(BaseModel):
    camera_id: str
    track: str = Field(max_length=64)
    read_as: str | None = Field(default=None, max_length=24)
    target_score: float
    crops: int = 1
    crop_jpeg_b64: str | None = Field(default=None, max_length=360_000)


def _readers_or_edge(user: CurrentUser) -> DemoUser:
    """Operators who may trace, and the edge readers that act on a pursuit."""
    if not (user.can(Permission.TRACK_READ) or user.can(Permission.DETECTION_INGEST)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Neither tracing nor detection ingest is permitted")
    return user


async def _hot_cameras(db: AsyncSession, plate: str, cameras: list[CameraRow]) -> list[str]:
    since = datetime.now(timezone.utc) - timedelta(minutes=HOT_MINUTES)
    rows = (await db.execute(select(PlateSighting).where(PlateSighting.timestamp_utc >= since))).scalars().all()
    seen = {r.camera_id for r in rows if plate_matching.plate_distance(plate, r.plate_normalised) <= MATCH_DISTANCE}
    placed = [c for c in cameras if c.latitude is not None and c.longitude is not None]
    hot = set(seen)
    for cam in [c for c in placed if c.camera_id in seen]:
        nearest = sorted((haversine_km(cam.latitude, cam.longitude, o.latitude, o.longitude), o.camera_id)
                         for o in placed if o.camera_id != cam.camera_id)
        hot.update(cid for _, cid in nearest[:NEIGHBOURS])
    return sorted(hot)


@router.post("/pursuits", status_code=status.HTTP_201_CREATED, summary="Start following a vehicle")
async def start_pursuit(
    body: PursuitStart, request: Request,
    user: DemoUser = Depends(require_permission(Permission.TRACK_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    plate = plate_matching.clean(body.plate)
    if not plate_matching.is_plausible(plate):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a plausible registration number")
    existing = (await db.execute(select(Pursuit).where(Pursuit.active.is_(True), Pursuit.plate_normalised == plate))).scalars().first()
    row = existing or Pursuit(plate_normalised=plate, reason=body.reason.strip(), started_by=user.username)
    if existing is None:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        await audit_service.record(db, username=user.username, role=user.role, action=AuditAction.PURSUIT_STARTED,
                                   outcome=AuditOutcome.SUCCESS, resource_type=ResourceType.PLATE_SIGHTING,
                                   resource_id=plate, department=user.department, case_or_reason=row.reason,
                                   client_ip=client_ip(request), details={"pursuit_id": row.id})
    return {"id": row.id, "plate": row.plate_normalised, "started_by": row.started_by, "started_at": row.started_at,
            "already_active": existing is not None}


@router.post("/pursuits/{pursuit_id}/end", summary="Stop following a vehicle")
async def end_pursuit(
    pursuit_id: int, request: Request,
    user: DemoUser = Depends(require_permission(Permission.TRACK_READ)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(Pursuit, pursuit_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pursuit")
    if row.active:
        row.active, row.ended_at = False, datetime.now(timezone.utc)
        await db.commit()
        await audit_service.record(db, username=user.username, role=user.role, action=AuditAction.PURSUIT_ENDED,
                                   outcome=AuditOutcome.SUCCESS, resource_type=ResourceType.PLATE_SIGHTING,
                                   resource_id=row.plate_normalised, department=user.department,
                                   client_ip=client_ip(request), details={"pursuit_id": row.id})
    return {"id": row.id, "plate": row.plate_normalised, "active": row.active}


@router.get("/pursuits", summary="Active pursuits and the cameras to watch for each")
async def active_pursuits(
    user: DemoUser = Depends(_readers_or_edge),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (await db.execute(select(Pursuit).where(Pursuit.active.is_(True)).order_by(Pursuit.id))).scalars().all()
    cameras = (await db.execute(select(CameraRow))).scalars().all()
    out = []
    for row in rows:
        out.append({"id": row.id, "plate": row.plate_normalised, "started_by": row.started_by,
                    "started_at": row.started_at, "hot_cameras": await _hot_cameras(db, row.plate_normalised, cameras)})
    return {"pursuits": out, "hot_minutes": HOT_MINUTES, "neighbours": NEIGHBOURS}


@router.post("/pursuits/{pursuit_id}/possible", status_code=status.HTTP_201_CREATED,
             summary="An edge reader's possible sighting of the pursued vehicle")
async def possible_sighting(
    pursuit_id: int, body: PossibleSighting,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_INGEST)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await db.get(Pursuit, pursuit_id)
    if row is None or not row.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such active pursuit")
    camera = (await db.execute(select(CameraRow).where(CameraRow.camera_id == body.camera_id))).scalar_one_or_none()
    if camera is None or not may_read_detections(user, camera):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Camera out of scope")
    detection_id = f"possible_{row.id}_{body.camera_id}_{body.track}"[:128]
    if (await db.execute(select(PlateSighting).where(PlateSighting.detection_id == detection_id))).scalar_one_or_none():
        return {"duplicate": True}
    sighting_id = f"poss_{hashlib.sha1(detection_id.encode()).hexdigest()[:20]}"
    db.add(PlateSighting(
        sighting_id=sighting_id, detection_id=detection_id, plate_text=row.plate_normalised,
        plate_normalised=row.plate_normalised, state_code=plate_matching.state_of(row.plate_normalised),
        camera_id=body.camera_id, timestamp_utc=datetime.now(timezone.utc),
        confidence=0.3, observations=max(1, body.crops), reader="target-check",
        is_demo_data=camera.provenance.get("surveyed", True) if camera.provenance else True,
        provenance={"possible": True, "pursuit_id": row.id, "target_score": round(body.target_score, 3),
                    "read_as": body.read_as, "track": body.track}))
    if body.crop_jpeg_b64:
        try:
            data = base64.b64decode(body.crop_jpeg_b64, validate=True)
            if data.startswith(b"\xff\xd8") and len(data) <= EVIDENCE_MAX_BYTES:
                db.add(PursuitEvidence(sighting_id=sighting_id, camera_id=body.camera_id, jpeg=data))
        except (binascii.Error, ValueError):
            pass
    await db.commit()
    return {"sighting_id": sighting_id, "plate": row.plate_normalised}


@router.get("/pursuits/evidence/{sighting_id}", summary="The plate crop behind a possible sighting",
            responses={200: {"content": {"image/jpeg": {}}}})
async def pursuit_evidence(
    sighting_id: str, request: Request,
    user: DemoUser = Depends(require_permission(Permission.TRACK_READ)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    ev = await db.get(PursuitEvidence, sighting_id)
    if ev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evidence for this sighting")
    camera = (await db.execute(select(CameraRow).where(CameraRow.camera_id == ev.camera_id))).scalar_one_or_none()
    if camera is None or not may_read_detections(user, camera):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Out of scope")
    await audit_service.record(db, username=user.username, role=user.role, action=AuditAction.PURSUIT_EVIDENCE_VIEWED,
                               outcome=AuditOutcome.SUCCESS, resource_type=ResourceType.PLATE_SIGHTING,
                               resource_id=sighting_id, department=camera.owning_department,
                               client_ip=client_ip(request), details={"camera_id": ev.camera_id})
    return Response(content=ev.jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
