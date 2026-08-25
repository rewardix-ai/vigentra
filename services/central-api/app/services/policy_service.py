"""Access policy: local video permissions, and Sentinel metadata visibility.

Three separate concepts, kept separate on purpose:

  1. **Local VMS permission policy** - who may view the actual FOOTAGE inside
     the owning department's system. Sentinel records the summary and enforces
     nothing here; the department's own system does that.
  2. **Sentinel registry permission** - who may read a camera's metadata,
     health and policy summary in Sentinel. Federation-wide: any account with
     `registry:read` can see any camera in the federation, because a registry
     that hides half the state's cameras from the other half is not a registry.
     What varies is the DEPTH, handled by `effective_visibility` below.
  3. **Sentinel video access** - a separate decision entirely, made in
     `video_permissions.py`. Reading a camera's record grants no footage.

The split in 2 and 3 is the whole access model in one sentence: everyone can
see that a camera exists; only the owning unit decides who may look through it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Visibility
from ..models import Camera as CameraRow
from ..models import CameraAccessPolicy
from ..schemas import CameraMetadata

#: Fields withheld from each visibility level. FULL withholds nothing that
#: Sentinel actually holds - and Sentinel never holds an unmasked serial, an
#: admin contact, a credential or a stream URL in the first place.
REDACTED_BY_VISIBILITY: dict[str, tuple[str, ...]] = {
    Visibility.FULL: (),
    Visibility.STANDARD: (
        "camera_serial_masked",
        "installation_vendor",
    ),
    # Health monitoring needs to know whether a camera is up, not who installed
    # it, what it is worth, or what it overlooks.
    Visibility.LIMITED: (
        "camera_serial_masked",
        "installation_vendor",
        "maintenance_agency",
        "police_station_or_zone",
        "coverage_description",
        "entry_exit_zone_description",
        "address_or_landmark",
        "vendor",
        "model",
        "attachments",
    ),
}


class VideoAccessNotAvailable(RuntimeError):
    """Raised if anything attempts to enable video access in Module 1."""


def assert_no_video_access(requested: bool | None) -> bool:
    """The Module 1 invariant, enforced in code rather than trusted.

    Called on every policy write. There is deliberately no argument value that
    returns True.
    """
    if requested:
        raise VideoAccessNotAvailable(
            "Sentinel Module 1 provides metadata-only federation. "
            "Video access cannot be enabled for any camera or role."
        )
    return False


#: Reading outside your own department is capped here regardless of role. A
#: department admin is senior inside their department, not inside someone
#: else's.
CROSS_DEPARTMENT_CAP = Visibility.STANDARD

#: Most-permissive first, so capping is a list-position comparison.
_VISIBILITY_ORDER = (Visibility.FULL, Visibility.STANDARD, Visibility.LIMITED)


def effective_visibility(user: DemoUser, camera: CameraRow | None = None) -> str:
    """How deeply this account may read THIS camera.

    Own department: whatever the role allows. Another department: never more
    than STANDARD, so a cross-unit reader learns where the camera is and
    whether it is up, but not who maintains it or what the site survey said.
    """
    own = user.visibility if user.visibility in _VISIBILITY_ORDER else Visibility.LIMITED
    if camera is None or user.may_access_department(camera.owning_department):
        return own
    return max(own, CROSS_DEPARTMENT_CAP, key=_VISIBILITY_ORDER.index)


def redacted_fields_for(user: DemoUser, camera: CameraRow | None = None) -> tuple[str, ...]:
    level = effective_visibility(user, camera)
    return REDACTED_BY_VISIBILITY.get(level, REDACTED_BY_VISIBILITY[Visibility.LIMITED])


async def upsert_policy(
    db: AsyncSession,
    camera: CameraMetadata,
    *,
    approved_by: str | None = None,
) -> CameraAccessPolicy:
    """Record the access policy that arrived with an approved camera record.

    `sentinel_video_access_enabled` is written through `assert_no_video_access`
    every single time, so the column cannot drift to True by any code path.
    """
    row = (
        await db.execute(
            select(CameraAccessPolicy).where(CameraAccessPolicy.camera_id == camera.camera_id)
        )
    ).scalar_one_or_none()

    previous_roles = list(row.permitted_local_roles_json or []) if row else None
    if row is None:
        row = CameraAccessPolicy(camera_id=camera.camera_id, policy_version=1)
        db.add(row)

    row.owning_department = camera.owning_department
    row.local_video_access_enabled = bool(camera.local_video_access)
    row.sentinel_video_access_enabled = assert_no_video_access(False)
    row.permitted_local_roles_json = list(camera.permitted_local_roles)
    row.metadata_visibility_level = Visibility.STANDARD
    row.approved_by = approved_by or row.approved_by
    row.approved_at = camera.approved_at or row.approved_at or datetime.now(timezone.utc)

    # A changed local-role list is a real policy change, so version it.
    if previous_roles is not None and previous_roles != row.permitted_local_roles_json:
        row.policy_version = (row.policy_version or 1) + 1

    return row


async def get_policy(db: AsyncSession, camera_id: str) -> CameraAccessPolicy | None:
    return (
        await db.execute(
            select(CameraAccessPolicy).where(CameraAccessPolicy.camera_id == camera_id)
        )
    ).scalar_one_or_none()


def may_read_camera(user: DemoUser, camera: CameraRow) -> bool:
    """Sentinel registry permission - metadata only, never footage.

    Always true for an account that reached here, because `registry:read` is
    already required by the router and the registry is federation-wide by
    design. The per-camera question that still has a real answer is how much
    detail to return (`effective_visibility`) and whether footage is
    permitted (`video_permissions.evaluate`) - not whether the row exists.
    """
    return True


def may_read_detections(user: DemoUser, camera: CameraRow) -> bool:
    """Who may read what a camera SAW - a different question from the registry.

    Detections are derived from footage. "Two motorcycles and a person at this
    junction at 21:14" is observational content about a place at a time, not an
    asset record, so it follows the video rules rather than the metadata ones
    and stays with the owning unit.

    Deliberately not `may_read_camera`: when the registry opened up so that
    units could find each other's cameras, reusing that predicate here would
    have handed every account the whole state's detection history as a side
    effect of a change about metadata.
    """
    return user.may_access_department(camera.owning_department)


def describe_metadata_access(user: DemoUser) -> str:
    """One line the UI can show about what this role may read."""
    if user.is_statewide:
        return f"{user.visibility} metadata for all departments"
    return (
        f"{user.visibility} metadata for {user.department}; "
        f"{CROSS_DEPARTMENT_CAP} metadata for other departments"
    )
