"""Installation onboarding, routed to the owning department's own system.

Vigentra does not host the installation register. Each department does. This
service resolves which department a form belongs to, calls that department's
adapter, and mirrors the result centrally so the pipeline stays listable and
auditable.

The consequence worth stating: an installation form submitted here lands in the
Traffic Police's or the Municipal Corporation's system, and validated there. Passing
validation registers it immediately - `sync_service` then brings the metadata
across. There is no approval step in this path.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.base import AdapterError, SurveillanceAdapter
from ..config import DemoUser, Settings
from ..models import InstallationRequest
from ..schemas import InstallationRequestOut, RequestStatus
from . import sync_service

logger = logging.getLogger("vigentra.installation")


class DepartmentNotFederated(Exception):
    """No department system is registered for this department name."""


class RequestNotFound(Exception):
    """No installation record with this ID in any permitted department."""


class NotPermitted(Exception):
    """The signed-in account may not act on this record."""


#: Only the operator who raised a record - or an account in the same department
#: with a broader role - may edit it, and only while it is still editable.
EDITABLE_STATUSES = {
    RequestStatus.DRAFT.value,
    RequestStatus.VALIDATION_FAILED.value,
}


def adapter_for_department(
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    department: str,
) -> tuple[str, SurveillanceAdapter]:
    source = settings.source_for_department(department)
    if source is None or source.source_system not in adapters:
        raise DepartmentNotFederated(department)
    return source.source_system, adapters[source.source_system]


def permitted_sources(
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    user: DemoUser,
) -> list[tuple[str, SurveillanceAdapter]]:
    """Every department system this account is allowed to talk to."""
    out = []
    for source in settings.sources:
        if source.source_system not in adapters:
            continue
        if user.may_access_department(source.department):
            out.append((source.source_system, adapters[source.source_system]))
    return out


async def _mirror(db: AsyncSession, request: InstallationRequestOut) -> InstallationRequestOut:
    await sync_service.mirror_request(db, request)
    await db.commit()
    return request


def _from_mirror(row: InstallationRequest, *, stale: bool = True) -> InstallationRequestOut:
    """Rebuild a record from the central mirror when its department is down."""
    return InstallationRequestOut(
        request_id=row.request_id,
        source_system=row.source_system,
        owning_department=row.owning_department,
        owning_unit=row.owning_unit,
        status=row.status,
        camera_name=row.camera_name,
        external_camera_id=row.external_camera_id,
        district=row.district,
        created_by=row.created_by,
        created_at=row.created_at,
        submitted_by=row.submitted_by,
        submitted_at=row.submitted_at,
        approved_by=row.approved_by,
        approved_by_role=row.approved_by_role,
        approved_at=row.approved_at,
        rejected_by=row.rejected_by,
        rejected_at=row.rejected_at,
        rejection_reason=row.rejection_reason,
        withdrawal_reason=row.withdrawal_reason,
        synchronized_at=row.synchronized_at,
        updated_at=row.updated_at,
        validation_errors=list(row.validation_errors or []),
        form=dict(row.installation_form_json or {}),
        attachments=[],
    )


async def list_requests(
    db: AsyncSession,
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    user: DemoUser,
) -> tuple[list[InstallationRequestOut], list[str]]:
    """List every record this account may see, across permitted departments.

    A department system being unreachable degrades to its last mirrored state
    rather than failing the whole listing - the same isolation guarantee the
    metadata sync gives.
    """
    records: list[InstallationRequestOut] = []
    warnings: list[str] = []

    for source_system, adapter in permitted_sources(adapters, settings, user):
        try:
            live = await adapter.list_installation_requests()
        except AdapterError as exc:
            warnings.append(f"{source_system}: {exc} - showing last known state")
            rows = (
                await db.execute(
                    select(InstallationRequest).where(
                        InstallationRequest.source_system == source_system
                    )
                )
            ).scalars().all()
            records.extend(_from_mirror(row) for row in rows)
            continue

        for record in live:
            await sync_service.mirror_request(db, record)
        records.extend(live)

    await db.commit()
    records.sort(key=lambda item: (item.updated_at or item.created_at or 0) or 0, reverse=True)
    return records, warnings


async def get_request(
    db: AsyncSession,
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    user: DemoUser,
    request_id: str,
) -> InstallationRequestOut:
    """Fetch one record from its owning department system."""
    row = (
        await db.execute(
            select(InstallationRequest).where(InstallationRequest.request_id == request_id)
        )
    ).scalar_one_or_none()

    candidates: list[tuple[str, SurveillanceAdapter]]
    if row is not None:
        if not user.may_access_department(row.owning_department):
            raise NotPermitted(request_id)
        candidates = [
            (name, adapter)
            for name, adapter in permitted_sources(adapters, settings, user)
            if name == row.source_system
        ]
    else:
        candidates = permitted_sources(adapters, settings, user)

    last_error: AdapterError | None = None
    for _, adapter in candidates:
        try:
            record = await adapter.get_installation_request(request_id)
        except AdapterError as exc:
            last_error = exc
            continue
        if not user.may_access_department(record.owning_department):
            raise NotPermitted(request_id)
        return await _mirror(db, record)

    if row is not None:
        logger.warning("installation %s served from mirror: %s", request_id, last_error)
        return _from_mirror(row)
    raise RequestNotFound(request_id)


async def create_request(
    db: AsyncSession,
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    user: DemoUser,
    form: Any,
) -> InstallationRequestOut:
    """Create a draft in the operator's own department system."""
    department = form.owning_department
    if not user.may_access_department(department):
        raise NotPermitted(department)

    _, adapter = adapter_for_department(adapters, settings, department)
    record = await adapter.create_installation_request(form, created_by=user.username)
    return await _mirror(db, record)


