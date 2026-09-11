"""Watchlist, alerts, plate search, movement history and the ANPR report.

Five endpoints groups, one subject: what the network saw, and which of it
somebody asked to be told about.

Everything here is behind its own permission, and the permissions are
deliberately not interchangeable. Reading the watchlist is oversight; adding to
it is a standing instruction to flag a vehicle statewide; acknowledging an
alert is an operational decision; and asking where a registration number has
been is the most revealing query this platform answers. An account may hold any
one without the others, and every disclosure is audited.

Camera scope applies throughout. A sighting is derived from footage, so it
follows the video rules rather than the metadata ones (see
`policy_service.may_read_detections`): an account never sees a plate read from
a camera whose detections it could not have listed, and a route is never
assembled from a sighting the caller could not have seen. Scope is applied
*before* assembly rather than after, so a filtered route is a shorter route
rather than a route with holes attributed to the vehicle.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import SettingsDep, client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import PlateSighting, WatchlistAlert, WatchlistEntry
from ..schemas import (
    AlertAcknowledge,
    AlertOut,
    AnalyticsReportRow,
    PlateSearchHit,
    SightingOut,
    TrackOut,
    TrackPointOut,
    WatchlistDeactivate,
    WatchlistEntryCreate,
    WatchlistEntryOut,
)
from ..services import audit_service, plate_matching, track_service, watchlist_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.policy_service import may_read_detections

logger = logging.getLogger("vigentra.watchlist.api")

router = APIRouter(prefix="/api/v1", tags=["watchlist"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _readable_cameras(db: AsyncSession, user: DemoUser) -> dict[str, CameraRow]:
    """Every camera whose observations this account may read, by id."""
    rows = (await db.execute(select(CameraRow))).scalars().all()
    return {row.camera_id: row for row in rows if may_read_detections(user, row)}


def _plate_horizon(settings) -> datetime:
    """The oldest plate this deployment still discloses.

    Enforced on read, not only by a purge job: a late or failed purge must not
    quietly extend how long identifying data stays available.
    """
    return datetime.now(timezone.utc) - timedelta(
        days=int(settings.anpr_plate_retention_days)
    )


def _within_retention(moment: datetime | None, horizon: datetime) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment >= horizon


def _location_of(camera: CameraRow | None) -> str | None:
    if camera is None:
        return None
    parts = [camera.road_or_junction, camera.landmark, camera.city, camera.district]
    return ", ".join(part for part in parts if part) or None


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

@router.get(
    "/watchlist",
    response_model=list[WatchlistEntryOut],
    summary="List watchlist entries",
)
async def list_watchlist(
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.WATCHLIST_READ)),
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(default=True),
    category: str | None = Query(default=None),
) -> list[WatchlistEntryOut]:
    statement = select(WatchlistEntry).order_by(WatchlistEntry.created_at.desc())
    if active_only:
        statement = statement.where(WatchlistEntry.active.is_(True))
    if category:
        statement = statement.where(WatchlistEntry.category == category.strip().lower())
    rows = (await db.execute(statement)).scalars().all()

    counts = dict(
        (
            await db.execute(
                select(WatchlistAlert.watch_plate, func.count(WatchlistAlert.id)).group_by(
                    WatchlistAlert.watch_plate
                )
            )
        ).all()
    )

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.WATCHLIST_VIEWED,
        resource_type=ResourceType.WATCHLIST_ENTRY,
        department=user.department,
        client_ip=client_ip(request),
        details={"returned": len(rows), "active_only": active_only},
    )

    return [
        WatchlistEntryOut(
            entry_id=row.entry_id,
            plate=row.plate,
            category=row.category,
            reason=row.reason,
            case_reference=row.case_reference,
            added_by=row.added_by,
            owning_department=row.owning_department,
            active=row.active,
            expires_at=row.expires_at,
            deactivated_at=row.deactivated_at,
            deactivated_by=row.deactivated_by,
            is_demo_data=row.is_demo_data,
            created_at=row.created_at,
            alert_count=counts.get(row.plate, 0),
        )
        for row in rows
    ]


@router.post(
    "/watchlist",
    response_model=WatchlistEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a registration number to the watchlist",
)
async def add_watchlist_entry(
    payload: WatchlistEntryCreate,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.WATCHLIST_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WatchlistEntryOut:
    """Add an entry.

    Refuses a plate that is not shaped like an Indian registration, rather than
    storing it and leaving an operator to wonder why it never fires. A string
    the matcher can never match is not a watchlist entry; it is a typo.
    """
    plate = plate_matching.clean(payload.plate)
    if not plate_matching.is_plausible(plate):
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.WATCHLIST_ENTRY_ADDED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.WATCHLIST_ENTRY,
            department=user.department,
            client_ip=client_ip(request),
            details={"submitted": payload.plate, "code": "IMPLAUSIBLE_PLATE"},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "IMPLAUSIBLE_PLATE",
                "message": (
                    f"'{payload.plate}' is not shaped like an Indian registration "
                    "number, so nothing would ever match it."
                ),
            },
        )

    existing = (
        await db.execute(select(WatchlistEntry).where(WatchlistEntry.plate == plate))
    ).scalar_one_or_none()
    if existing is not None and existing.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ALREADY_WATCHED",
                "message": f"{plate} is already on the watchlist.",
                "entry_id": existing.entry_id,
            },
        )

    if existing is not None:
        # Re-activating a stood-down entry keeps its history rather than
        # creating a second row for the same vehicle.
        existing.active = True
        existing.category = payload.category.value
        existing.reason = payload.reason
        existing.case_reference = payload.case_reference
        existing.expires_at = payload.expires_at
        existing.added_by = user.username
        existing.deactivated_at = None
        existing.deactivated_by = None
        row = existing
    else:
        row = WatchlistEntry(
            entry_id=watchlist_service.new_entry_id(),
            plate=plate,
            category=payload.category.value,
            reason=payload.reason,
            case_reference=payload.case_reference,
            added_by=user.username,
            owning_department=None if user.is_statewide else user.department,
            active=True,
            expires_at=payload.expires_at,
            is_demo_data=True,
        )
        db.add(row)

    await db.commit()
    await db.refresh(row)
    # The matcher reads a short-lived cache; drop it so the very next camera
    # catches this vehicle rather than the one after the TTL.
    watchlist_service.invalidate_cache()

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.WATCHLIST_ENTRY_ADDED,
        resource_type=ResourceType.WATCHLIST_ENTRY,
        resource_id=row.entry_id,
        department=user.department,
        case_or_reason=payload.reason,
        client_ip=client_ip(request),
        details={
            "plate": plate,
            "category": row.category,
            "case_reference": row.case_reference,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        },
    )

    return WatchlistEntryOut(
        entry_id=row.entry_id,
        plate=row.plate,
        category=row.category,
        reason=row.reason,
        case_reference=row.case_reference,
        added_by=row.added_by,
        owning_department=row.owning_department,
        active=row.active,
        expires_at=row.expires_at,
        is_demo_data=row.is_demo_data,
        created_at=row.created_at,
    )


@router.delete(
    "/watchlist/{entry_id}",
    response_model=WatchlistEntryOut,
    summary="Stand down a watchlist entry",
)
async def deactivate_watchlist_entry(
    entry_id: str,
    payload: WatchlistDeactivate,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.WATCHLIST_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WatchlistEntryOut:
    """Deactivate an entry. It is never deleted — the trail is the point."""
    row = (
        await db.execute(select(WatchlistEntry).where(WatchlistEntry.entry_id == entry_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such watchlist entry.")

    row.active = False
    row.deactivated_at = datetime.now(timezone.utc)
    row.deactivated_by = user.username
    await db.commit()
    await db.refresh(row)
    watchlist_service.invalidate_cache()

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.WATCHLIST_ENTRY_DEACTIVATED,
        resource_type=ResourceType.WATCHLIST_ENTRY,
        resource_id=row.entry_id,
        department=user.department,
        case_or_reason=payload.reason,
        client_ip=client_ip(request),
        details={"plate": row.plate},
    )

    return WatchlistEntryOut(
        entry_id=row.entry_id,
        plate=row.plate,
        category=row.category,
        reason=row.reason,
        case_reference=row.case_reference,
        added_by=row.added_by,
        owning_department=row.owning_department,
        active=row.active,
        expires_at=row.expires_at,
        deactivated_at=row.deactivated_at,
        deactivated_by=row.deactivated_by,
        is_demo_data=row.is_demo_data,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts", response_model=list[AlertOut], summary="List watchlist alerts")
async def list_alerts(
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.ALERT_READ)),
    db: AsyncSession = Depends(get_db),
    unacknowledged_only: bool = Query(default=False),
    exact_only: bool = Query(
        default=False,
        description="Hide near matches. Off by default - a near match is a thing to look at.",
    ),
    category: str | None = Query(default=None),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[AlertOut]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    statement = (
        select(WatchlistAlert)
        .where(WatchlistAlert.timestamp_utc >= cutoff)
        .order_by(WatchlistAlert.timestamp_utc.desc())
        .limit(limit)
    )
    if unacknowledged_only:
        statement = statement.where(WatchlistAlert.acknowledged.is_(False))
    if exact_only:
        statement = statement.where(WatchlistAlert.exact.is_(True))
    if category:
        statement = statement.where(WatchlistAlert.category == category.strip().lower())

    rows = (await db.execute(statement)).scalars().all()
    cameras = await _readable_cameras(db, user)
    rows = [row for row in rows if row.camera_id in cameras]

    may_read_plate = user.can(Permission.PLATE_READ)
    horizon = _plate_horizon(settings)
    disclosed = 0

    projected = []
    for row in rows:
        show = may_read_plate and _within_retention(row.timestamp_utc, horizon)
        if show:
            disclosed += 1
        camera = cameras.get(row.camera_id)
        projected.append(
            AlertOut(
                alert_id=row.alert_id,
                watch_plate=row.watch_plate if show else None,
                seen_plate=row.seen_plate if show else None,
                category=row.category,
                distance=row.distance,
                exact=row.exact,
                sighting_id=row.sighting_id,
                camera_id=row.camera_id,
                camera_name=camera.name if camera else None,
                owning_department=camera.owning_department if camera else None,
                city=camera.city if camera else None,
                district=camera.district if camera else None,
                latitude=camera.latitude if camera else None,
                longitude=camera.longitude if camera else None,
                timestamp_utc=row.timestamp_utc,
                acknowledged=row.acknowledged,
                acknowledged_by=row.acknowledged_by,
                acknowledged_at=row.acknowledged_at,
                dismissed_reason=row.dismissed_reason,
                is_demo_data=row.is_demo_data,
                plate_withheld=not show,
            )
        )

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.ALERTS_VIEWED,
        resource_type=ResourceType.WATCHLIST_ALERT,
        department=user.department,
        client_ip=client_ip(request),
        details={"returned": len(projected), "plates_disclosed": disclosed},
    )
    return projected


@router.post(
    "/alerts/{alert_id}/acknowledge",
    response_model=AlertOut,
    summary="Acknowledge or dismiss an alert",
)
async def acknowledge_alert(
    alert_id: str,
    payload: AlertAcknowledge,
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.ALERT_ACKNOWLEDGE)),
    db: AsyncSession = Depends(get_db),
) -> AlertOut:
    """Close an alert.

    Supplying `dismissed_reason` records that a human looked and it was not the
    watched vehicle — which is worth keeping. A plate that generates a steady
    stream of dismissals is usually one that sits a single confusion-pair away
    from something common, and that is a tuning signal, not noise.
    """
    row = (
        await db.execute(select(WatchlistAlert).where(WatchlistAlert.alert_id == alert_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such alert.")

    cameras = await _readable_cameras(db, user)
    if row.camera_id not in cameras:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.ALERT_ACKNOWLEDGED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.WATCHLIST_ALERT,
            resource_id=alert_id,
            department=user.department,
            client_ip=client_ip(request),
            details={"code": "CAMERA_OUT_OF_SCOPE", "camera_id": row.camera_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CAMERA_OUT_OF_SCOPE",
                "message": "This alert belongs to a camera outside your scope.",
            },
        )

    row.acknowledged = True
    row.acknowledged_by = user.username
    row.acknowledged_at = datetime.now(timezone.utc)
    row.dismissed_reason = payload.dismissed_reason
    await db.commit()
    await db.refresh(row)

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=(
            AuditAction.ALERT_DISMISSED
            if payload.dismissed_reason
            else AuditAction.ALERT_ACKNOWLEDGED
        ),
        resource_type=ResourceType.WATCHLIST_ALERT,
        resource_id=row.alert_id,
        department=user.department,
        case_or_reason=payload.dismissed_reason,
        client_ip=client_ip(request),
        details={"camera_id": row.camera_id, "exact": row.exact, "distance": row.distance},
    )

    camera = cameras.get(row.camera_id)
    # The same retention horizon list_alerts applies. Without it, acknowledging
    # an alert older than anpr_plate_retention_days handed back both plates the
    # list had already withheld.
    may_read_plate = user.can(Permission.PLATE_READ) and _within_retention(
        row.timestamp_utc, _plate_horizon(settings)
    )
    return AlertOut(
        alert_id=row.alert_id,
        watch_plate=row.watch_plate if may_read_plate else None,
        seen_plate=row.seen_plate if may_read_plate else None,
        category=row.category,
        distance=row.distance,
        exact=row.exact,
        sighting_id=row.sighting_id,
        camera_id=row.camera_id,
        camera_name=camera.name if camera else None,
        owning_department=camera.owning_department if camera else None,
        city=camera.city if camera else None,
        district=camera.district if camera else None,
        latitude=camera.latitude if camera else None,
        longitude=camera.longitude if camera else None,
        timestamp_utc=row.timestamp_utc,
        acknowledged=row.acknowledged,
        acknowledged_by=row.acknowledged_by,
        acknowledged_at=row.acknowledged_at,
        dismissed_reason=row.dismissed_reason,
        is_demo_data=row.is_demo_data,
        plate_withheld=not may_read_plate,
    )


# ---------------------------------------------------------------------------
# Sightings, search and movement
# ---------------------------------------------------------------------------

@router.get("/sightings", response_model=list[SightingOut], summary="List plate sightings")
async def list_sightings(
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.DETECTION_READ)),
    db: AsyncSession = Depends(get_db),
    camera_id: str | None = Query(default=None),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[SightingOut]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    statement = (
        select(PlateSighting)
        .where(PlateSighting.timestamp_utc >= cutoff)
        .order_by(PlateSighting.timestamp_utc.desc())
        .limit(limit)
    )
    if camera_id:
        statement = statement.where(PlateSighting.camera_id == camera_id)

    rows = (await db.execute(statement)).scalars().all()
    cameras = await _readable_cameras(db, user)
    rows = [row for row in rows if row.camera_id in cameras]

    may_read_plate = user.can(Permission.PLATE_READ)
    horizon = _plate_horizon(settings)
    disclosed = 0

    projected = []
    for row in rows:
        show = may_read_plate and _within_retention(row.timestamp_utc, horizon)
        if show:
            disclosed += 1
        camera = cameras.get(row.camera_id)
        projected.append(
            SightingOut(
                sighting_id=row.sighting_id,
                detection_id=row.detection_id,
                plate_text=row.plate_text if show else None,
                plate_normalised=row.plate_normalised if show else None,
                state_code=row.state_code if show else None,
                camera_id=row.camera_id,
                camera_name=camera.name if camera else None,
                owning_department=camera.owning_department if camera else None,
                city=camera.city if camera else None,
                district=camera.district if camera else None,
                latitude=camera.latitude if camera else None,
                longitude=camera.longitude if camera else None,
                timestamp_utc=row.timestamp_utc,
                confidence=row.confidence,
                observations=row.observations,
                reader=row.reader,
                frame_quality=row.frame_quality,
                is_demo_data=row.is_demo_data,
                plate_withheld=not show,
            )
        )

    if disclosed:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.PLATE_DATA_VIEWED,
            resource_type=ResourceType.PLATE_SIGHTING,
            department=user.department,
            client_ip=client_ip(request),
            details={"plates_disclosed": disclosed, "camera_id": camera_id},
        )
    return projected


@router.get(
    "/plates/search",
    response_model=list[PlateSearchHit],
    summary="Find plates the network saw, tolerating OCR error",
)
async def search_plates(
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.TRACK_READ)),
    db: AsyncSession = Depends(get_db),
    q: str = Query(min_length=3, description="Full or partial registration number"),
    max_distance: float = Query(
        default=plate_matching.DEFAULT_MAX_DISTANCE, ge=0.0, le=4.0
    ),
    since_hours: int = Query(default=168, ge=1, le=8760),
) -> list[PlateSearchHit]:
    """Rank distinct plates near *q*.

    The answer to "I have an uncertain registration — what did the network
    actually see?" An operator picks one of these and then asks for its route.
    """
    cameras = await _readable_cameras(db, user)
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    hits = await track_service.search_plates(
        db, q, max_distance=max_distance, camera_ids=set(cameras), since=since
    )

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.PLATE_SEARCHED,
        resource_type=ResourceType.PLATE_SIGHTING,
        department=user.department,
        case_or_reason=f"plate search: {q}",
        client_ip=client_ip(request),
        details={"query": q, "max_distance": max_distance, "hits": len(hits)},
    )
    return [PlateSearchHit(**hit) for hit in hits]


@router.get(
    "/plates/{plate}/track",
    response_model=TrackOut,
    summary="Movement history for one registration number",
)
async def plate_track(
    plate: str,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.TRACK_READ)),
    db: AsyncSession = Depends(get_db),
    reason: str = Query(
        min_length=8,
        description="Why this vehicle is being traced. Recorded against your account.",
    ),
    max_distance: float = Query(
        default=plate_matching.DEFAULT_MAX_DISTANCE, ge=0.0, le=4.0
    ),
    since_hours: int = Query(default=168, ge=1, le=8760),
) -> TrackOut:
    """Reconstruct where a vehicle was seen, in time order.

    A reason is mandatory. This is the single most revealing question the
    platform answers, and an unexplained trace is the one that should never
    have been run — so the requirement is at the API, not in a guideline.
    """
    cameras = await _readable_cameras(db, user)
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    track = await track_service.reconstruct(
        db, plate, max_distance=max_distance, since=since, camera_ids=set(cameras)
    )

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VEHICLE_MOVEMENT_VIEWED,
        resource_type=ResourceType.PLATE_SIGHTING,
        resource_id=track.query,
        department=user.department,
        case_or_reason=reason,
        client_ip=client_ip(request),
        details={
            "plate": track.query,
            "points": len(track.points),
            "cameras_seen": track.cameras_seen,
            "max_distance": max_distance,
        },
    )

    return TrackOut(
        query=track.query,
        max_distance=track.max_distance,
        points=[TrackPointOut(**point.__dict__) for point in track.points],
        first_seen=track.first_seen,
        last_seen=track.last_seen,
        cameras_seen=track.cameras_seen,
        total_distance_km=track.total_distance_km,
        exact_reads=track.exact_reads,
        implausible_legs=track.implausible_legs,
    )


# ---------------------------------------------------------------------------
# Output report
# ---------------------------------------------------------------------------

@router.get(
    "/reports/anpr",
    response_model=list[AnalyticsReportRow],
    summary="ANPR output report - detected plates with timestamps",
)
async def anpr_report(
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.PLATE_READ)),
    db: AsyncSession = Depends(get_db),
    camera_id: str | None = Query(default=None),
    since_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=1000, ge=1, le=5000),
    as_csv: bool = Query(default=False, description="Return text/csv instead of JSON"),
):
    """The report the challenge asks to be submitted with the feed demonstration.

    "Detected vehicles or number plates with corresponding timestamps" — plus
    the camera, its location and whether the read hit the watchlist, because a
    report of plates with no places is not evidence of an integration.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    statement = (
        select(PlateSighting)
        .where(PlateSighting.timestamp_utc >= cutoff)
        .order_by(PlateSighting.timestamp_utc.asc())
        .limit(limit)
    )
    if camera_id:
        statement = statement.where(PlateSighting.camera_id == camera_id)

    rows = (await db.execute(statement)).scalars().all()
    cameras = await _readable_cameras(db, user)
    rows = [row for row in rows if row.camera_id in cameras]

    alert_by_sighting = {
        alert.sighting_id: alert
        for alert in (
            await db.execute(
                select(WatchlistAlert).where(
                    WatchlistAlert.sighting_id.in_({row.sighting_id for row in rows} or {""})
                )
            )
        ).scalars().all()
    }

    horizon = _plate_horizon(settings)
    report = []
    for row in rows:
        camera = cameras.get(row.camera_id)
        alert = alert_by_sighting.get(row.sighting_id)
        report.append(
            AnalyticsReportRow(
                plate=(
                    row.plate_normalised
                    if _within_retention(row.timestamp_utc, horizon)
                    else None
                ),
                camera_id=row.camera_id,
                camera_name=camera.name if camera else None,
                location=_location_of(camera),
                district=camera.district if camera else None,
                latitude=camera.latitude if camera else None,
                longitude=camera.longitude if camera else None,
                timestamp_utc=row.timestamp_utc,
                confidence=row.confidence,
                observations=row.observations,
                watchlist_hit=alert is not None,
                watchlist_category=alert.category if alert else None,
            )
        )

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.PLATE_DATA_VIEWED,
        resource_type=ResourceType.PLATE_SIGHTING,
        department=user.department,
        case_or_reason="ANPR output report",
        client_ip=client_ip(request),
        details={"rows": len(report), "format": "csv" if as_csv else "json"},
    )

    if not as_csv:
        return report

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "plate", "camera_id", "camera_name", "location", "district",
            "latitude", "longitude", "timestamp_utc", "confidence",
            "observations", "watchlist_hit", "watchlist_category",
        ]
    )
    for line in report:
        writer.writerow(
            [
                line.plate or "",
                line.camera_id,
                line.camera_name or "",
                line.location or "",
                line.district or "",
                line.latitude if line.latitude is not None else "",
                line.longitude if line.longitude is not None else "",
                line.timestamp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                f"{line.confidence:.3f}",
                line.observations,
                "yes" if line.watchlist_hit else "no",
                line.watchlist_category or "",
            ]
        )
    buffer.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="vigentra-anpr-{stamp}.csv"'
        },
    )
