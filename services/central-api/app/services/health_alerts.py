"""Camera and department-system health alerts: a camera that stops is told to someone.

The health monitor knows within one sweep when a camera, or a whole department system, stops
answering. On its own that only turned a number red on the overview page, which helps nobody
who is not looking at it. This raises an alert after `health_alert_after_checks` failed sweeps
in a row (about 40 s at the default 20 s interval, so a blip is not an alarm) and closes it
itself when the camera answers again.

Two rules keep it quiet enough to be believed:

- **One alert per department system, not one per camera.** When a system is unreachable every
  camera behind it reads offline; that is one fault, reported once.
- **A camera withdrawn by decision is not down.** Suspended and decommissioned cameras are
  `unavailable` because someone chose it, and never alert.

The streak counters live in memory: after a restart a camera that is still down needs the same
two sweeps to be noticed again, and the alert already open in the database is not duplicated.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Camera as CameraRow
from ..models import HealthAlert
from ..schemas import CameraStatus, SyncStatus

CAMERA_OFFLINE = "CAMERA_OFFLINE"
SOURCE_UNREACHABLE = "SOURCE_UNREACHABLE"
#: the camera answers but its picture is broken (edge frame quality); raised at once, not per sweep
FOOTAGE_CORRUPTED = "FOOTAGE_CORRUPTED"
FOOTAGE_BLACKOUT = "FOOTAGE_BLACKOUT"
FOOTAGE_KINDS = {"corrupted": FOOTAGE_CORRUPTED, "blackout": FOOTAGE_BLACKOUT}
#: this many bad frames within the window raise the alert (one smeared frame is a blip)
FOOTAGE_FRAMES, FOOTAGE_WINDOW_S = 3, 60.0
#: closed when no bad frame of its kind has come for this long
FOOTAGE_CLEAR_S = 300.0
_bad_frames: dict[tuple[str, str], list[datetime]] = {}

_DOWN = {CameraStatus.OFFLINE.value, CameraStatus.UNAVAILABLE.value}
_streak: dict[str, int] = {}


def reset() -> None:
    """Forget every streak (tests, and a fresh monitor)."""
    _streak.clear()
    _bad_frames.clear()


async def footage(db: AsyncSession, camera: CameraRow, quality: str, now: datetime | None = None) -> HealthAlert | None:
    """A corrupted or black frame from the edge: raise the camera's footage alert as soon as a few
    arrive within a minute (none open of that kind already). Returns the alert it raised."""
    kind = FOOTAGE_KINDS.get(quality)
    if kind is None:
        return None
    now = now or datetime.now(timezone.utc)
    key = (camera.camera_id, kind)
    times = [t for t in _bad_frames.get(key, []) if (now - t).total_seconds() <= FOOTAGE_WINDOW_S] + [now]
    _bad_frames[key] = times[-FOOTAGE_FRAMES:]
    if len(times) < FOOTAGE_FRAMES:
        return None
    open_row = (await db.execute(select(HealthAlert).where(
        HealthAlert.camera_id == camera.camera_id, HealthAlert.kind == kind, HealthAlert.recovered_at.is_(None),
    ))).scalars().first()
    if open_row is not None:
        return None
    detail = ("Picture breaking up into blocks (data lost from the stream)" if kind == FOOTAGE_CORRUPTED
              else "Picture completely black (no signal or lens covered)")
    row = _new(kind, camera.source_system, camera.owning_department, camera.camera_id, detail, now)
    db.add(row)
    return row


async def clear_footage(db: AsyncSession, now: datetime | None = None) -> list[HealthAlert]:
    """Close footage alerts whose camera has sent no bad frame of that kind for FOOTAGE_CLEAR_S."""
    from ..models import FrameQualityEvent
    from sqlalchemy import func

    now = now or datetime.now(timezone.utc)
    rows = (await db.execute(select(HealthAlert).where(
        HealthAlert.kind.in_(list(FOOTAGE_KINDS.values())), HealthAlert.recovered_at.is_(None),
    ))).scalars().all()
    closed = []
    for row in rows:
        quality = next(q for q, k in FOOTAGE_KINDS.items() if k == row.kind)
        last = (await db.execute(select(func.max(FrameQualityEvent.timestamp_utc)).where(
            FrameQualityEvent.camera_id == row.camera_id, FrameQualityEvent.quality == quality,
        ))).scalar()
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is None or (now - last).total_seconds() >= FOOTAGE_CLEAR_S:
            row.recovered_at = now
            closed.append(row)
    return closed


def _new(kind: str, source_system: str, department: str | None, camera_id: str | None,
         detail: str, now: datetime) -> HealthAlert:
    return HealthAlert(alert_id=f"halert_{uuid.uuid4().hex[:20]}", kind=kind, camera_id=camera_id,
                       source_system=source_system, department=department, detail=detail, raised_at=now)


async def evaluate(
    db: AsyncSession,
    *,
    source_system: str,
    source_name: str,
    department: str | None,
    reachable: bool,
    cameras: list[CameraRow],
    after_checks: int,
    now: datetime | None = None,
) -> tuple[list[HealthAlert], list[HealthAlert]]:
    """Raise and close alerts for one department system after its sweep: (raised, recovered)."""
    now = now or datetime.now(timezone.utc)
    after = max(1, after_checks)
    open_rows = {
        (row.kind, row.camera_id): row
        for row in (await db.execute(select(HealthAlert).where(
            HealthAlert.source_system == source_system, HealthAlert.recovered_at.is_(None)))).scalars()
    }
    raised: list[HealthAlert] = []
    recovered: list[HealthAlert] = []

    def close(row: HealthAlert | None) -> None:
        if row is not None:
            row.recovered_at = now
            recovered.append(row)

    key = f"source:{source_system}"
    if not reachable:
        _streak[key] = _streak.get(key, 0) + 1
        if _streak[key] >= after and (SOURCE_UNREACHABLE, None) not in open_rows:
            raised.append(_new(SOURCE_UNREACHABLE, source_system, department, None,
                               f"{source_name} has not answered for {_streak[key]} health checks in a row; "
                               f"its {len(cameras)} cameras read offline until it does", now))
        # Every camera behind it reads offline because the system does: one fault, already reported.
        db.add_all(raised)
        return raised, recovered
    _streak.pop(key, None)
    close(open_rows.get((SOURCE_UNREACHABLE, None)))

    for camera in cameras:
        ckey = f"camera:{camera.camera_id}"
        withdrawn = camera.sync_status == SyncStatus.WITHDRAWN.value
        if not withdrawn and camera.health_status in _DOWN:
            _streak[ckey] = _streak.get(ckey, 0) + 1
            if _streak[ckey] >= after and (CAMERA_OFFLINE, camera.camera_id) not in open_rows:
                raised.append(_new(CAMERA_OFFLINE, source_system, camera.owning_department, camera.camera_id,
                                   f"{camera.name or camera.camera_id} has read {camera.health_status} for "
                                   f"{_streak[ckey]} health checks in a row", now))
        else:
            _streak.pop(ckey, None)
            close(open_rows.get((CAMERA_OFFLINE, camera.camera_id)))
    db.add_all(raised)
    return raised, recovered


def as_payload(row: HealthAlert, state: str) -> dict[str, Any]:
    """One alert as the webhook and the audit trail describe it."""
    return {
        "alert_id": row.alert_id, "kind": row.kind, "state": state, "camera_id": row.camera_id,
        "source_system": row.source_system, "department": row.department, "detail": row.detail,
        "raised_at": row.raised_at.isoformat() if row.raised_at else None,
        "recovered_at": row.recovered_at.isoformat() if row.recovered_at else None,
    }
