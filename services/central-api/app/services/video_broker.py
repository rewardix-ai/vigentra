"""Video session brokering.

The browser never learns how Sentinel reaches the pixels. It receives one
opaque path — `/api/v1/streams/{session_id}` — and everything sensitive stays
server-side:

  * the department's feed handle and its ticket (`source_session_reference`)
  * the department credentials (they live in the video adapters, never here)
  * internal service hostnames, private IPs and filesystem paths

Sessions are short-lived (5 minutes live, 15 playback, and never longer than
the department's own ticket), bound to the operator who created them, bound to
the camera they were issued for, revocable, and audited on creation, on first
read and on every refusal.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ..config import DemoUser, Settings
from ..models import Camera as CameraRow
from ..models import VideoSession as VideoSessionRow
from ..schemas import VideoMode, VideoSessionCreate, VideoSessionOut
from ..video_adapters import VideoAdapterError, build_video_adapter
from . import video_permissions
from .normalization import to_utc

logger = logging.getLogger("sentinel.video")

#: Only these upstream response headers reach the client. Everything else -
#: Set-Cookie, Server, vendor identifiers - is dropped.
PASSTHROUGH_HEADERS = {
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
}


class VideoAccessDenied(Exception):
    """Authorisation refused. Carries the operator-facing reason."""

    def __init__(self, reason: str, *, state: str = "denied") -> None:
        super().__init__(reason)
        self.reason = reason
        self.state = state


class SessionNotFound(Exception):
    """No such session id."""


class SessionExpired(Exception):
    """The session existed but its window has closed, or it was revoked."""


class SessionForbidden(Exception):
    """The session belongs to a different operator."""


class InvalidPlaybackWindow(Exception):
    """The requested playback range is missing, inverted, future or too long."""


def new_session_id() -> str:
    return f"vs_{secrets.token_urlsafe(18)}"


def _validate_playback_window(
    request: VideoSessionCreate, settings: Settings, camera: CameraRow | None = None
) -> tuple[datetime, datetime]:
    """Check the requested window against what the owner actually keeps.

    Playback can be asked for as often as anyone likes - there is deliberately
    no quota. What bounds it is the owning unit's own policy: how long that
    department retains footage, and how wide a single request may be.
    """
    start = to_utc(request.start_time_utc)
    end = to_utc(request.end_time_utc)
    if start is None or end is None:
        raise InvalidPlaybackWindow(
            "Playback requires both start_time_utc and end_time_utc."
        )
    if end <= start:
        raise InvalidPlaybackWindow("end_time_utc must be after start_time_utc.")

    now = datetime.now(timezone.utc)
    # A small grace window allows for clock skew between the operator's
    # browser and this service without permitting genuine future requests.
    if start > now + timedelta(minutes=5):
        raise InvalidPlaybackWindow("Playback cannot be requested for a future time.")

    span_minutes = (end - start).total_seconds() / 60
    if span_minutes > settings.video_playback_max_window_minutes:
        raise InvalidPlaybackWindow(
            f"Playback window is {span_minutes:.0f} minutes; the maximum is "
            f"{settings.video_playback_max_window_minutes}."
        )

    # Retention is the owning department's decision, recorded on the camera.
    # Asking for footage past it is not a permission problem - the recording
    # does not exist any more, and saying so is more useful than a 403.
    retention_days = getattr(camera, "retention_days", None) if camera else None
    if retention_days:
        oldest = now - timedelta(days=int(retention_days))
        if start < oldest:
            raise InvalidPlaybackWindow(
                f"{camera.owning_department} retains this camera's footage for "
                f"{int(retention_days)} days. The earliest window still held is "
                f"{oldest.strftime('%d %b %Y %H:%M')} UTC."
            )
    return start, end


async def create_session(
    db: AsyncSession,
    *,
    user: DemoUser,
    camera: CameraRow,
    request: VideoSessionCreate,
    settings: Settings,
    client_ip: str | None = None,
    adapters: dict | None = None,
) -> tuple[VideoSessionRow, VideoSessionOut]:
    """Authorise, ask the department for a feed, store the sensitive half.

    Raises VideoAccessDenied / InvalidPlaybackWindow / VideoAdapterError; the
    router turns each into the right status code and audits the refusal.
    """
    mode = VideoMode(request.mode if isinstance(request.mode, str) else request.mode.value)

    # Cross-unit access needs a live grant from the owning unit; inside your
    # own unit this is None and unused.
    from . import video_grants

    grant = await video_grants.active_grant_for(
        db, username=user.username, camera_id=camera.camera_id
    )
    decision = video_permissions.evaluate(
        user, camera, settings, grant=list(grant.allowed_modes) if grant else None
    )
    if not decision.permits(mode):
        raise VideoAccessDenied(
            decision.reason
            if not decision.allowed
            else f"Your account may not open {mode.value} for this camera.",
            state=decision.state.value,
        )

    start_time: datetime | None = None
    end_time: datetime | None = None
    if mode is VideoMode.PLAYBACK:
        start_time, end_time = _validate_playback_window(request, settings, camera)

    # Ask the owning department for a handle.
    config = settings.source_for_department(camera.owning_department)
    if config is None:
        raise VideoAdapterError(
            f"No federated source is configured for '{camera.owning_department}'",
            source_system=camera.source_system,
        )

    # Reuse the metadata adapter's pool for this department so video calls go
    # through the same (and in tests, the same injected) transport.
    metadata_adapter = (adapters or {}).get(camera.source_system)
    shared_client = getattr(metadata_adapter, "_client", None)
    adapter = build_video_adapter(config, settings, shared_client)
    try:
        handle = await adapter.create_session(
            camera.external_camera_id,
            mode.value,
            start_time,
            end_time,
            user_context={
                "username": user.username,
                "role": user.role,
                "department": user.department,
                "source_system": camera.source_system,
                "case_id": request.case_id,
            },
        )
    finally:
        await adapter.aclose()

    reference = handle.get("source_session_reference")
    if not reference:
        raise VideoAdapterError(
            "The department system returned no usable feed handle",
            source_system=camera.source_system,
        )

    now = datetime.now(timezone.utc)
    ttl = (
        settings.video_live_session_seconds
        if mode is VideoMode.LIVE
        else settings.video_playback_session_seconds
    )
    expires_at = now + timedelta(seconds=ttl)

    # Never outlive the department's own ticket: a session that cannot fetch
    # bytes is worse than one that has visibly expired.
    upstream_expiry = to_utc(handle.get("expires_at"))
    if upstream_expiry and upstream_expiry < expires_at:
        expires_at = upstream_expiry

    watermark = video_permissions.watermark_for(user, camera)

    row = VideoSessionRow(
        session_id=new_session_id(),
        camera_id=camera.camera_id,
        user_id=user.username,
        role=user.role,
        department=camera.owning_department,
        city=camera.city,
        mode=mode.value,
        start_time_utc=start_time,
        end_time_utc=end_time,
        created_at_utc=now,
        expires_at_utc=expires_at,
        status="active",
        source_system=camera.source_system,
        source_session_reference=reference,
        upstream_protocol=str(handle.get("protocol") or "http-mp4"),
        watermark_text=watermark,
        case_id=request.case_id,
        reason=request.reason,
        client_ip=client_ip,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return row, to_out(
        row,
        camera_name=camera.name,
        now=now,
        segment_start_seconds=_as_float(handle.get("segment_start_seconds")),
        segment_end_seconds=_as_float(handle.get("segment_end_seconds")),
    )


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def to_out(
    row: VideoSessionRow,
    *,
    camera_name: str | None = None,
    now: datetime | None = None,
    segment_start_seconds: float | None = None,
    segment_end_seconds: float | None = None,
) -> VideoSessionOut:
    """Session row -> the safe half. Never includes the source reference."""
    now = now or datetime.now(timezone.utc)
    expires = to_utc(row.expires_at_utc) or now
    return VideoSessionOut(
        session_id=row.session_id,
        camera_id=row.camera_id,
        camera_name=camera_name,
        department=row.department,
        city=row.city,
        mode=VideoMode(row.mode),
        stream_url=f"/api/v1/streams/{row.session_id}",
        status=row.status,
        expires_at_utc=expires,
        expires_in_seconds=max(0, int((expires - now).total_seconds())),
        watermark=row.watermark_text,
        case_id=row.case_id,
        reason=row.reason,
        audit_id=row.audit_id,
        start_time_utc=row.start_time_utc,
        end_time_utc=row.end_time_utc,
        access_count=row.access_count,
        segment_start_seconds=segment_start_seconds,
        segment_end_seconds=segment_end_seconds,
    )


async def resolve_session(
    db: AsyncSession,
    session_id: str,
    *,
    user: DemoUser,
) -> VideoSessionRow:
    """Look up a session and enforce ownership, revocation and expiry.

    Ownership is strict: a session belongs to the operator who created it, and
    no role inherits another operator's session. Sharing a session id must not
    be a way to share footage.
    """
    row = (
        await db.execute(
            select(VideoSessionRow).where(VideoSessionRow.session_id == session_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise SessionNotFound(session_id)
    if row.user_id != user.username:
        raise SessionForbidden(session_id)
    if row.status == "revoked" or row.revoked_at_utc is not None:
        raise SessionExpired(session_id)

    expires = to_utc(row.expires_at_utc)
    if expires is None or expires <= datetime.now(timezone.utc):
        # Persist the transition so /status reports `expired` rather than
        # continuing to claim the session is active.
        if row.status != "expired":
            row.status = "expired"
            await db.commit()
        raise SessionExpired(session_id)
    return row


async def revoke_session(db: AsyncSession, row: VideoSessionRow) -> VideoSessionRow:
    """Stop a session immediately. Idempotent."""
    if row.status != "revoked":
        row.status = "revoked"
        row.revoked_at_utc = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
    return row


async def open_stream(
    client: httpx.AsyncClient,
    session: VideoSessionRow,
    range_header: str | None,
    *,
    media_root: str | None = None,
) -> StreamingResponse:
    """Proxy the department's media through Sentinel, preserving byte ranges.

    Range passthrough is what lets an operator scrub the timeline; without it
    the <video> element can only play from the start.

    The upstream URL is read from the session row and never echoed into the
    response — not in a header, not in an error body.
    """
    reference = session.source_session_reference

    # Mock mode serves a bundled clip straight off disk.
    if reference.startswith("mock://clip/"):
        return _stream_local_clip(reference.split("/")[-1], range_header, media_root)

    headers = {}
    if range_header:
        headers["Range"] = range_header

    upstream_request = client.build_request("GET", reference, headers=headers)
    upstream_response = await client.send(upstream_request, stream=True)

    safe_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() in PASSTHROUGH_HEADERS
    }
    safe_headers.setdefault("Accept-Ranges", "bytes")
    # Brokered footage must not be cached by the browser or any intermediary.
    safe_headers["Cache-Control"] = "no-store, private, max-age=0"
    safe_headers["X-Sentinel-Session"] = session.session_id
    safe_headers["X-Sentinel-Watermark"] = session.watermark_text
    safe_headers["X-Content-Type-Options"] = "nosniff"

    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=safe_headers,
        background=BackgroundTask(upstream_response.aclose),
    )


def _stream_local_clip(
    filename: str, range_header: str | None, media_root: str | None
) -> StreamingResponse:
    """Range-aware read of a bundled demo clip (mock resource mode only)."""
    from pathlib import Path

    from fastapi import HTTPException

    root = Path(media_root or "/app/videos")
    # Basename only: a session reference must never be able to escape the
    # media root, even though it is internally generated.
    path = root / Path(filename).name
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "DEMO_MEDIA_MISSING",
                "message": (
                    "The bundled demo clip is not present in this container. "
                    "Run scripts/generate_demo_videos.py and rebuild."
                ),
            },
        )

    size = path.stat().st_size
    start, end = 0, size - 1
    status_code = 200
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store, private, max-age=0",
        "X-Content-Type-Options": "nosniff",
    }

    if range_header and range_header.startswith("bytes="):
        spec = range_header.split("=", 1)[1].split(",")[0].strip()
        start_raw, _, end_raw = spec.partition("-")
        if start_raw:
            start = int(start_raw)
            end = min(int(end_raw), size - 1) if end_raw else size - 1
        elif end_raw:
            start = max(0, size - int(end_raw))
        if start >= size:
            return StreamingResponse(
                iter(()),
                status_code=416,
                headers={**headers, "Content-Range": f"bytes */{size}"},
            )
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    headers["Content-Length"] = str(end - start + 1)

    def _iter():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _iter(), status_code=status_code, media_type="video/mp4", headers=headers
    )


async def expire_stale_sessions(db: AsyncSession) -> int:
    """Mark past-expiry sessions expired. Called by the health monitor sweep."""
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(VideoSessionRow).where(VideoSessionRow.status == "active")
        )
    ).scalars().all()
    changed = 0
    for row in rows:
        expires = to_utc(row.expires_at_utc)
        if expires is None or expires <= now:
            row.status = "expired"
            changed += 1
    if changed:
        await db.commit()
    return changed
