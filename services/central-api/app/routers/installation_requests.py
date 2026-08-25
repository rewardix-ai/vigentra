"""CCTV installation onboarding.

Every write here is routed to the OWNING DEPARTMENT's own system. Sentinel does
not hold the authoritative register, does not validate on the department's
behalf, and cannot approve a camera itself. It orchestrates, mirrors and audits.

Lifecycle:

    DRAFT -> SUBMITTED -> VALIDATION_FAILED   (fix and resubmit)
                       -> REGISTERED          (live in the central registry)
                       -> SYNCHRONIZED
                       -> SUSPENDED | DECOMMISSIONED

There is no approval step. The unit had already decided to install the camera;
asking it to also approve its own paperwork added delay without adding a
decision. Validation still gates entry - bad coordinates never reach the
registry - and withdrawal still works.

Footage is the part that IS gated: see routers/video_grants.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..adapters.base import AdapterError
from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import AdaptersDep, SettingsDep, client_ip, require_permission
from ..schemas import (
    InstallationRequestCreate,
    InstallationRequestOut,
    InstallationRequestPatch,
    WithdrawalRequest,
)
from ..services import audit_service, installation_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.installation_service import (
    DepartmentNotFederated,
    NotPermitted,
    RequestNotFound,
)

router = APIRouter(prefix="/api/v1/installation-requests", tags=["installation onboarding"])


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, NotPermitted):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, RequestNotFound):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown installation request '{exc}'"
        )
    if isinstance(exc, DepartmentNotFederated):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No department system is federated for '{exc}'",
        )
    if isinstance(exc, AdapterError):
        return HTTPException(status_code=exc.http_status, detail=exc.to_dict())
    raise exc


async def _audit(
    db: AsyncSession,
    request: Request,
    user: DemoUser,
    action: AuditAction,
    record: InstallationRequestOut,
    *,
    reason: str | None = None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
) -> None:
    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=action,
        outcome=outcome,
        resource_type=ResourceType.INSTALLATION_REQUEST,
        resource_id=record.request_id,
        department=record.owning_department,
        source_system=record.source_system,
        case_or_reason=reason,
        client_ip=client_ip(request),
        details={
            "status": record.status if isinstance(record.status, str) else record.status.value,
            "camera_name": record.camera_name,
            "external_camera_id": record.external_camera_id,
        },
    )


@router.post(
    "",
    response_model=InstallationRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a CCTV installation form (DRAFT)",
)
async def create_request(
    payload: InstallationRequestCreate,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_CREATE)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    """Create a draft in the operator's own department system.

    The form is stored by that department, not by Sentinel. It reaches the
    central registry as soon as it is submitted and passes validation.
    """
    try:
        record = await installation_service.create_request(
            db, adapters, settings, user, payload.form
        )
    except Exception as exc:
        raise _handle(exc) from exc

    await _audit(db, request, user, AuditAction.INSTALLATION_FORM_CREATED, record)
    return record


@router.get("", response_model=list[InstallationRequestOut], summary="List installation records")
async def list_requests(
    request: Request,
    response: Response,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_READ)),
    db: AsyncSession = Depends(get_db),
    request_status: str | None = Query(default=None, alias="status"),
    owning_department: str | None = Query(default=None),
) -> list[InstallationRequestOut]:
    records, warnings = await installation_service.list_requests(db, adapters, settings, user)

    if request_status:
        wanted = request_status.upper()
        records = [
            item for item in records
            if (item.status if isinstance(item.status, str) else item.status.value) == wanted
        ]
    if owning_department:
        records = [item for item in records if item.owning_department == owning_department]

    if warnings:
        # Surfaced as a header so the UI can flag stale rows without the list
        # itself failing when one department system is down.
        response.headers["X-Sentinel-Degraded"] = "; ".join(warnings)[:500]

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.INSTALLATION_REGISTER_VIEWED,
        outcome=AuditOutcome.PARTIAL if warnings else AuditOutcome.SUCCESS,
        resource_type=ResourceType.INSTALLATION_REQUEST,
        department=user.department,
        client_ip=client_ip(request),
        details={"returned": len(records), "warnings": warnings},
    )
    return records


@router.get(
    "/{request_id}", response_model=InstallationRequestOut, summary="Read one installation record"
)
async def get_request(
    request_id: str,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_READ)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    try:
        record = await installation_service.get_request(db, adapters, settings, user, request_id)
    except Exception as exc:
        raise _handle(exc) from exc

    await _audit(db, request, user, AuditAction.INSTALLATION_REQUEST_VIEWED, record)
    return record


@router.patch(
    "/{request_id}", response_model=InstallationRequestOut, summary="Edit a draft installation form"
)
async def update_request(
    request_id: str,
    payload: InstallationRequestPatch,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    """Only a draft (or a returned form) can be edited, and only by its raiser."""
    try:
        record = await installation_service.update_request(
            db, adapters, settings, user, request_id, payload.form
        )
    except Exception as exc:
        raise _handle(exc) from exc

    await _audit(db, request, user, AuditAction.INSTALLATION_FORM_UPDATED, record)
    return record


@router.post(
    "/{request_id}/submit",
    response_model=InstallationRequestOut,
    summary="Submit a form for department validation",
)
async def submit_request(
    request_id: str,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_SUBMIT)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    """Validation runs inside the owning department's system, not here.

    A form that fails it comes back as VALIDATION_FAILED with the department's
    own error list attached, and can be corrected and resubmitted. A form that
    passes becomes REGISTERED immediately - it is visible centrally from that
    moment, with no human approval step in between.
    """
    try:
        record = await installation_service.submit_request(
            db, adapters, settings, user, request_id
        )
    except Exception as exc:
        raise _handle(exc) from exc

    failed = record.validation_errors
    await _audit(
        db, request, user, AuditAction.INSTALLATION_FORM_SUBMITTED, record,
        outcome=AuditOutcome.ERROR if failed else AuditOutcome.SUCCESS,
        reason="; ".join(failed)[:500] if failed else None,
    )
    return record


@router.post(
    "/{request_id}/suspend",
    response_model=InstallationRequestOut,
    summary="Suspend a commissioned camera",
)
async def suspend_request(
    request_id: str,
    payload: WithdrawalRequest,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_SUSPEND)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    """The camera stays in the register and becomes unavailable on the next sync."""
    try:
        record = await installation_service.suspend_request(
            db, adapters, settings, user, request_id, payload.reason
        )
    except Exception as exc:
        raise _handle(exc) from exc

    await _audit(
        db, request, user, AuditAction.INSTALLATION_REQUEST_SUSPENDED, record,
        reason=payload.reason,
    )
    return record


@router.post(
    "/{request_id}/decommission",
    response_model=InstallationRequestOut,
    summary="Permanently retire a camera asset",
)
async def decommission_request(
    request_id: str,
    payload: WithdrawalRequest,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_DECOMMISSION)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    try:
        record = await installation_service.decommission_request(
            db, adapters, settings, user, request_id, payload.reason
        )
    except Exception as exc:
        raise _handle(exc) from exc

    await _audit(
        db, request, user, AuditAction.INSTALLATION_REQUEST_DECOMMISSIONED, record,
        reason=payload.reason,
    )
    return record


@router.post(
    "/{request_id}/sync-metadata",
    response_model=InstallationRequestOut,
    summary="Push this record's metadata into the central registry",
)
async def sync_metadata(
    request_id: str,
    request: Request,
    adapters: AdaptersDep,
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_SYNC)),
    db: AsyncSession = Depends(get_db),
) -> InstallationRequestOut:
    """Synchronise one record, rather than waiting for a full registry sync.

    Refuses anything still in drafting or failing validation - the same gate the
    bulk sync applies, enforced per record.
    """
    from ..schemas import PUBLISHABLE_STATUSES
    from ..services import sync_service

    try:
        record = await installation_service.get_request(db, adapters, settings, user, request_id)
    except Exception as exc:
        raise _handle(exc) from exc

    status_value = record.status if isinstance(record.status, str) else record.status.value
    if status_value not in {item.value for item in PUBLISHABLE_STATUSES}:
        await _audit(
            db, request, user, AuditAction.CAMERA_METADATA_SYNCHRONIZED, record,
            outcome=AuditOutcome.DENIED,
            reason=f"record is {status_value}; only registered records may be synchronised",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Installation request '{request_id}' is {status_value}. Only REGISTERED "
                "or SYNCHRONIZED records may be published to the central registry."
            ),
        )

    result = await sync_service.sync_all(
        db, {record.source_system: adapters[record.source_system]}, settings,
        triggered_by=user.username,
    )
    refreshed = await installation_service.get_request(db, adapters, settings, user, request_id)

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.CAMERA_METADATA_SYNCHRONIZED,
        resource_type=ResourceType.INSTALLATION_REQUEST,
        resource_id=request_id,
        department=record.owning_department,
        source_system=record.source_system,
        client_ip=client_ip(request),
        details={
            "metadata_only": True,
            "per_source": {
                name: item.model_dump() for name, item in result.sources.items()
            },
        },
    )
    return refreshed
