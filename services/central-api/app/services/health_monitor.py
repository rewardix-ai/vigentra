"""Camera and department-system health monitoring.

A background task polls every registered department on a fixed interval. Each
is probed independently, so an outage in one never delays or fails the other.

What an operator sees per camera: status, last heartbeat, last frame timestamp,
owning department, round-trip latency, the vendor's reconnect counter, and when
the metadata was last synchronised.

Cameras whose installation record has been suspended or decommissioned are left
alone: they are `unavailable` by decision, not by outage, and a health probe
must not quietly bring them back.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..adapters.base import AdapterError, SurveillanceAdapter
from ..config import Settings
from ..models import Camera as CameraRow
from ..models import CameraHealth, Source as SourceRow
from ..schemas import CameraStatus, SyncStatus
from . import normalization as norm

logger = logging.getLogger("sentinel.health")


async def latest_health(db: AsyncSession, camera_id: str) -> CameraHealth | None:
    return (
        await db.execute(
            select(CameraHealth)
            .where(CameraHealth.camera_id == camera_id)
            .order_by(CameraHealth.checked_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _is_withdrawn(camera: CameraRow) -> bool:
    return camera.sync_status == SyncStatus.WITHDRAWN.value


async def check_camera(
    db: AsyncSession,
    adapter: SurveillanceAdapter,
    camera: CameraRow,
) -> CameraHealth:
    """Probe one camera through its department adapter and persist a sample."""
    if _is_withdrawn(camera):
        sample = CameraHealth(
            camera_id=camera.camera_id,
            source_system=camera.source_system,
            status=CameraStatus.UNAVAILABLE.value,
            last_frame_utc=camera.last_frame_utc,
            detail={
                "reason": "installation_record_withdrawn",
                "installation_status": camera.installation_status,
            },
        )
        db.add(sample)
        return sample

    try:
        facts: dict[str, Any] = await adapter.get_camera_health(camera.external_camera_id)
        status = facts["status"]
        status_value = status.value if isinstance(status, CameraStatus) else str(status)
        sample = CameraHealth(
            camera_id=camera.camera_id,
            source_system=camera.source_system,
            status=status_value,
            last_heartbeat_utc=norm.to_utc(facts.get("last_heartbeat_utc")),
            last_frame_utc=norm.to_utc(facts.get("last_frame_utc")),
            latency_ms=facts.get("latency_ms"),
            reconnect_count=facts.get("reconnect_count"),
            detail=facts.get("detail", {}),
        )
        camera.health_status = status_value
        camera.last_heartbeat_utc = sample.last_heartbeat_utc
        if sample.last_frame_utc:
            camera.last_frame_utc = sample.last_frame_utc
    except AdapterError as exc:
        sample = CameraHealth(
            camera_id=camera.camera_id,
            source_system=camera.source_system,
            status=CameraStatus.OFFLINE.value,
            last_frame_utc=camera.last_frame_utc,
            detail={"error": str(exc), "error_code": exc.code, "source_reachable": False},
        )
        camera.health_status = CameraStatus.OFFLINE.value

    db.add(sample)
    return sample


async def poll_source(
    db: AsyncSession,
    adapter: SurveillanceAdapter,
    settings: Settings,
) -> dict[str, Any]:
    """Probe one department system and every camera the registry holds for it."""
    probe = await adapter.check_source_health()
    cameras = (
        await db.execute(select(CameraRow).where(CameraRow.source_system == adapter.source_system))
    ).scalars().all()
    source_row = (
        await db.execute(
            select(SourceRow).where(SourceRow.source_system_id == adapter.source_system)
        )
    ).scalar_one_or_none()

    if not probe["reachable"]:
        now = datetime.now(timezone.utc)
        for camera in cameras:
            if _is_withdrawn(camera):
                continue
            camera.health_status = CameraStatus.OFFLINE.value
            db.add(
                CameraHealth(
                    camera_id=camera.camera_id,
                    source_system=camera.source_system,
                    status=CameraStatus.OFFLINE.value,
                    last_heartbeat_utc=now,
                    last_frame_utc=camera.last_frame_utc,
                    detail={
                        "reason": "department_system_unreachable",
                        "error": probe.get("error"),
                        "source_reachable": False,
                    },
                )
            )
        if source_row is not None:
            source_row.status = "offline"
            source_row.last_error = probe.get("error")
            source_row.latency_ms = probe.get("latency_ms")
        return {"source": adapter.source_system, "reachable": False, "cameras": len(cameras)}

    statuses = [(await check_camera(db, adapter, camera)).status for camera in cameras]

    if source_row is not None:
        source_row.status = "online"
        source_row.last_error = None
        source_row.latency_ms = probe.get("latency_ms")
        source_row.last_success_at = datetime.now(timezone.utc)

    return {
        "source": adapter.source_system,
        "reachable": True,
        "cameras": len(cameras),
        "online": statuses.count(CameraStatus.ONLINE.value),
        "latency_ms": probe.get("latency_ms"),
    }


async def poll_once(
    session_factory: async_sessionmaker[AsyncSession],
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
) -> list[dict[str, Any]]:
    """One monitoring sweep across every department system. Never raises."""
    async with session_factory() as db:
        results = []
        for adapter in adapters.values():
            try:
                results.append(await poll_source(db, adapter, settings))
            except Exception as exc:  # one bad department must not stop the sweep
                logger.exception("health sweep failed for %s", adapter.source_system)
                results.append({"source": adapter.source_system, "error": str(exc)})
        await _prune(db, settings)
        await db.commit()
        return results


async def _prune(db: AsyncSession, settings: Settings) -> None:
    """Health samples are a rolling window, not an archive."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.health_retention_hours)
    await db.execute(delete(CameraHealth).where(CameraHealth.checked_at < cutoff))


async def monitor_loop(
    session_factory: async_sessionmaker[AsyncSession],
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
) -> None:
    """Long-running background sweep. Cancelled on application shutdown."""
    interval = max(5, settings.health_poll_interval_seconds)
    logger.info("health monitor started (every %ss)", interval)
    try:
        while True:
            await asyncio.sleep(interval)
            with contextlib.suppress(Exception):
                await poll_once(session_factory, adapters, settings)
    except asyncio.CancelledError:
        logger.info("health monitor stopped")
        raise
