"""Video access requests — asking the owning unit.

Metadata federates automatically. Footage does not: to watch a camera owned by
another unit you ask that unit, and someone there decides.

    POST   /api/v1/video-access-requests            ask
    GET    /api/v1/video-access-requests            what I asked / what I owe
    POST   /api/v1/video-access-requests/{id}/grant   the owner says yes
    POST   /api/v1/video-access-requests/{id}/deny    the owner says no
    DELETE /api/v1/video-access-requests/{id}          withdraw or revoke

No grant is needed inside your own unit — the unit already owns the camera.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import CurrentUser, SettingsDep, client_ip, require_permission
from ..models import Camera as CameraRow
from ..models import VideoAccessGrant
from ..schemas import (
    VideoAccessDecision,
    VideoAccessRequestCreate,
    VideoAccessRequestOut,
)
from ..services import audit_service, video_grants
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.video_grants import DuplicateRequest, GrantError

router = APIRouter(prefix="/api/v1/video-access-requests", tags=["video access requests"])


def _to_out(grant: VideoAccessGrant, camera: CameraRow | None = None) -> VideoAccessRequestOut:
    return VideoAccessRequestOut(
        grant_id=grant.grant_id,
        camera_id=grant.camera_id,
        camera_name=camera.name if camera else None,
        owning_department=grant.owning_department,
        requested_by=grant.requested_by,
        requester_department=grant.requester_department,
        requester_role=grant.requester_role,
        reason=grant.reason,
        case_id=grant.case_id,
        status=grant.status,
        allowed_modes=list(grant.allowed_modes or []),
        decided_by=grant.decided_by,
        decided_at=grant.decided_at,
        decision_note=grant.decision_note,
        requested_at=grant.requested_at,
        expires_at=grant.expires_at,
        is_active=video_grants.is_active(grant),
    )


async def _load(db: AsyncSession, grant_id: str) -> VideoAccessGrant:
    row = (
        await db.execute(
            select(VideoAccessGrant).where(VideoAccessGrant.grant_id == grant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown request '{grant_id}'"
        )
    return row


async def _camera(db: AsyncSession, camera_id: str) -> CameraRow:
    row = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == camera_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown camera '{camera_id}'"
        )
    return row


@router.post(
    "",
    response_model=VideoAccessRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ask the owning unit for access to one of its cameras",
)
async def request_access(
    payload: VideoAccessRequestCreate,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.VIDEO_REQUEST_ACCESS)),
    db: AsyncSession = Depends(get_db),
) -> VideoAccessRequestOut:
    camera = await _camera(db, payload.camera_id)

    try:
        grant = await video_grants.request_access(
            db,
            user=user,
            camera=camera,
            reason=payload.reason,
            case_id=payload.case_id,
            modes=[m.value if hasattr(m, "value") else str(m) for m in payload.modes],
        )
    except DuplicateRequest as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GrantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VIDEO_ACCESS_REQUESTED,
        resource_type=ResourceType.VIDEO_ACCESS_GRANT,
        resource_id=grant.grant_id,
        department=grant.owning_department,
        source_system=camera.source_system,
        case_or_reason=payload.reason,
        client_ip=client_ip(request),
        details={
            "camera_id": camera.camera_id,
            "requester_department": user.department,
            "modes": grant.allowed_modes,
            "case_id": payload.case_id,
        },
    )
    return _to_out(grant, camera)


@router.get(
    "",
    response_model=list[VideoAccessRequestOut],
    summary="Requests you raised, and requests against your unit",
)
async def list_requests(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    request_status: str | None = None,
) -> list[VideoAccessRequestOut]:
    grants = await video_grants.visible_grants(db, user=user, status=request_status)
    cameras = {
        row.camera_id: row
        for row in (await db.execute(select(CameraRow))).scalars().all()
    }
    return [_to_out(grant, cameras.get(grant.camera_id)) for grant in grants]


@router.post(
    "/{grant_id}/grant",
    response_model=VideoAccessRequestOut,
    summary="Owning unit grants the request",
)
async def grant_request(
    grant_id: str,
    payload: VideoAccessDecision,
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.VIDEO_GRANT_ACCESS)),
    db: AsyncSession = Depends(get_db),
) -> VideoAccessRequestOut:
    """Only the unit that owns the camera may say yes."""
    grant = await _load(db, grant_id)
    try:
        grant = await video_grants.decide(
            db,
            grant=grant,
            decider=user,
            approve=True,
            settings=settings,
            note=payload.note,
            modes=[m.value if hasattr(m, "value") else str(m) for m in (payload.modes or [])] or None,
            days=payload.days,
        )
    except GrantError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VIDEO_ACCESS_GRANTED,
        resource_type=ResourceType.VIDEO_ACCESS_GRANT,
        resource_id=grant.grant_id,
        department=grant.owning_department,
        case_or_reason=payload.note or grant.reason,
        client_ip=client_ip(request),
        details={
            "camera_id": grant.camera_id,
            "granted_to": grant.requested_by,
            "modes": grant.allowed_modes,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        },
    )
    return _to_out(grant)


@router.post(
    "/{grant_id}/deny",
    response_model=VideoAccessRequestOut,
    summary="Owning unit refuses the request",
)
async def deny_request(
    grant_id: str,
    payload: VideoAccessDecision,
    request: Request,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.VIDEO_GRANT_ACCESS)),
    db: AsyncSession = Depends(get_db),
) -> VideoAccessRequestOut:
    grant = await _load(db, grant_id)
    try:
        grant = await video_grants.decide(
            db, grant=grant, decider=user, approve=False, settings=settings, note=payload.note
        )
    except GrantError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VIDEO_ACCESS_REFUSED,
        outcome=AuditOutcome.DENIED,
        resource_type=ResourceType.VIDEO_ACCESS_GRANT,
        resource_id=grant.grant_id,
        department=grant.owning_department,
        case_or_reason=payload.note or grant.reason,
        client_ip=client_ip(request),
        details={"camera_id": grant.camera_id, "requested_by": grant.requested_by},
    )
    return _to_out(grant)


@router.delete(
    "/{grant_id}",
    response_model=VideoAccessRequestOut,
    summary="Withdraw a request, or revoke a live grant",
)
async def revoke_request(
    grant_id: str,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> VideoAccessRequestOut:
    """Either side can end it: the owner revokes, the requester withdraws."""
    grant = await _load(db, grant_id)
    try:
        grant = await video_grants.revoke(db, grant=grant, actor=user)
    except GrantError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VIDEO_ACCESS_REVOKED,
        resource_type=ResourceType.VIDEO_ACCESS_GRANT,
        resource_id=grant.grant_id,
        department=grant.owning_department,
        client_ip=client_ip(request),
        details={"camera_id": grant.camera_id, "requested_by": grant.requested_by},
    )
    return _to_out(grant)
