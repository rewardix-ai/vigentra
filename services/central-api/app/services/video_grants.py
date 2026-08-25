"""Asking the owning unit for video access.

The shape of the model, stated once:

  * **Metadata** federates automatically. A unit fills in the installation
    form, its own validation passes, and the camera record appears centrally.
    Nobody approves that, because nobody was making a decision - the unit had
    already decided to install the camera.

  * **Footage** does not federate. To watch a camera belonging to another unit
    you ask that unit, and someone there decides. This module is that request.

Within your own unit no grant is needed: the unit owns the camera, and the
existing department/city/zone scope already governs who may watch it. Grants
exist for the cross-unit case, which is exactly where a human decision belongs.

Grants are time-boxed on purpose. An open-ended grant is a standing permission
that nobody ever revisits.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Settings
from ..models import Camera as CameraRow
from ..models import VideoAccessGrant
from .normalization import to_utc

logger = logging.getLogger("sentinel.video.grants")

REQUESTED = "requested"
GRANTED = "granted"
DENIED = "denied"
REVOKED = "revoked"
EXPIRED = "expired"


class GrantError(Exception):
    """A grant could not be created or decided."""


class DuplicateRequest(GrantError):
    """An identical request is already open or already granted."""


def new_grant_id() -> str:
    return f"vag_{secrets.token_urlsafe(14)}"


def is_active(grant: VideoAccessGrant, *, now: datetime | None = None) -> bool:
    """A grant counts only while it is granted and unexpired."""
    if grant.status != GRANTED:
        return False
    expires = to_utc(grant.expires_at)
    if expires is None:
        return True
    return expires > (now or datetime.now(timezone.utc))


async def active_grant_for(
    db: AsyncSession, *, username: str, camera_id: str
) -> VideoAccessGrant | None:
    """The live grant letting `username` view `camera_id`, if any."""
    rows = (
        await db.execute(
            select(VideoAccessGrant)
            .where(VideoAccessGrant.requested_by == username)
            .where(VideoAccessGrant.camera_id == camera_id)
            .where(VideoAccessGrant.status == GRANTED)
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for grant in rows:
        if is_active(grant, now=now):
            return grant
    return None


async def request_access(
    db: AsyncSession,
    *,
    user: DemoUser,
    camera: CameraRow,
    reason: str,
    case_id: str | None = None,
    modes: list[str] | None = None,
) -> VideoAccessGrant:
    """Raise a request against the unit that owns the camera."""
    if user.may_access_department(camera.owning_department):
        raise GrantError(
            f"'{camera.camera_id}' already belongs to your own unit "
            f"({camera.owning_department}); no cross-unit request is needed."
        )

    existing = (
        await db.execute(
            select(VideoAccessGrant)
            .where(VideoAccessGrant.requested_by == user.username)
            .where(VideoAccessGrant.camera_id == camera.camera_id)
            .where(VideoAccessGrant.status.in_([REQUESTED, GRANTED]))
        )
    ).scalars().all()
    for grant in existing:
        if grant.status == REQUESTED:
            raise DuplicateRequest(
                f"You already have a pending request ({grant.grant_id}) for this camera."
            )
        if is_active(grant):
            raise DuplicateRequest(
                f"You already hold an active grant ({grant.grant_id}) for this camera."
            )

    row = VideoAccessGrant(
        grant_id=new_grant_id(),
        camera_id=camera.camera_id,
        owning_department=camera.owning_department,
        requested_by=user.username,
        requester_department=user.department,
        requester_role=user.role,
        reason=reason,
        case_id=case_id,
        status=REQUESTED,
        requested_at=datetime.now(timezone.utc),
        allowed_modes=list(modes or ["live", "playback"]),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "video access requested: %s by %s on %s",
        row.grant_id, user.username, camera.camera_id,
    )
    return row


async def decide(
    db: AsyncSession,
    *,
    grant: VideoAccessGrant,
    decider: DemoUser,
    approve: bool,
    settings: Settings,
    note: str | None = None,
    modes: list[str] | None = None,
    days: int | None = None,
) -> VideoAccessGrant:
    """Grant or refuse. Only the OWNING unit may decide."""
    if not decider.may_access_department(grant.owning_department):
        raise GrantError(
            f"Only {grant.owning_department} may decide requests for its own cameras."
        )
    if grant.status != REQUESTED:
        raise GrantError(f"This request is already {grant.status}.")

    now = datetime.now(timezone.utc)
    grant.decided_by = decider.username
    grant.decided_at = now
    grant.decision_note = note

    if approve:
        grant.status = GRANTED
        grant.allowed_modes = list(modes or grant.allowed_modes or ["live", "playback"])
        grant.expires_at = now + timedelta(days=days or settings.video_grant_default_days)
    else:
        grant.status = DENIED

    await db.commit()
    await db.refresh(grant)
    logger.info(
        "video access %s: %s by %s", "granted" if approve else "denied",
        grant.grant_id, decider.username,
    )
    return grant


async def revoke(
    db: AsyncSession, *, grant: VideoAccessGrant, actor: DemoUser, note: str | None = None
) -> VideoAccessGrant:
    """Withdraw a live grant. The owning unit, or the requester themselves."""
    owns = actor.may_access_department(grant.owning_department)
    if not owns and actor.username != grant.requested_by:
        raise GrantError("Only the owning unit or the requester may revoke this grant.")
    if grant.status not in (GRANTED, REQUESTED):
        raise GrantError(f"This request is already {grant.status}.")

    grant.status = REVOKED
    grant.decided_by = actor.username
    grant.decided_at = datetime.now(timezone.utc)
    grant.decision_note = note or grant.decision_note
    await db.commit()
    await db.refresh(grant)
    return grant


async def visible_grants(
    db: AsyncSession, *, user: DemoUser, status: str | None = None
) -> list[VideoAccessGrant]:
    """Requests this account raised, plus requests against its own unit."""
    rows = (
        await db.execute(select(VideoAccessGrant).order_by(VideoAccessGrant.requested_at.desc()))
    ).scalars().all()

    out = []
    for grant in rows:
        mine = grant.requested_by == user.username
        ours = user.may_access_department(grant.owning_department)
        if not (mine or ours):
            continue
        if status and grant.status != status:
            continue
        out.append(grant)
    return out
