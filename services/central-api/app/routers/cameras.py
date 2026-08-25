"""The unified camera METADATA registry - one shape, every department.

The registry is federation-wide: any account with `registry:read` sees every
camera in it, whichever unit installed it. That is the point of federating in
the first place - an officer needs to know a camera covers the junction before
there is anything to ask about. Reading another department's records is capped
at STANDARD depth (see `policy_service.effective_visibility`), so a unit's
contacts, vendors and paperwork stay its own.

No route in this module returns a stream URL, a media token or anything else
that could be used to reach footage. A camera record tells you the camera
exists; `routers/video_sessions.py` decides whether you may watch it, and
`routers/video_grants.py` is how you ask the owning unit when you may not.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.base import AdapterError
from ..config import DemoUser, Permission
from ..database import get_db
from ..config import normalize_city
from ..dependencies import (
    AdaptersDep,
    SettingsDep,
    client_ip,
    require_permission,
)
from ..mappers import access_policy_to_schema, camera_to_detail, camera_to_schema, health_to_schema
from ..models import Camera as CameraRow, InstallationRequestAttachment
from ..schemas import AccessPolicyOut, Camera, CameraDetail, CameraHealthOut, PagedCameras
from ..services import audit_service, health_monitor, policy_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType

router = APIRouter(prefix="/api/v1/cameras", tags=["camera registry"])


async def _load_camera(db: AsyncSession, camera_id: str) -> CameraRow:
    row = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == camera_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown camera '{camera_id}'"
        )
    return row


async def _authorise(
    db: AsyncSession,
    request: Request,
    user: DemoUser,
    camera: CameraRow,
    action: AuditAction,
) -> None:
    """Sentinel registry permission - which records this account may READ.

    Retained as the single choke point even though the registry is now
    federation-wide, so that reintroducing a restriction is a one-line change
    in `policy_service` rather than an audit of every route.
    """
    if policy_service.may_read_camera(user, camera):
        return
    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=action,
        outcome=AuditOutcome.DENIED,
        resource_type=ResourceType.CAMERA,
        resource_id=camera.camera_id,
        department=camera.owning_department,
        source_system=camera.source_system,
        case_or_reason="account is not scoped to the owning department",
        client_ip=client_ip(request),
        details={"account_department": user.department},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Your account is scoped to '{user.department}' and may not read records "
            f"owned by '{camera.owning_department}'"
        ),
    )


@router.get("", response_model=PagedCameras, summary="List canonical camera records")
async def list_cameras(
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
    city: str | None = Query(default=None, description="Case-insensitive city name"),
    department: str | None = Query(default=None, description="Department display name"),
    department_code: str | None = Query(default=None),
    zone: str | None = Query(default=None),
    source_system: str | None = Query(default=None),
    owning_department: str | None = Query(default=None),
    district: str | None = Query(default=None),
    health_status: str | None = Query(default=None, alias="status"),
    installation_status: str | None = Query(default=None),
    approval_status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Substring match on name or camera ID"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> list[Camera]:
    """Cameras from every federated department, in the canonical shape.

    Every camera in the federation, for every account holding `registry:read`.
    Each row carries its own `video_access` state, so the list can show a
    Municipal camera to a Traffic operator while marking it
    `needs_unit_approval` - visible, and honestly labelled as not yet viewable.
    """
    stmt = select(CameraRow).order_by(CameraRow.owning_department, CameraRow.camera_id)
    if city:
        # Matches on the indexed normalised column, so "ahmedabad",
        # "Ahmedabad" and " AHMEDABAD " are the same query.
        stmt = stmt.where(CameraRow.city_normalized == normalize_city(city))
    if department or owning_department:
        stmt = stmt.where(CameraRow.owning_department == (department or owning_department))
    if department_code:
        stmt = stmt.where(CameraRow.department_code == department_code.upper())
    if zone:
        stmt = stmt.where(CameraRow.zone == zone)
    if source_system:
        stmt = stmt.where(CameraRow.source_system == source_system)
    if district:
        stmt = stmt.where(CameraRow.district == district)
    if health_status:
        stmt = stmt.where(CameraRow.health_status == health_status)
    if installation_status:
        stmt = stmt.where(CameraRow.installation_status == installation_status)
    if approval_status:
        stmt = stmt.where(CameraRow.approval_status == approval_status)

    rows = (await db.execute(stmt)).scalars().all()
    rows = [row for row in rows if policy_service.may_read_camera(user, row)]

    if q:
        needle = q.strip().lower()
        rows = [
            row for row in rows
            if needle in row.name.lower()
            or needle in row.camera_id.lower()
            or needle in (row.external_camera_id or "").lower()
        ]

    applied_filters = {
        key: value
        for key, value in {
            "city": city,
            "department": department or owning_department,
            "department_code": department_code,
            "zone": zone,
            "district": district,
            "source_system": source_system,
            "status": health_status,
            "installation_status": installation_status,
            "q": q,
        }.items()
        if value
    }

    window = rows[offset : offset + limit]
    # One pass for every grant this user holds, so the list can show an
    # accurate per-camera video state without N queries.
    from ..services import video_grants as grants_service

    my_grants = {
        grant.camera_id: list(grant.allowed_modes or [])
        for grant in await grants_service.visible_grants(db, user=user, status="granted")
        if grant.requested_by == user.username and grants_service.is_active(grant)
    }
    policies = {
        camera.camera_id: await policy_service.get_policy(db, camera.camera_id)
        for camera in window
    }

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.CAMERA_REGISTRY_VIEWED,
        resource_type=ResourceType.CAMERA,
        department=user.department,
        client_ip=client_ip(request),
        details={"returned": len(window), "filters": applied_filters},
    )
    return PagedCameras(
        items=[
            camera_to_schema(
                row,
                policies.get(row.camera_id),
                user=user,
                settings=settings,
                granted_modes=my_grants.get(row.camera_id),
            )
            for row in window
        ],
        filters=applied_filters,
        total=len(rows),
    )


@router.get("/{camera_id}", response_model=CameraDetail, summary="Canonical camera record")
async def get_camera(
    camera_id: str,
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> CameraDetail:
    camera = await _load_camera(db, camera_id)
    await _authorise(db, request, user, camera, AuditAction.CAMERA_DETAILS_VIEWED)

    policy = await policy_service.get_policy(db, camera.camera_id)
    health = await health_monitor.latest_health(db, camera.camera_id)
    attachments = []
    if camera.installation_request_id:
        attachments = (
            await db.execute(
                select(InstallationRequestAttachment).where(
                    InstallationRequestAttachment.request_id == camera.installation_request_id
                )
            )
        ).scalars().all()

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.CAMERA_DETAILS_VIEWED,
        resource_type=ResourceType.CAMERA,
        resource_id=camera.camera_id,
        department=camera.owning_department,
        source_system=camera.source_system,
        client_ip=client_ip(request),
        details={
            "external_camera_id": camera.external_camera_id,
            "visibility_level": policy_service.effective_visibility(user, camera),
        },
    )
    from ..services import video_grants as grants_service

    grant = await grants_service.active_grant_for(
        db, username=user.username, camera_id=camera.camera_id
    )
    return camera_to_detail(
        camera, user, settings=settings,
        granted_modes=list(grant.allowed_modes) if grant else None,
        policy=policy, health=health, attachments=list(attachments),
    )


@router.get("/{camera_id}/health", response_model=CameraHealthOut, summary="Camera health")
async def get_camera_health(
    camera_id: str,
    request: Request,
    adapters: AdaptersDep,
    user: DemoUser = Depends(require_permission(Permission.HEALTH_READ)),
    db: AsyncSession = Depends(get_db),
    live: bool = Query(default=True, description="Probe the department system now"),
) -> CameraHealthOut:
    """Probe the owning department for this camera's health.

    When that system is unreachable the last stored sample is returned instead,
    so the page degrades to stale data rather than to an error.
    """
    camera = await _load_camera(db, camera_id)
    await _authorise(db, request, user, camera, AuditAction.CAMERA_HEALTH_VIEWED)

    adapter = adapters.get(camera.source_system)
    sample = None
    if live and adapter is not None:
        try:
            sample = await health_monitor.check_camera(db, adapter, camera)
            await db.commit()
        except AdapterError:
            await db.rollback()
            sample = None
    if sample is None:
        sample = await health_monitor.latest_health(db, camera.camera_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No health data available for '{camera_id}' yet",
        )

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.CAMERA_HEALTH_VIEWED,
        resource_type=ResourceType.CAMERA,
        resource_id=camera.camera_id,
        department=camera.owning_department,
        source_system=camera.source_system,
        client_ip=client_ip(request),
        details={"status": sample.status},
    )
    return health_to_schema(sample, camera)


@router.get(
    "/{camera_id}/access-policy",
    response_model=AccessPolicyOut,
    summary="Access policy summary for one camera",
)
async def get_access_policy(
    camera_id: str,
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.POLICY_READ)),
    db: AsyncSession = Depends(get_db),
) -> AccessPolicyOut:
    """Who may view the FOOTAGE locally, and what Sentinel does and does not grant.

    This endpoint describes a policy. It does not grant anything, and it links
    to nothing playable.
    """
    camera = await _load_camera(db, camera_id)
    await _authorise(db, request, user, camera, AuditAction.ACCESS_POLICY_VIEWED)
    policy = await policy_service.get_policy(db, camera.camera_id)

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.ACCESS_POLICY_VIEWED,
        resource_type=ResourceType.ACCESS_POLICY,
        resource_id=camera.camera_id,
        department=camera.owning_department,
        source_system=camera.source_system,
        client_ip=client_ip(request),
        details={"policy_version": policy.policy_version if policy else 1},
    )
    from ..services import video_grants as grants_service

    grant = await grants_service.active_grant_for(
        db, username=user.username, camera_id=camera.camera_id
    )
    return access_policy_to_schema(
        camera, policy, user, settings,
        granted_modes=list(grant.allowed_modes) if grant else None,
    )
