"""Authorized video sessions.

Four routes:

    POST   /api/v1/video-sessions              open an authorized session
    GET    /api/v1/video-sessions/{id}/status  countdown / state for the player
    DELETE /api/v1/video-sessions/{id}         stop it now
    GET    /api/v1/streams/{id}                the bytes, proxied, range-aware

Every refusal is a 403 with `VIDEO_ACCESS_DENIED` and lands in the audit trail.
The body carries a `state` telling the two kinds of no apart: a flat `denied`,
versus `needs_unit_approval`, which names the owning unit and the endpoint to
ask it. Every successful open, and the first read of every session, is audited.

Nothing here returns a department URL, a media ticket, an RTSP address or a
credential. The only address a client receives is this service's own
`/api/v1/streams/{session_id}`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import (
    AdaptersDep,
    CurrentUser,
    SettingsDep,
    authenticate,
    client_ip,
    get_media_client,
)
from ..models import Camera as CameraRow
from ..models import VideoSession as VideoSessionRow
from ..providers.base import ProviderNotConfigured
from ..schemas import VideoAccessState, VideoSessionCreate, VideoSessionOut
from ..services import audit_service, video_broker, video_permissions
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..video_adapters import VideoAdapterError, VideoNotConfigured

logger = logging.getLogger("vigentra.video.router")

router = APIRouter(tags=["video"])


def _denied(
    reason: str,
    *,
    state: str = "denied",
    camera: CameraRow | None = None,
) -> HTTPException:
    """A refusal that says which kind of refusal it is.

    `needs_unit_approval` is not the same answer as `denied`, and collapsing
    them costs the operator a support ticket. When another unit owns the
    camera the body names that unit and the endpoint to ask it, so the UI can
    offer the request instead of a dead end.
    """
    body: dict[str, object] = {
        "code": "VIDEO_ACCESS_DENIED",
        "message": reason,
        "state": state,
    }
    if state == VideoAccessState.NEEDS_UNIT_APPROVAL.value and camera is not None:
        body["owning_department"] = camera.owning_department
        body["request_access_at"] = "/api/v1/video-access-requests"
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=body)


async def _load_camera(db: AsyncSession, camera_id: str) -> CameraRow:
    row = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == camera_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown camera '{camera_id}'"
        )
    return row


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------

@router.post(
    "/api/v1/video-sessions",
    response_model=VideoSessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a short-lived, audited video session",
    responses={
        403: {"description": "VIDEO_ACCESS_DENIED"},
        503: {"description": "SOURCE_ACCESS_NOT_CONFIGURED"},
    },
)
async def create_video_session(
    payload: VideoSessionCreate,
    request: Request,
    settings: SettingsDep,
    adapters: AdaptersDep,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> VideoSessionOut:
    """Authorise, then broker a feed from the owning department.

    A camera appearing in the registry is not sufficient: role, department,
    city, zone, camera state and the owner's own policy are all checked before
    anything is requested from the department system.
    """
    camera = await _load_camera(db, payload.camera_id)
    mode = payload.mode if isinstance(payload.mode, str) else payload.mode.value

    # Step-up authentication. Holding a valid token proves who signed in; it
    # does not prove who is at the keyboard now. Opening a camera is the point
    # where that difference matters, so the operator re-enters their password
    # here and the refusal is audited like any other.
    if authenticate(user.username, payload.password, settings) is None:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.VIDEO_ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.VIDEO,
            resource_id=camera.camera_id,
            department=camera.owning_department,
            source_system=camera.source_system,
            case_or_reason=payload.reason,
            client_ip=client_ip(request),
            details={"code": "STEP_UP_FAILED", "denial_reason": "password_reauth_failed"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "STEP_UP_FAILED",
                "message": "That password is not correct. Re-enter it to open the camera.",
            },
        )

    async def _audit_denial(reason: str, code: str = "VIDEO_ACCESS_DENIED") -> None:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.VIDEO_ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.VIDEO,
            resource_id=camera.camera_id,
            department=camera.owning_department,
            source_system=camera.source_system,
            case_or_reason=payload.reason,
            client_ip=client_ip(request),
            details={
                "code": code,
                "mode": mode,
                "denial_reason": reason,
                "account_department": user.department,
                "account_city": user.city,
                "case_id": payload.case_id,
            },
        )

    try:
        session_row, response = await video_broker.create_session(
            db,
            user=user,
            camera=camera,
            request=payload,
            settings=settings,
            client_ip=client_ip(request),
            adapters=adapters,
        )
    except video_broker.VideoAccessDenied as exc:
        await _audit_denial(exc.reason)
        raise _denied(exc.reason, state=exc.state, camera=camera) from exc
    except video_broker.InvalidPlaybackWindow as exc:
        await _audit_denial(str(exc), code="INVALID_PLAYBACK_WINDOW")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PLAYBACK_WINDOW", "message": str(exc)},
        ) from exc
    except (VideoNotConfigured, ProviderNotConfigured) as exc:
        await _audit_denial(exc.message, code="SOURCE_ACCESS_NOT_CONFIGURED")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SOURCE_ACCESS_NOT_CONFIGURED", "message": exc.message},
        ) from exc
    except VideoAdapterError as exc:
        await _audit_denial(exc.message, code=exc.code)
        raise HTTPException(status_code=exc.http_status, detail=exc.to_dict()) from exc

    entry = await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VIDEO_SESSION_OPENED,
        resource_type=ResourceType.VIDEO,
        resource_id=camera.camera_id,
        department=camera.owning_department,
        source_system=camera.source_system,
        case_or_reason=payload.reason,
        client_ip=client_ip(request),
        details={
            "session_id": session_row.session_id,
            "mode": mode,
            "case_id": payload.case_id,
            "expires_at_utc": response.expires_at_utc.isoformat(),
            "watermark": session_row.watermark_text,
            "start_time_utc": (
                session_row.start_time_utc.isoformat() if session_row.start_time_utc else None
            ),
            "end_time_utc": (
                session_row.end_time_utc.isoformat() if session_row.end_time_utc else None
            ),
        },
    )
    session_row.audit_id = entry.audit_id
    await db.commit()
    response.audit_id = entry.audit_id
    return response


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get(
    "/api/v1/video-sessions/{session_id}/status",
    response_model=VideoSessionOut,
    summary="Session state and remaining time",
)
async def video_session_status(
    session_id: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> VideoSessionOut:
    """Drives the player's countdown. Reports expiry without raising."""
    row = (
        await db.execute(
            select(VideoSessionRow).where(VideoSessionRow.session_id == session_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown video session")
    if row.user_id != user.username:
        raise _denied("This video session belongs to another operator.")

    from datetime import datetime, timezone

    from ..services.normalization import to_utc

    expires = to_utc(row.expires_at_utc)
    if row.status == "active" and (expires is None or expires <= datetime.now(timezone.utc)):
        row.status = "expired"
        await db.commit()

    camera = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == row.camera_id))
    ).scalar_one_or_none()
    return video_broker.to_out(row, camera_name=camera.name if camera else None)


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------

