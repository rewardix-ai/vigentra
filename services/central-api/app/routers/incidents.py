"""Incident ingestion, review and query.

Like detections, this has no computer-vision dependency: the edge detector
watches the vehicle tracking it already runs and ships CANDIDATES here. The
central API stores them, scopes them to the departments an account may read,
and lets an operator dispose of each one - it never upgrades a candidate to a
finding on its own.

An incident is a different object from a detection and lives in a different
table on purpose. A detection is a fact - a box existed at an instant. An
incident is a claim that a pattern in several boxes is worth a human's
attention, and the whole design turns on not letting that claim harden into an
assertion nobody checked. So it carries a status that starts at CANDIDATE, an
evidence block, and a note field for the person who reviews it.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import Incident as IncidentRow
from ..schemas import (
    INCIDENT_KINDS,
    INCIDENT_SEVERITIES,
    INCIDENT_STATUSES,
    IncidentBatch,
    IncidentIngestResult,
    IncidentOut,
    IncidentReview,
)
from ..services import audit_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.policy_service import may_read_detections

logger = logging.getLogger("vigentra.incidents")

router = APIRouter(prefix="/api/v1", tags=["incidents"])


def _incident_id(camera_id: str, kind: str, track_ids: list[int], first_seen: float) -> str:
    """Deterministic id so a re-raised incident collapses onto one row.

    Built from the camera, the pattern, the vehicles involved and roughly when
    it began - stable across a worker restart that re-observes the same event,
    so the review queue is not flooded with duplicates of one incident.
    """
    seed = f"{camera_id}|{kind}|{sorted(track_ids)}|{round(first_seen, 1)}"
    return f"inc_{hashlib.sha1(seed.encode()).hexdigest()[:20]}"


def _to_out(row: IncidentRow, camera: CameraRow | None) -> IncidentOut:
    return IncidentOut(
        incident_id=row.incident_id,
        camera_id=row.camera_id,
        camera_name=camera.name if camera else None,
        owning_department=camera.owning_department if camera else None,
        source_system=row.source_system,
        kind=row.kind,
        severity=row.severity,
        status=row.status,
        track_ids=list(row.track_ids or []),
        first_seen_utc=row.first_seen_utc,
        last_seen_utc=row.last_seen_utc,
        duration_s=round((row.last_seen_utc - row.first_seen_utc).total_seconds(), 2),
        reason=row.reason,
        evidence=row.evidence or {},
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        review_note=row.review_note,
        is_demo_data=row.is_demo_data,
    )


@router.post(
    "/incidents/ingest",
    response_model=IncidentIngestResult,
    summary="Submit incident candidates from an edge worker",
)
async def ingest_incidents(
    payload: IncidentBatch,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_INGEST)),
    db: AsyncSession = Depends(get_db),
) -> IncidentIngestResult:
    """Store incident candidates, scoped to cameras this account may submit for.

    Idempotent on a deterministic id, and per-row: one bad candidate does not
    reject the rest of the batch.

    The detector reports first/last-seen in the stream's own PTS clock, which
    is not wall-clock time. The DURATION between them is meaningful and is
    preserved; the absolute instant is anchored to server-receive time, since
    a candidate is submitted within a frame or two of the pattern matching.
    """
    accepted = duplicates = rejected = 0
    errors: list[dict] = []
    now = datetime.now(timezone.utc)

    camera_ids = {item.camera_id for item in payload.incidents}
    cameras = {
        row.camera_id: row
        for row in (
            await db.execute(select(CameraRow).where(CameraRow.camera_id.in_(camera_ids)))
        ).scalars().all()
    }

    for item in payload.incidents:
        camera = cameras.get(item.camera_id)
        if camera is None:
            rejected += 1
            errors.append({"camera_id": item.camera_id, "code": "UNKNOWN_CAMERA"})
            continue
        if not may_read_detections(user, camera):
            rejected += 1
            errors.append({"camera_id": item.camera_id, "code": "CAMERA_OUT_OF_SCOPE"})
            continue
        if item.kind not in INCIDENT_KINDS:
            rejected += 1
            errors.append({"camera_id": item.camera_id, "code": "UNKNOWN_KIND",
                           "message": f"'{item.kind}' is not a known incident kind."})
            continue
        severity = item.severity if item.severity in INCIDENT_SEVERITIES else "LOW"

        span = max(0.0, float(item.last_seen) - float(item.first_seen))
        last_seen_utc = now
        first_seen_utc = now - timedelta(seconds=span)
        incident_id = _incident_id(item.camera_id, item.kind, item.track_ids, item.first_seen)

        existing = (
            await db.execute(
                select(IncidentRow).where(IncidentRow.incident_id == incident_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Same incident observed again: extend its window, do not duplicate.
            existing.last_seen_utc = last_seen_utc
            existing.evidence = item.evidence or existing.evidence
            duplicates += 1
            continue

        db.add(
            IncidentRow(
                incident_id=incident_id,
                camera_id=item.camera_id,
                source_system=camera.source_system,
                kind=item.kind,
                severity=severity,
                status="CANDIDATE",
                first_seen_utc=first_seen_utc,
                last_seen_utc=last_seen_utc,
                track_ids=list(item.track_ids),
                reason=item.reason,
                evidence=item.evidence or {},
                is_demo_data=camera.provenance.get("surveyed", True) if camera.provenance else True,
            )
        )
        accepted += 1

    await db.commit()
    if accepted:
        logger.info("incident ingest: %d new, %d extended, %d rejected",
                    accepted, duplicates, rejected)
    return IncidentIngestResult(
        accepted=accepted, duplicates=duplicates, rejected=rejected, errors=errors
    )


@router.get("/incidents", response_model=list[IncidentOut], summary="List incident candidates")
async def list_incidents(
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
    camera_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[IncidentOut]:
    """Incidents on cameras this account may read, newest first."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(IncidentRow)
        .where(IncidentRow.last_seen_utc >= cutoff)
        .order_by(IncidentRow.last_seen_utc.desc())
    )
    if camera_id:
        stmt = stmt.where(IncidentRow.camera_id == camera_id)
    if kind:
        stmt = stmt.where(IncidentRow.kind == kind.strip().upper())
    if status_filter:
        stmt = stmt.where(IncidentRow.status == status_filter.strip().upper())

    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    cameras = {
        row.camera_id: row for row in (await db.execute(select(CameraRow))).scalars().all()
    }
    return [
        _to_out(row, cameras.get(row.camera_id))
        for row in rows
        if row.camera_id in cameras and may_read_detections(user, cameras[row.camera_id])
    ]


@router.patch(
    "/incidents/{incident_id}",
    response_model=IncidentOut,
    summary="Record an operator's review of an incident candidate",
)
async def review_incident(
    incident_id: str,
    review: IncidentReview,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
) -> IncidentOut:
    """Move a candidate to REVIEWING / CONFIRMED / DISMISSED, with a note.

    This is the human in the loop the whole design exists to keep: the edge
    raises a pattern, a person decides what it was. The disposition is audited.
    """
    if review.status not in INCIDENT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of {INCIDENT_STATUSES}",
        )
    row = (
        await db.execute(select(IncidentRow).where(IncidentRow.incident_id == incident_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown incident")

    camera = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == row.camera_id))
    ).scalar_one_or_none()
    if camera is None or not may_read_detections(user, camera):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Out of scope")

    row.status = review.status
    row.review_note = review.note
    row.reviewed_by = user.username
    row.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.INCIDENT_REVIEWED,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.DETECTION,
        resource_id=incident_id,
        department=camera.owning_department,
        client_ip=client_ip(request),
        details={"status": review.status, "kind": row.kind},
    )
    return _to_out(row, camera)