async def _load_for_action(
    db: AsyncSession,
    adapters: dict[str, SurveillanceAdapter],
    settings: Settings,
    user: DemoUser,
    request_id: str,
) -> tuple[InstallationRequestOut, SurveillanceAdapter]:
    record = await get_request(db, adapters, settings, user, request_id)
    _, adapter = adapter_for_department(adapters, settings, record.owning_department)
    return record, adapter


async def update_request(
    db: AsyncSession, adapters, settings, user: DemoUser, request_id: str, patch: Any
) -> InstallationRequestOut:
    record, adapter = await _load_for_action(db, adapters, settings, user, request_id)
    _assert_editable(record, user)
    updated = await adapter.update_installation_request(
        request_id, patch, updated_by=user.username
    )
    return await _mirror(db, updated)


async def submit_request(
    db: AsyncSession, adapters, settings, user: DemoUser, request_id: str
) -> InstallationRequestOut:
    record, adapter = await _load_for_action(db, adapters, settings, user, request_id)
    _assert_editable(record, user)
    submitted = await adapter.submit_installation_request(request_id, submitted_by=user.username)
    result = await _mirror(db, submitted)

    # A successful bulk or single-form submission should seed the central
    # registry immediately. Keep the department record REGISTERED until the
    # normal acknowledgement cycle confirms synchronization.
    status_value = submitted.status if isinstance(submitted.status, str) else submitted.status.value
    if status_value == RequestStatus.REGISTERED.value and not submitted.validation_errors:
        source_system = adapter.source_system
        await sync_service.sync_all(
            db,
            {source_system: adapter},
            settings,
            triggered_by=user.username,
            acknowledge_to_source=False,
        )
    return result


async def suspend_request(
    db: AsyncSession, adapters, settings, user: DemoUser, request_id: str, reason: str | None
) -> InstallationRequestOut:
    _, adapter = await _load_for_action(db, adapters, settings, user, request_id)
    suspended = await adapter.suspend_installation_request(
        request_id, actor=user.username, reason=reason
    )
    return await _mirror(db, suspended)


async def decommission_request(
    db: AsyncSession, adapters, settings, user: DemoUser, request_id: str, reason: str | None
) -> InstallationRequestOut:
    _, adapter = await _load_for_action(db, adapters, settings, user, request_id)
    retired = await adapter.decommission_installation_request(
        request_id, actor=user.username, reason=reason
    )
    return await _mirror(db, retired)


def _assert_editable(record: InstallationRequestOut, user: DemoUser) -> None:
    """A record leaves the operator's hands the moment it is submitted.

    After submission only the approval path can move it, so an operator cannot
    quietly alter a form that an approver has already started reading.
    """
    status = record.status if isinstance(record.status, str) else record.status.value
    if status not in EDITABLE_STATUSES:
        raise NotPermitted(
            f"Record {record.request_id} is {status} and can no longer be edited by an operator"
        )
    # An operator may only work on their own drafts; departmental roles above
    # them are not restricted this way.
    from ..config import Role

    if user.role == Role.INSTALLATION_OPERATOR and record.created_by not in (None, user.username):
        raise NotPermitted(
            f"Record {record.request_id} was raised by {record.created_by}"
        )
