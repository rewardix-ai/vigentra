"""Metadata synchronisation across every federated department system.

Two rules this module exists to enforce:

1. **Validation gate.** Only records the owning department has REGISTERED
   (or already SYNCHRONIZED) may enter the central registry. Drafts, unresolved
   submissions and failed validations are counted and skipped. There is no
   approval step: passing the department's own validation is what registers a
   camera, and it appears centrally on the next sync. Suspended and
   decommissioned cameras are kept but marked unavailable, so a withdrawn
   camera cannot quietly vanish from the register.

   Metadata being visible centrally says nothing about footage. Video is
   brokered separately and only against a grant from the owning unit - see
   `services/video_grants.py`.

2. **Failure isolation.** Departments are polled CONCURRENTLY and
   INDEPENDENTLY. One system being unreachable produces an error entry for that
   system and nothing else.

Nothing in this module moves video. It moves rows.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.base import AdapterError, SurveillanceAdapter
from ..config import Settings, SourceSettings
from ..models import Camera as CameraRow
from ..models import (
    CameraHealth,
    InstallationRequest,
    InstallationRequestAttachment,
    MetadataSyncLog,
    Source as SourceRow,
)
from ..schemas import (
    CameraMetadata,
    CameraStatus,
    InstallationRequestOut,
    InstallationStatus,
    PUBLISHABLE_STATUSES,
    RequestStatus,
    SyncResponse,
    SyncSourceResult,
    SyncStatus,
    WITHDRAWN_STATUSES,
)
from . import normalization as norm
from . import policy_service

logger = logging.getLogger("vigentra.sync")

#: Statuses that are still inside the department's pipeline. Their metadata
#: must never reach the central registry.
UNAPPROVED_STATUSES = {
    RequestStatus.DRAFT,
    RequestStatus.SUBMITTED,
    RequestStatus.VALIDATION_FAILED,
}


def _status_value(status: Any) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _in(status: Any, group: set[RequestStatus]) -> bool:
    """Compare a status against a group.

    Schemas are configured with `use_enum_values`, so a status crossing the
    adapter boundary is a plain string. Comparing on values keeps both forms
    working without callers having to care which one they hold.
    """
    return _status_value(status) in {member.value for member in group}


# --------------------------------------------------------------------------
# Collection (network I/O only - no database work in here)
# --------------------------------------------------------------------------

async def _collect(adapter: SurveillanceAdapter) -> dict[str, Any]:
    """Pull one department's register and camera metadata, failures as data."""
    started = time.perf_counter()
    outcome: dict[str, Any] = {
        "source_system": adapter.source_system,
        "requests": [],
        "cameras": [],
        "errors": [],
        "reachable": False,
        "latency_ms": None,
    }

    try:
        outcome["requests"] = await adapter.list_installation_requests()
    except AdapterError as exc:
        outcome["errors"].append(f"installation-register: {exc}")
    except Exception as exc:  # a bug in an adapter must not take the run down
        outcome["errors"].append(f"installation-register: [adapter_exception] {exc}")
        logger.exception("sync: %s register read failed", adapter.source_system)

    try:
        outcome["cameras"] = await adapter.list_approved_cameras()
        outcome["reachable"] = True
    except AdapterError as exc:
        outcome["errors"].append(str(exc))
        outcome["error_code"] = exc.code
        logger.warning("sync: %s camera read failed: %s", adapter.source_system, exc)
    except Exception as exc:
        outcome["errors"].append(f"[adapter_exception] {exc}")
        logger.exception("sync: %s raised an unexpected error", adapter.source_system)

    outcome["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return outcome


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

async def mirror_request(db: AsyncSession, request: InstallationRequestOut) -> InstallationRequest:
    """Mirror one department installation record into the central index.

    The authoritative copy stays with the department. This mirror is what makes
    the pipeline listable and auditable centrally, and what keeps the last known
    state visible when a department system is unreachable.
    """
    row = (
        await db.execute(
            select(InstallationRequest).where(InstallationRequest.request_id == request.request_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = InstallationRequest(request_id=request.request_id)
        db.add(row)

    row.source_system = request.source_system
    row.status = _status_value(request.status)
    row.owning_department = request.owning_department
    row.owning_unit = request.owning_unit
    row.district = request.district
    row.camera_name = request.camera_name
    row.external_camera_id = request.external_camera_id
    row.created_by = request.created_by
    row.submitted_by = request.submitted_by
    row.submitted_at = norm.to_utc(request.submitted_at)
    row.approved_by = request.approved_by
    row.approved_by_role = request.approved_by_role
    row.approved_at = norm.to_utc(request.approved_at)
    row.rejected_by = request.rejected_by
    row.rejected_at = norm.to_utc(request.rejected_at)
    row.rejection_reason = request.rejection_reason
    row.withdrawal_reason = request.withdrawal_reason
    row.synchronized_at = norm.to_utc(request.synchronized_at)
    row.validation_errors = list(request.validation_errors)
    row.installation_form_json = dict(request.form)
    row.mirrored_at = datetime.now(timezone.utc)

    await _mirror_attachments(db, request)
    return row


async def _mirror_attachments(db: AsyncSession, request: InstallationRequestOut) -> None:
    existing = {
        row.reference: row
        for row in (
            await db.execute(
                select(InstallationRequestAttachment).where(
                    InstallationRequestAttachment.request_id == request.request_id
                )
            )
        ).scalars().all()
    }
    for item in request.attachments:
        reference = item.get("reference")
        if not reference:
            continue
        row = existing.get(reference)
        if row is None:
            row = InstallationRequestAttachment(
                request_id=request.request_id, reference=reference
            )
            db.add(row)
        row.document_type = item.get("document_type") or "other"
        row.filename = item.get("filename")
        row.custodian = item.get("custodian") or request.owning_department


async def _upsert_camera(
    db: AsyncSession,
    camera: CameraMetadata,
    adapter: SurveillanceAdapter,
) -> str:
    """Insert or update one canonical camera. Returns created/updated/conflict."""
    row = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == camera.camera_id))
    ).scalar_one_or_none()

    # A canonical ID that already belongs to a different department record is a
    # registry integrity problem, not something to silently overwrite.
    if row is not None and (
        row.source_system != camera.source_system
        or row.external_camera_id != camera.external_camera_id
    ):
        return "conflict"

    now = datetime.now(timezone.utc)
    verdict = "updated"
    if row is None:
        row = CameraRow(camera_id=camera.camera_id, first_synced_at=now)
        db.add(row)
        verdict = "created"

    withdrawn = _in(camera.request_status, WITHDRAWN_STATUSES)

    row.external_camera_id = camera.external_camera_id
    row.source_system = camera.source_system
    row.installation_request_id = camera.installation_request_id
    row.name = camera.name
    row.vendor = camera.vendor
    row.model = camera.model
    row.camera_type = _status_value(camera.camera_type)
    row.installation_purpose = _status_value(camera.installation_purpose) if camera.installation_purpose else None
    row.camera_serial_masked = camera.camera_serial_masked
    row.owning_department = camera.owning_department
    row.department_code = camera.department_code or ""
    row.owning_unit = camera.owning_unit
    row.police_station_or_zone = camera.police_station_or_zone
    row.city = camera.city or ""
    row.city_normalized = norm.normalize_city(camera.city)
    row.zone = camera.zone
    row.capabilities = list(camera.capabilities or [])
    row.maintenance_agency = camera.maintenance_agency
    row.installation_vendor = camera.installation_vendor
    row.district = camera.district
    row.road_or_junction = camera.road_or_junction
    row.landmark = camera.landmark
    row.latitude = camera.latitude
    row.longitude = camera.longitude
    row.view_direction = camera.view_direction
    row.coverage_description = camera.coverage_description
    row.entry_exit_zone_description = camera.entry_exit_zone_description
    row.source_type = _status_value(camera.source_type) if camera.source_type else None
    row.vms_name = camera.vms_name
    row.vms_vendor = camera.vms_vendor
    row.resolution = camera.resolution
    row.fps = camera.fps
    row.codec = camera.codec
    row.retention_days = camera.retention_days
    row.timezone_name = camera.timezone_name
    row.installation_date = camera.installation_date
    row.commissioning_date = camera.commissioning_date
    row.installation_status = _status_value(camera.installation_status)
    row.approval_status = _status_value(camera.request_status)
    row.approved_by_role = camera.approved_by_role
    row.approved_at = norm.to_utc(camera.approved_at)
    row.provenance = dict(camera.provenance)
    row.last_metadata_sync_utc = now

    if withdrawn:
        # Kept in the register, but unmistakably out of service - and with
        # brokered video switched off, whatever the owner's flag says.
        row.health_status = CameraStatus.UNAVAILABLE.value
        row.sync_status = SyncStatus.WITHDRAWN.value
        row.last_frame_utc = norm.to_utc(camera.last_frame_utc)
        row.video_access_enabled = False
        row.capabilities = ["metadata", "health"]
    else:
        row.health_status = _status_value(camera.health_status)
        row.sync_status = SyncStatus.SYNCHRONIZED.value
        row.last_heartbeat_utc = now
        row.last_frame_utc = norm.to_utc(camera.last_frame_utc)
        # The owner's decision about brokered video, carried through verbatim.
        row.video_access_enabled = bool(camera.local_video_access)

    db.add(
        CameraHealth(
            camera_id=camera.camera_id,
            source_system=camera.source_system,
            status=row.health_status,
            last_heartbeat_utc=now,
            last_frame_utc=row.last_frame_utc,
            reconnect_count=camera.reconnect_count,
            detail={
                "observed_during": "metadata_sync",
                "installation_status": row.installation_status,
                "adapter": adapter.adapter_name,
            },
        )
    )
    await policy_service.upsert_policy(db, camera)
    return verdict


async def _mark_source_cameras_offline(db: AsyncSession, source_system: str, reason: str) -> int:
    """A department system we cannot reach means its cameras' state is unknown.

    The rows stay in the register with the reason attached; they recover on the
    next successful synchronisation.
    """
    rows = (
        await db.execute(select(CameraRow).where(CameraRow.source_system == source_system))
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        if row.sync_status == SyncStatus.WITHDRAWN.value:
            continue
        row.health_status = CameraStatus.OFFLINE.value
        db.add(
            CameraHealth(
                camera_id=row.camera_id,
                source_system=source_system,
                status=CameraStatus.OFFLINE.value,
                last_heartbeat_utc=now,
                last_frame_utc=row.last_frame_utc,
                detail={"reason": reason, "source_reachable": False},
            )
        )
    return len(rows)


async def _upsert_source(
    db: AsyncSession,
    config: SourceSettings,
    adapter: SurveillanceAdapter,
    *,
    status: str,
    camera_count: int,
    pending_requests: int,
    latency_ms: float | None,
    error: str | None,
) -> SourceRow:
    row = (
        await db.execute(select(SourceRow).where(SourceRow.source_system_id == config.source_system))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = SourceRow(source_system_id=config.source_system)
        db.add(row)

    row.display_name = config.display_name
    row.adapter = adapter.adapter_name
    row.adapter_version = adapter.adapter_version
    row.department = config.department
    row.base_url = config.base_url
    row.status = status
    row.camera_count = camera_count
    row.pending_requests = pending_requests
    row.latency_ms = latency_ms
    row.last_sync_at = now
    row.last_error = error
    if status == "online":
        row.last_success_at = now
    return row


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

async def _retire_unconfigured_sources(
    db: AsyncSession, configured: set[str]
) -> dict[str, int]:
    """Remove registry rows belonging to sources that are no longer federated.

    Turning a department off in configuration is a statement that Vigentra no
    longer federates it. Leaving its cameras in the registry would contradict
    that, and for the two demo departments it would leave synthetic cameras on
    the map with nothing to mark them as such.

    Only rows keyed by `source_system` are touched, so a source that is still
    configured is never affected. Returns what it removed, per table, so the
    caller can log and audit it rather than deleting silently.
    """
    from ..models import Event as EventRow

    removed: dict[str, int] = {}
    for label, model in (
        ("cameras", CameraRow),
        ("installation_requests", InstallationRequest),
        ("events", EventRow),
    ):
        rows = (
            await db.execute(
                select(model).where(model.source_system.notin_(configured or {""}))
            )
        ).scalars().all()
        for row in rows:
            await db.delete(row)
        if rows:
            removed[label] = len(rows)

    # ...and the source's own registry entry, or the department table keeps
    # listing a system nobody federates any more. That row is what the console
    # renders as a department: it carried a name, a camera count, a latency and
    # a health banner long after the source was removed from configuration,
    # which is a department that does not exist reporting that it is fine.
    stale_sources = (
        await db.execute(
            select(SourceRow).where(SourceRow.source_system_id.notin_(configured or {""}))
        )
    ).scalars().all()
    for row in stale_sources:
        await db.delete(row)
    if stale_sources:
        removed["sources"] = len(stale_sources)

    if removed:
        await db.commit()
        logger.info(
            "retired rows from de-configured sources: %s",
            ", ".join(f"{k}={v}" for k, v in removed.items()),
        )
    return removed


async def sync_all(
    db: AsyncSession,
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    *,
    triggered_by: str = "system",
    acknowledge_to_source: bool = True,
) -> SyncResponse:
    """Synchronise approved camera METADATA from every department system.

    Never raises because one department is down.
    """
    sync_id = f"sync_{uuid.uuid4().hex[:16]}"
    source_configs = {source.source_system: source for source in settings.sources}
    ordered = [(name, adapters[name]) for name in source_configs if name in adapters]

    # A source that has been switched off must take its cameras with it.
    # Without this, disabling a department leaves its rows behind and the
    # registry keeps showing cameras from a system it no longer federates -
    # which for the demo departments means synthetic cameras presented as if
    # they were real. Deliberately a hard delete: these rows can always be
    # recreated by re-enabling the source and syncing again.
    retired = await _retire_unconfigured_sources(db, set(source_configs))

    started_at = datetime.now(timezone.utc)
    collected = await asyncio.gather(*(_collect(adapter) for _, adapter in ordered))

    results: dict[str, SyncSourceResult] = {}

    for (source_system, adapter), outcome in zip(ordered, collected):
        config = source_configs[source_system]
        result = SyncSourceResult(
            errors=list(outcome["errors"]), latency_ms=outcome["latency_ms"]
        )

        # Mirror the whole pipeline, whatever its state, and count what is not
        # yet eligible to be published centrally.
        requests: list[InstallationRequestOut] = outcome["requests"]
        pending_requests = 0
        for request in requests:
            await mirror_request(db, request)
            if _in(request.status, UNAPPROVED_STATUSES):
                result.skipped_unregistered += 1
                if _in(request.status, {RequestStatus.DRAFT}):
                    pending_requests += 1

        if not outcome["reachable"]:
            affected = await _mark_source_cameras_offline(
                db, source_system,
                reason=outcome["errors"][0] if outcome["errors"] else "unreachable",
            )
            result.status = "offline"
            await _upsert_source(
                db, config, adapter,
                status="offline",
                camera_count=affected,
                pending_requests=pending_requests,
                latency_ms=outcome["latency_ms"],
                error=result.errors[0] if result.errors else "unreachable",
            )
            results[source_system] = result
            await _write_sync_log(db, sync_id, source_system, triggered_by, result, started_at)
            continue

        acknowledge: list[str] = []
        seen_ids: set[str] = set()
        for camera in outcome["cameras"]:
            status = camera.request_status
            is_publishable = _in(status, PUBLISHABLE_STATUSES)
            is_withdrawn = _in(status, WITHDRAWN_STATUSES)

            if not (is_publishable or is_withdrawn):
                # Belt and braces: a department system should never publish an
                # unapproved record, but if one does, it stops here.
                result.skipped_unregistered += 1
                continue

            verdict = await _upsert_camera(db, camera, adapter)
            if verdict != "conflict":
                seen_ids.add(camera.camera_id)
            if verdict == "conflict":
                result.errors.append(
                    f"canonical ID {camera.camera_id} already belongs to another record "
                    f"- {camera.external_camera_id} skipped"
                )
                continue

            if is_withdrawn:
                result.withdrawn += 1
            else:
                result.approved_records_seen += 1
                result.synchronized += 1
                if verdict == "created":
                    result.created += 1
                else:
                    result.updated += 1
                if camera.installation_request_id and _in(status, {RequestStatus.REGISTERED}):
                    acknowledge.append(camera.installation_request_id)

        # Drop cameras this source no longer publishes.
        #
        # A register that only ever adds is not a register of what exists. A
        # camera removed upstream - decommissioned, renumbered, moved to
        # another system - otherwise stays on the map and in the counts for
        # ever, indistinguishable from one that is simply offline today.
        #
        # Guarded on having read at least one camera, so an upstream that
        # answers with an empty list, or a read that half-failed, cannot empty
        # the registry. That is the failure mode this ordering exists to
        # prevent: absence of evidence is not evidence of decommissioning.
        if seen_ids and not result.errors:
            stale = (
                await db.execute(
                    select(CameraRow).where(
                        CameraRow.source_system == source_system,
                        CameraRow.camera_id.notin_(seen_ids),
                    )
                )
            ).scalars().all()
            for row in stale:
                await db.delete(row)
            if stale:
                result.retired = len(stale)
                logger.info(
                    "sync: %s no longer publishes %d camera(s); removed from the "
                    "registry: %s",
                    source_system,
                    len(stale),
                    ", ".join(sorted(row.camera_id for row in stale)[:5])
                    + (" ..." if len(stale) > 5 else ""),
                )

        # Tell the department system its approved records have been taken.
        if acknowledge_to_source:
            for request_id in acknowledge:
                try:
                    acknowledged = await adapter.mark_synchronized(request_id)
                    await mirror_request(db, acknowledged)
                except AdapterError as exc:
                    result.errors.append(f"acknowledge[{request_id}]: {exc}")

        result.status = "degraded" if result.errors else "online"
        await _upsert_source(
            db, config, adapter,
            status="online" if not result.errors else "degraded",
            camera_count=result.synchronized,
            pending_requests=pending_requests,
            latency_ms=outcome["latency_ms"],
            error="; ".join(result.errors) if result.errors else None,
        )
        results[source_system] = result
        await _write_sync_log(db, sync_id, source_system, triggered_by, result, started_at)

    await db.commit()

    total = (await db.execute(select(func.count()).select_from(CameraRow))).scalar_one()
    success = any(item.synchronized > 0 for item in results.values())

    logger.info(
        "metadata sync %s complete: %s%s",
        sync_id,
        {name: {"synchronized": r.synchronized, "skipped": r.skipped_unregistered} for name, r in results.items()},
        f" | retired {retired}" if retired else "",
    )
    return SyncResponse(
        success=success,
        metadata_only=True,
        sources=results,
        total_cameras=int(total),
        synced_at=datetime.now(timezone.utc),
    )


async def _write_sync_log(
    db: AsyncSession,
    sync_id: str,
    source_system: str,
    triggered_by: str,
    result: SyncSourceResult,
    started_at: datetime,
) -> None:
    db.add(
        MetadataSyncLog(
            sync_id=sync_id,
            source_system=source_system,
            triggered_by=triggered_by,
            status=result.status,
            approved_records_seen=result.approved_records_seen,
            synchronized=result.synchronized,
            skipped_unregistered=result.skipped_unregistered,
            withdrawn=result.withdrawn,
            errors=list(result.errors),
            latency_ms=result.latency_ms,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
        )
    )
