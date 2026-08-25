"""Detection ingestion and query.

This module has **no computer-vision dependencies**. Inference happens at the
edge, inside the department environment that already holds the video; the
central API receives only detection metadata. That keeps raw statewide footage
off the wire and keeps this container small enough to deploy anywhere.

Scope: generic object classes only (person, car, motorcycle, bus, truck,
auto-rickshaw, bicycle). No plate text, no face data, no vehicle identity, no
cross-camera association — those are later phases with their own tables and
their own review.

Detections are probabilistic. `confidence` is a model score, and the API says
so on `/detector/health` rather than leaving consumers to assume otherwise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import SettingsDep, client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import Detection as DetectionRow
from ..models import FrameQualityEvent, ModelVersion
from ..schemas import (
    DETECTION_CLASSES,
    DetectionBatch,
    DetectionIngestResult,
    DetectionOut,
    DetectorHealth,
    InstallationStatus,
)
from ..services import audit_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.normalization import to_utc
from ..services.policy_service import may_read_detections

logger = logging.getLogger("sentinel.detections")

router = APIRouter(prefix="/api/v1", tags=["detections"])

#: A detection dated further ahead than this is a clock problem, not a finding.
FUTURE_TOLERANCE = timedelta(minutes=5)


def _plate_access(user: DemoUser, settings: Settings) -> tuple[bool, datetime]:
    """Whether this account may see registration numbers, and from when.

    Retention is enforced on read as well as by any purge job. A plate past its
    retention is not disclosed even if the row is still on disk, so a late or
    failed purge cannot quietly extend how long identifying data is available.
    """
    may = user.can(Permission.PLATE_READ)
    horizon = datetime.now(timezone.utc) - timedelta(
        days=int(settings.anpr_plate_retention_days)
    )
    return may, horizon


def _within_plate_retention(row: DetectionRow, horizon: datetime) -> bool:
    read_at = row.plate_read_at or row.timestamp_utc
    if read_at is None:
        return False
    if read_at.tzinfo is None:
        read_at = read_at.replace(tzinfo=timezone.utc)
    return read_at >= horizon


def _to_out(
    row: DetectionRow, camera: CameraRow | None, *, may_read_plate: bool = False
) -> DetectionOut:
    """Project a stored detection for one reader.

    The plate is withheld unless the account holds `plate:read`. It is withheld
    rather than the whole row refused: an operator counting vehicles at a
    junction has a legitimate need for the detection and no need for the
    registration number, and those are different questions.

    `plate_withheld` is set so the UI can say "withheld for your role" instead
    of showing a blank that reads as "no plate was seen".
    """
    has_plate = bool(row.plate_text)
    disclose = has_plate and may_read_plate
    return DetectionOut(
        detection_id=row.detection_id,
        camera_id=row.camera_id,
        camera_name=camera.name if camera else None,
        owning_department=camera.owning_department if camera else None,
        city=camera.city if camera else None,
        timestamp_utc=row.timestamp_utc,
        class_name=row.class_name,
        class_id=row.class_id,
        confidence=row.confidence,
        bbox_xyxy=list(row.bbox_json or []),
        model_name=row.model_name,
        model_version=row.model_version,
        source_mode=row.source_mode,
        inference_latency_ms=row.inference_latency_ms,
        frame_quality=row.frame_quality,
        evidence_reference=row.evidence_reference,
        is_demo_data=row.is_demo_data,
        provenance=dict(row.provenance or {}),
        created_at=row.created_at,
        plate_text=row.plate_text if disclose else None,
        plate_confidence=row.plate_confidence if disclose else None,
        plate_bbox_xyxy=list(row.plate_bbox_json or []) if disclose else None,
        plate_reader=row.plate_reader if disclose else None,
        plate_withheld=has_plate and not may_read_plate,
    )


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@router.post(
    "/detections/ingest",
    response_model=DetectionIngestResult,
    summary="Ingest a batch of detections from an authorized edge worker",
)
async def ingest_detections(
    payload: DetectionBatch,
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_INGEST)),
    db: AsyncSession = Depends(get_db),
) -> DetectionIngestResult:
    """Validate and store detections.

    Per-row outcomes rather than all-or-nothing: one malformed detection in a
    batch of 200 should not discard the other 199, and the worker gets back
    exactly which rows to fix and resend.

    Idempotent on `detection_id`, which edge workers derive deterministically
    from (camera, frame instant, class, box) — so a replayed batch updates
    rather than inflating counts.
    """
    accepted = duplicates = rejected = 0
    errors: list[dict] = []
    now = datetime.now(timezone.utc)

    # One lookup for the whole batch rather than per row.
    camera_ids = {item.camera_id for item in payload.detections}
    cameras = {
        row.camera_id: row
        for row in (
            await db.execute(select(CameraRow).where(CameraRow.camera_id.in_(camera_ids)))
        ).scalars().all()
    }

    model_seen: tuple[str, str] | None = None

    for item in payload.detections:
        camera = cameras.get(item.camera_id)

        if camera is None:
            rejected += 1
            errors.append({
                "detection_id": item.detection_id,
                "code": "UNKNOWN_CAMERA",
                "message": f"No camera '{item.camera_id}' in the registry.",
            })
            continue

        # A detection against a camera the department has withdrawn is a stale
        # worker still running against a decommissioned asset.
        if camera.installation_status != InstallationStatus.COMMISSIONED.value:
            rejected += 1
            errors.append({
                "detection_id": item.detection_id,
                "code": "CAMERA_NOT_COMMISSIONED",
                "message": (
                    f"Camera '{item.camera_id}' is {camera.installation_status}; "
                    "detections are only accepted for commissioned cameras."
                ),
            })
            continue

        if not may_read_detections(user, camera):
            rejected += 1
            errors.append({
                "detection_id": item.detection_id,
                "code": "CAMERA_OUT_OF_SCOPE",
                "message": (
                    f"This account may not submit detections for "
                    f"'{camera.owning_department}' cameras."
                ),
            })
            continue

        timestamp = to_utc(item.timestamp_utc)
        if timestamp is None or timestamp > now + FUTURE_TOLERANCE:
            rejected += 1
            errors.append({
                "detection_id": item.detection_id,
                "code": "INVALID_TIMESTAMP",
                "message": "timestamp_utc is missing or in the future.",
            })
            continue

        existing = (
            await db.execute(
                select(DetectionRow).where(DetectionRow.detection_id == item.detection_id)
            )
        ).scalar_one_or_none()

        if existing is not None:
            # Idempotent: refresh in place, do not count as a new finding.
            existing.confidence = item.confidence
            existing.bbox_json = list(item.bbox_xyxy)
            existing.frame_quality = (
                item.frame_quality.value if item.frame_quality else None
            )
            existing.inference_latency_ms = item.inference_latency_ms
            duplicates += 1
            continue

        db.add(
            DetectionRow(
                detection_id=item.detection_id,
                camera_id=item.camera_id,
                timestamp_utc=timestamp,
                class_name=item.class_name,
                class_id=item.class_id,
                confidence=item.confidence,
                bbox_json=list(item.bbox_xyxy),
                model_name=item.model_name,
                model_version=item.model_version,
                source_mode=item.source_mode,
                inference_latency_ms=item.inference_latency_ms,
                frame_quality=item.frame_quality.value if item.frame_quality else None,
                evidence_reference=item.evidence_reference,
                is_demo_data=item.is_demo_data,
                plate_text=item.plate_text,
                plate_confidence=item.plate_confidence,
                plate_bbox_json=list(item.plate_bbox_xyxy) if item.plate_bbox_xyxy else None,
                plate_reader=item.plate_reader,
                plate_read_at=timestamp if item.plate_text else None,
                provenance={
                    **dict(item.provenance),
                    "submitted_by": user.username,
                    "submitted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source_system": camera.source_system,
                },
            )
        )
        accepted += 1
        model_seen = (item.model_name, item.model_version)

    # Record the detector build so a result can always be traced to it.
    if model_seen:
        await _touch_model_version(db, model_seen[0], model_seen[1], accepted)

    await db.commit()

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.DETECTIONS_INGESTED,
        outcome=AuditOutcome.PARTIAL if rejected else AuditOutcome.SUCCESS,
        resource_type=ResourceType.DETECTION,
        department=user.department,
        client_ip=client_ip(request),
        details={
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected": rejected,
            "model_name": model_seen[0] if model_seen else None,
            "model_version": model_seen[1] if model_seen else None,
        },
    )

    return DetectionIngestResult(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        errors=errors,
        model_name=model_seen[0] if model_seen else None,
        model_version=model_seen[1] if model_seen else None,
    )


async def _touch_model_version(
    db: AsyncSession, model_name: str, model_version: str, produced: int
) -> None:
    row = (
        await db.execute(
            select(ModelVersion).where(
                ModelVersion.model_name == model_name,
                ModelVersion.model_version == model_version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ModelVersion(
            model_name=model_name,
            model_version=model_version,
            classes=list(DETECTION_CLASSES),
        )
        db.add(row)
    row.last_seen_at = datetime.now(timezone.utc)
    row.detection_count = (row.detection_count or 0) + produced


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

@router.get("/detections", response_model=list[DetectionOut], summary="List detections")
async def list_detections(
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
    camera_id: str | None = Query(default=None),
    class_name: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[DetectionOut]:
    """Detections for cameras this account may read."""
    may_read_plate, plate_horizon = _plate_access(user, settings)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    stmt = (
        select(DetectionRow)
        .where(DetectionRow.timestamp_utc >= cutoff)
        .where(DetectionRow.confidence >= min_confidence)
        .order_by(DetectionRow.timestamp_utc.desc())
    )
    if camera_id:
        stmt = stmt.where(DetectionRow.camera_id == camera_id)
    if class_name:
        stmt = stmt.where(DetectionRow.class_name == class_name.strip().lower())

    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()

    cameras = {
        row.camera_id: row for row in (await db.execute(select(CameraRow))).scalars().all()
    }
    rows = [
        row
        for row in rows
        if row.camera_id in cameras and may_read_detections(user, cameras[row.camera_id])
    ]

    disclosed = 0
    projected = []
    for row in rows:
        allow = may_read_plate and _within_plate_retention(row, plate_horizon)
        if allow and row.plate_text:
            disclosed += 1
        projected.append(_to_out(row, cameras.get(row.camera_id), may_read_plate=allow))

    if disclosed:
        # Reading registration numbers is a distinct, recorded act - not a
        # by-product of listing detections.
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.PLATE_DATA_VIEWED,
            resource_type=ResourceType.DETECTION,
            department=user.department,
            client_ip=client_ip(request),
            details={"plates_disclosed": disclosed, "camera_id": camera_id},
        )
    return projected


@router.get(
    "/detections/{detection_id}",
    response_model=DetectionOut,
    summary="One detection",
)
async def get_detection(
    detection_id: str,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
) -> DetectionOut:
    row = (
        await db.execute(
            select(DetectionRow).where(DetectionRow.detection_id == detection_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown detection '{detection_id}'"
        )
    camera = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == row.camera_id))
    ).scalar_one_or_none()
    if camera is not None and not may_read_detections(user, camera):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This detection belongs to a camera outside your scope.",
        )
    return _to_out(row, camera, may_read_plate=may_read_plate)


@router.get(
    "/cameras/{camera_id}/detections",
    response_model=list[DetectionOut],
    summary="Detections for one camera",
)
async def camera_detections(
    camera_id: str,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[DetectionOut]:
    camera = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == camera_id))
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown camera '{camera_id}'"
        )
    if not may_read_detections(user, camera):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This camera is outside your scope.",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    rows = (
        await db.execute(
            select(DetectionRow)
            .where(DetectionRow.camera_id == camera_id)
            .where(DetectionRow.timestamp_utc >= cutoff)
            .order_by(DetectionRow.timestamp_utc.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [_to_out(row, camera, may_read_plate=may_read_plate) for row in rows]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get(
    "/detector/health",
    response_model=DetectorHealth,
    summary="Analytics configuration and last-seen detector build",
)
async def detector_health(
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
) -> DetectorHealth:
    """Reports the CONFIGURED analytics posture and what has actually reported.

    The central API runs no model itself, so "weights_available" here means
    "an edge worker using these weights has submitted detections", not
    "this container can run inference".
    """
    latest = (
        await db.execute(
            select(ModelVersion).order_by(ModelVersion.last_seen_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    if latest is None:
        detail = (
            "No edge worker has submitted detections yet. Central runs no model; "
            "start services/edge-worker to produce them."
        )
    else:
        # Deliberately no stored-detection total. This endpoint describes the
        # detector, and a running count of how many people and vehicles the
        # state has recorded is a surveillance metric nobody asked for - it
        # belongs in an answer to a query, not on an always-on health panel.
        detail = (
            f"Last detector build seen: {latest.model_name} {latest.model_version}."
        )

    return DetectorHealth(
        enabled=settings.yolo_enable,
        detector="ultralytics-yolo" if settings.yolo_enable else "mock_detector",
        model_name=settings.yolo_model_name,
        model_version=latest.model_version if latest else "not-yet-reported",
        device=settings.yolo_device,
        weights_available=latest is not None,
        classes=list(DETECTION_CLASSES),
        confidence_threshold=settings.yolo_confidence_threshold,
        frame_sample_interval=settings.yolo_frame_sample_interval,
        detail=detail,
    )


@router.post(
    "/detections/frame-quality",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record a frame-quality observation from an edge worker",
)
async def record_frame_quality(
    payload: dict,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_INGEST)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Log a non-normal frame so degraded conditions are visible centrally.

    Useful on its own: a camera that reports `overexposed` every afternoon is a
    siting problem, not an analytics problem.
    """
    camera_id = str(payload.get("camera_id") or "")
    camera = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == camera_id))
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown camera '{camera_id}'"
        )
    if not may_read_detections(user, camera):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Camera outside your scope."
        )

    db.add(
        FrameQualityEvent(
            camera_id=camera_id,
            timestamp_utc=to_utc(payload.get("timestamp_utc")) or datetime.now(timezone.utc),
            quality=str(payload.get("quality") or "normal"),
            mean_luma=payload.get("mean_luma"),
            laplacian_variance=payload.get("laplacian_variance"),
            clipped_highlight_ratio=payload.get("clipped_highlight_ratio"),
            enhancement_applied=payload.get("enhancement_applied"),
            inference_skipped=bool(payload.get("inference_skipped", False)),
            detail=dict(payload.get("detail") or {}),
        )
    )
    await db.commit()
    return {"recorded": True, "camera_id": camera_id}
