"""The video authorisation decision, in one place.

Every video path in the system routes through `evaluate()`. Nothing else is
allowed to decide whether footage may be seen — routers ask this module and
obey the answer.

The conditions, all of which must hold, in the order they are checked:

    1. user authenticated          (enforced upstream by the bearer dependency)
    2. user active
    3. role permits video          (and, for oversight roles, is opt-in)
    4. camera is commissioned, owner-enabled and healthy
    5. the camera is in the user's own unit, OR the owning unit granted access
       (a CENTRAL oversight account is never "own unit" - it always asks)
    6. city / zone scope matches   (own-unit access only)
    7. the requested mode is allowed by role, camera and grant

Camera state is checked before scope so that a cross-unit grant can never
smuggle access to a suspended or owner-disabled camera.

Appearing in the registry is explicitly NOT sufficient. A camera an operator
can *read* is routinely a camera that operator may not *watch*.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import CENTRAL_OVERSIGHT_ROLES, DemoUser, Permission, Settings
from ..models import Camera as CameraRow
from ..schemas import CameraStatus, InstallationStatus, VideoAccessState, VideoMode


@dataclass(frozen=True)
class VideoDecision:
    """The outcome of an authorisation check.

    `state` is what the UI renders; `reason` is what the operator reads and
    what lands in the audit trail on a denial.
    """

    allowed: bool
    state: VideoAccessState
    reason: str
    #: Which modes survived every check.
    allowed_modes: tuple[str, ...] = ()

    def permits(self, mode: str | VideoMode) -> bool:
        value = mode.value if isinstance(mode, VideoMode) else str(mode)
        return self.allowed and value in self.allowed_modes


_DENY_NOT_ENABLED = "The owning department has not enabled brokered video for this camera."
_DENY_ROLE = "Your role does not permit footage viewing."
_DENY_INACTIVE = "This account is not active."
_DENY_DEPARTMENT = "This camera belongs to a department outside your scope."
_DENY_CITY = "This camera is in a city outside your scope."
_DENY_ZONE = "This camera is in a zone outside your assignment."
_DENY_WITHDRAWN = "This camera is suspended or decommissioned."
_DENY_OFFLINE = "This camera is not currently reporting a healthy feed."
_DENY_NO_MODE = "No viewing mode is available to you for this camera."
_NEEDS_GRANT = (
    "This camera belongs to another unit. Request access from the owning "
    "department; footage is shared only when that unit agrees."
)
_NEEDS_OPERATOR_APPROVAL = (
    "Central accounts do not hold footage by default. Request access and the "
    "operator who owns this camera decides; you get a time-boxed, revocable "
    "grant only if they agree."
)


def evaluate(
    user: DemoUser,
    camera: CameraRow,
    settings: Settings,
    grant: list[str] | None = None,
) -> VideoDecision:
    """Decide what `user` may do with `camera`'s footage. Never raises.

    `grant` is the list of modes the owning unit has granted this user for
    this camera, or None. It is only consulted for cross-unit access - a
    grant is not needed, and not checked, inside your own unit.
    """

    # --- global kill switch ------------------------------------------------
    if not settings.video_enabled:
        return VideoDecision(
            False,
            VideoAccessState.DENIED,
            "Video brokering is disabled on this deployment.",
        )

    # --- 2. account active -------------------------------------------------
    if not user.active:
        return VideoDecision(False, VideoAccessState.DENIED, _DENY_INACTIVE)

    # --- 3. role permits video --------------------------------------------
    # Oversight roles (state/city admin) are metadata-first: they hold the
    # permission only when a deployment explicitly opts them in.
    if not settings.role_grants_video(user.role):
        return VideoDecision(False, VideoAccessState.DENIED, _DENY_ROLE)

    # An opted-in oversight role holds no video permission in the role table -
    # the deployment flag IS its grant, so honour it here too. Without this the
    # flag opens the gate above and is then refused by this check, which made
    # VIGENTRA_STATE_ADMIN_VIDEO a switch that did nothing.
    opted_in = settings.role_video_opt_in(user.role)
    can_live = user.can(Permission.VIDEO_LIVE) or opted_in
    can_playback = user.can(Permission.VIDEO_PLAYBACK) or opted_in
    if not (can_live or can_playback):
        return VideoDecision(False, VideoAccessState.DENIED, _DENY_ROLE)

    # --- 4. camera state first --------------------------------------------
    # Checked before scope so that a grant can never smuggle access to a
    # suspended camera, and so an in-scope operator gets the accurate reason.
    if camera.installation_status != InstallationStatus.COMMISSIONED.value:
        return VideoDecision(False, VideoAccessState.CAMERA_UNAVAILABLE, _DENY_WITHDRAWN)

    if not camera.video_access_enabled:
        return VideoDecision(False, VideoAccessState.NOT_ENABLED_BY_OWNER, _DENY_NOT_ENABLED)

    # A camera that is offline has nothing to serve. Degraded still streams.
    if camera.health_status in (CameraStatus.OFFLINE.value, CameraStatus.UNAVAILABLE.value):
        return VideoDecision(False, VideoAccessState.CAMERA_UNAVAILABLE, _DENY_OFFLINE)

    # --- 5. own unit, or a grant from the owning unit ----------------------
    # Metadata federates automatically; footage does not. Inside your own unit
    # the normal scope rules apply. Outside it, you must have asked the owning
    # unit and been granted access.
    #
    # A central oversight account never counts as "inside the unit", however
    # wide its scope. State-level visibility is what lets it see that a camera
    # exists; watching what that camera sees stays the operator's decision, and
    # the request has to reach a human in the owning unit first.
    is_central = user.role in CENTRAL_OVERSIGHT_ROLES
    if is_central or not user.may_access_department(camera.owning_department):
        if grant is None:
            return VideoDecision(
                False,
                VideoAccessState.NEEDS_UNIT_APPROVAL,
                _NEEDS_OPERATOR_APPROVAL if is_central else _NEEDS_GRANT,
            )
        # The owner shared this specific camera deliberately, so the requester's
        # own city and zone no longer apply - the grant is the decision.
        return _decide_modes(user, camera, granted_modes=list(grant), opted_in=opted_in)

    # --- 6. city and zone scope (own unit only) ---------------------------
    if not user.may_access_city(camera.city):
        return VideoDecision(False, VideoAccessState.DENIED, _DENY_CITY)
    if not user.may_access_zone(camera.zone):
        return VideoDecision(False, VideoAccessState.DENIED, _DENY_ZONE)

    # --- 8. which modes survive -------------------------------------------
    return _decide_modes(user, camera, opted_in=opted_in)


def _decide_modes(
    user: DemoUser,
    camera: CameraRow,
    granted_modes: list[str] | None = None,
    *,
    opted_in: bool = False,
) -> VideoDecision:
    """Intersect what the role allows, what the camera offers, and any grant.

    `opted_in` carries the deployment flag for oversight roles, which hold no
    video permission of their own - see `Settings.role_video_opt_in`.
    """
    can_live = user.can(Permission.VIDEO_LIVE) or opted_in
    can_playback = user.can(Permission.VIDEO_PLAYBACK) or opted_in
    capabilities = {str(item).lower() for item in (camera.capabilities or [])}
    allowed = {str(item).lower() for item in granted_modes} if granted_modes is not None else None

    modes: list[str] = []
    for mode, permitted in ((VideoMode.LIVE.value, can_live), (VideoMode.PLAYBACK.value, can_playback)):
        if not permitted or mode not in capabilities:
            continue
        if allowed is not None and mode not in allowed:
            continue
        modes.append(mode)

    if not modes:
        return VideoDecision(False, VideoAccessState.DENIED, _DENY_NO_MODE)

    if len(modes) == 2:
        state = VideoAccessState.LIVE_AND_PLAYBACK
    elif modes[0] == VideoMode.LIVE.value:
        state = VideoAccessState.LIVE_ONLY
    else:
        state = VideoAccessState.PLAYBACK_ONLY

    reason = (
        f"Granted by {camera.owning_department} for this camera."
        if granted_modes is not None
        else "Authorized - this camera belongs to your own unit and is in your scope."
    )
    return VideoDecision(True, state, reason, tuple(modes))


def watermark_for(user: DemoUser, camera: CameraRow) -> str:
    """Identify the viewer on the footage itself.

    Format: `Department | username | camera_id | UTC timestamp`. Rendered as an
    overlay by the player and recorded on the session, so a screen-captured
    frame still carries who pulled it.
    """
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    return f"{camera.owning_department} | {user.username} | {camera.camera_id} | {stamp}"
