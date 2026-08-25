"""Aggregate counts behind the operations overview page.

Registry, onboarding-pipeline and health arithmetic. The pipeline counters are
worth reading carefully: there is no approval stage, so nothing in them is
"waiting for a signature". A record is either still with the unit that raised
it, or it is already in the registry.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import require_permission
from ..models import Camera as CameraRow, InstallationRequest, Source as SourceRow
from ..schemas import (
    CameraStatus,
    InstallationStatus,
    OverviewResponse,
    RequestStatus,
    SyncStatus,
)
from ..services import policy_service

router = APIRouter(prefix="/api/v1/overview", tags=["dashboard"])


@router.get("", response_model=OverviewResponse, summary="Operations overview counts")
async def overview(
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    """Two different scopes, on purpose.

    The camera register and the federated systems are counted federation-wide,
    because that is exactly what `/api/v1/cameras` lists - a tile reading 3
    above a registry listing 7 is worse than either number alone.

    The onboarding pipeline stays department-scoped. An installation form is
    internal paperwork - owner contacts, vendor, attachments - and unlike the
    camera record it never became a shared artefact.
    """
    cameras = [
        row for row in (await db.execute(select(CameraRow))).scalars().all()
        if policy_service.may_read_camera(user, row)
    ]
    requests = [
        row for row in (await db.execute(select(InstallationRequest))).scalars().all()
        if user.may_access_department(row.owning_department)
    ]
    sources = (await db.execute(select(SourceRow))).scalars().all()

    health_tally: dict[str, int] = {}
    for row in cameras:
        health_tally[row.health_status] = health_tally.get(row.health_status, 0) + 1

    last_sync = max(
        (row.last_sync_at for row in sources if row.last_sync_at is not None), default=None
    )

    return OverviewResponse(
        total_cameras=len(cameras),
        approved_cameras=len(
            [c for c in cameras if c.installation_status == InstallationStatus.COMMISSIONED.value]
        ),
        suspended_cameras=len(
            [c for c in cameras if c.installation_status == InstallationStatus.SUSPENDED.value]
        ),
        decommissioned_cameras=len(
            [c for c in cameras if c.installation_status == InstallationStatus.DECOMMISSIONED.value]
        ),
        # "Pending" now means: raised, but not yet in the central registry.
        # Nothing here waits on an approver - a form is either still being
        # written, mid-submission, or bounced by its own department's
        # validation.
        pending_installation_requests=len(
            [
                r for r in requests
                if r.status in {
                    RequestStatus.DRAFT.value,
                    RequestStatus.SUBMITTED.value,
                    RequestStatus.VALIDATION_FAILED.value,
                }
            ]
        ),
        draft_installation_requests=len(
            [r for r in requests if r.status == RequestStatus.DRAFT.value]
        ),
        # Registered by its unit but not yet pulled into the central
        # registry - the queue a sync run clears.
        requiring_metadata_review=len(
            [r for r in requests if r.status == RequestStatus.REGISTERED.value]
            + [c for c in cameras if c.sync_status == SyncStatus.PENDING.value]
        ),
        online=health_tally.get(CameraStatus.ONLINE.value, 0),
        offline=health_tally.get(CameraStatus.OFFLINE.value, 0),
        degraded=health_tally.get(CameraStatus.DEGRADED.value, 0),
        unavailable=health_tally.get(CameraStatus.UNAVAILABLE.value, 0),
        unknown=health_tally.get(CameraStatus.UNKNOWN.value, 0),
        departments_connected=len([s for s in sources if s.status == "online"]),
        departments_total=len(sources),
        last_metadata_sync_at=last_sync,
        video_access="NOT_AVAILABLE",
    )