@router.delete(
    "/api/v1/video-sessions/{session_id}",
    response_model=VideoSessionOut,
    summary="Stop a video session immediately",
)
async def revoke_video_session(
    session_id: str,
    request: Request,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> VideoSessionOut:
    """Revocation is immediate and final; a revoked id never streams again."""
    row = (
        await db.execute(
            select(VideoSessionRow).where(VideoSessionRow.session_id == session_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown video session")
    if row.user_id != user.username:
        raise _denied("This video session belongs to another operator.")

    row = await video_broker.revoke_session(db, row)

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VIDEO_SESSION_REVOKED,
        resource_type=ResourceType.VIDEO,
        resource_id=row.camera_id,
        department=row.department,
        source_system=row.source_system,
        case_or_reason=row.reason,
        client_ip=client_ip(request),
        details={
            "session_id": row.session_id,
            "mode": row.mode,
            "access_count": row.access_count,
            "revoked_at_utc": row.revoked_at_utc.isoformat() if row.revoked_at_utc else None,
        },
    )
    camera = (
        await db.execute(select(CameraRow).where(CameraRow.camera_id == row.camera_id))
    ).scalar_one_or_none()
    return video_broker.to_out(row, camera_name=camera.name if camera else None)


# ---------------------------------------------------------------------------
# Stream
# ---------------------------------------------------------------------------

@router.get("/api/v1/streams/{session_id}", summary="Read brokered video for a session")
async def read_stream(
    session_id: str,
    request: Request,
    settings: SettingsDep,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Proxy the department's media, enforcing the session window on every read.

    Re-checks camera authorisation on each request rather than trusting the
    session alone: if an operator's scope is revoked mid-session, the next
    range request stops.
    """
    try:
        session = await video_broker.resolve_session(db, session_id, user=user)
    except video_broker.SessionNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown video session")
    except video_broker.SessionForbidden:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.VIDEO_ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.VIDEO,
            resource_id=session_id,
            client_ip=client_ip(request),
            details={"reason": "session_belongs_to_another_operator"},
        )
        raise _denied("This video session belongs to another operator.")
    except video_broker.SessionExpired:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.VIDEO_ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.VIDEO,
            resource_id=session_id,
            client_ip=client_ip(request),
            details={"reason": "session_expired_or_revoked"},
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "VIDEO_SESSION_ENDED",
                "message": "This video session has expired or was stopped. Open the camera again.",
            },
        )

    # Re-authorise against current state, not the state at session creation.
    camera = await _load_camera(db, session.camera_id)
    # Re-read the grant too: revoking access mid-session must stop the stream
    # at the next range request, not at session expiry.
    from ..services import video_grants

    live_grant = await video_grants.active_grant_for(
        db, username=user.username, camera_id=camera.camera_id
    )
    decision = video_permissions.evaluate(
        user, camera, settings, grant=list(live_grant.allowed_modes) if live_grant else None
    )
    if not decision.permits(session.mode):
        await video_broker.revoke_session(db, session)
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.VIDEO_ACCESS_DENIED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.VIDEO,
            resource_id=camera.camera_id,
            department=camera.owning_department,
            client_ip=client_ip(request),
            details={"reason": "authorisation_changed_mid_session", "detail": decision.reason},
        )
        raise _denied(decision.reason, state=decision.state.value, camera=camera)

    session.access_count += 1
    await db.commit()

    # Range requests arrive constantly while scrubbing; audit the opening read
    # only, so the trail records the viewing without drowning in byte ranges.
    if session.access_count == 1:
        await audit_service.record(
            db,
            username=user.username,
            role=user.role,
            action=AuditAction.VIDEO_STREAM_ACCESSED,
            resource_type=ResourceType.VIDEO,
            resource_id=camera.camera_id,
            department=camera.owning_department,
            source_system=camera.source_system,
            case_or_reason=session.reason,
            client_ip=client_ip(request),
            details={
                "session_id": session.session_id,
                "mode": session.mode,
                "case_id": session.case_id,
            },
        )

    media_client = get_media_client(request)
    try:
        return await video_broker.open_stream(
            media_client,
            session,
            request.headers.get("range"),
            media_root=getattr(request.app.state, "media_root", None),
            # HLS playlists are rewritten to point back here with ?p=<ref>, so
            # every segment request re-enters this route and is re-authorised.
            sub_path=request.query_params.get("p"),
        )
    except HTTPException:
        raise
    except video_broker.HTTPExceptionLike as exc:
        # A malformed or escaping sub-path is the caller's fault, not upstream's.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STREAM_PATH", "message": str(exc)},
        ) from exc
    except Exception as exc:
        # Never echo the upstream URL into the error.
        logger.warning("stream proxy failed for %s: %s", session.session_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "VIDEO_SOURCE_UNAVAILABLE",
                "message": "The owning department's system did not deliver media.",
            },
        ) from exc


# ---------------------------------------------------------------------------
# Live wall snapshots
# ---------------------------------------------------------------------------
#
# The grid's HLS CDN delivers a 6-second segment in 15-80 seconds and 403s the
# moment two requests overlap, so a browser HLS wall of thirty tiles blacks out
# completely - the network simply cannot feed a player. The edge worker decodes
# the same cameras over the RTSP gateway, which is real-time and handles every
# codec (HEVC included, which hls.js cannot play in MPEG-TS at all), and serves
# each camera's latest frame as a small JPEG. This route proxies those frames
# behind the very same authorisation the live session uses - role, department,
# city, zone, camera state and the owner's policy - minus the per-frame
# password step-up, because a wall refreshes every couple of seconds. The
# upstream URL and the grid credentials never reach the browser.
@router.get(
    "/api/v1/cameras/{camera_id}/snapshot",
    summary="Latest still frame for the live wall (proxied from the edge worker)",
    responses={403: {"description": "VIDEO_ACCESS_DENIED"}, 503: {"description": "SNAPSHOT_UNAVAILABLE"}},
)
async def camera_snapshot(
    camera_id: str,
    settings: SettingsDep,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> Response:
    from ..schemas import VideoMode
    from ..services import video_grants

    camera = await _load_camera(db, camera_id)
    grant = await video_grants.active_grant_for(db, username=user.username, camera_id=camera.camera_id)
    decision = video_permissions.evaluate(
        user, camera, settings, grant=list(grant.allowed_modes) if grant else None
    )
    if not decision.permits(VideoMode.LIVE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "VIDEO_ACCESS_DENIED", "message": decision.reason},
        )

    external = camera.external_camera_id or ""
    if not settings.edge_snapshot_url or not external.startswith("GRID-"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SNAPSHOT_UNAVAILABLE", "message": "No live-frame source for this camera."},
        )
    grid_id = external[len("GRID-"):]
    import httpx

    url = settings.edge_snapshot_url.rstrip("/") + f"/snap/{grid_id}.jpg"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            upstream = await client.get(url)
    except Exception as exc:  # noqa: BLE001 - never echo the upstream URL
        logger.warning("snapshot proxy failed for %s: %s", camera.camera_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SNAPSHOT_UNAVAILABLE", "message": "The live-frame source did not answer."},
        ) from exc
    if upstream.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SNAPSHOT_UNAVAILABLE", "message": "This camera has no frame yet."},
        )
    return Response(content=upstream.content, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})
