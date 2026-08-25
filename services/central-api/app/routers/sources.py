"""Federated department systems and metadata synchronisation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import AdaptersDep, SettingsDep, client_ip, require_permission
from ..mappers import source_to_schema
from ..models import Source as SourceRow
from ..schemas import SourceOut, SyncResponse
from ..services import audit_service, sync_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType
from ..services.normalization import redact_endpoint

router = APIRouter(prefix="/api/v1/sources", tags=["federation"])


@router.get("", response_model=list[SourceOut], summary="List federated department systems")
async def list_sources(
    settings: SettingsDep,
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[SourceOut]:
    rows = (await db.execute(select(SourceRow).order_by(SourceRow.source_system_id))).scalars().all()
    by_id = {row.source_system_id: row for row in rows}

    out: list[SourceOut] = []
    for config in settings.sources:
        if not user.may_access_department(config.department):
            continue
        row = by_id.get(config.source_system)
        if row is not None:
            out.append(source_to_schema(row))
            continue
        # Registered but never synced yet - still worth showing.
        out.append(
            SourceOut(
                source_system=config.source_system,
                display_name=config.display_name,
                department=config.department,
                adapter=config.adapter,
                adapter_version="0.2.0",
                status="unknown",
                camera_count=0,
                endpoint=redact_endpoint(config.base_url),
            )
        )
    return out


@router.post("/sync", response_model=SyncResponse, summary="Synchronise approved camera metadata")
async def sync_sources(
    request: Request,
    settings: SettingsDep,
    adapters: AdaptersDep,
    user: DemoUser = Depends(require_permission(Permission.INSTALLATION_SYNC)),
    db: AsyncSession = Depends(get_db),
) -> SyncResponse:
    """Pull REGISTERED camera metadata from every department system concurrently.

    Records still inside a department's approval pipeline are counted and
    skipped. A department that is down contributes an error entry and leaves the
    rest of the run untouched, so `success` stays true as long as at least one
    department answered.

    Metadata only: no footage, no stream URL and no credential crosses here.
    """
    permitted = {
        name: adapter
        for name, adapter in adapters.items()
        if user.may_access_department(
            next((s.department for s in settings.sources if s.source_system == name), None)
        )
    }
    result = await sync_service.sync_all(db, permitted, settings, triggered_by=user.username)

    failed = [name for name, item in result.sources.items() if item.errors]
    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.CAMERA_METADATA_SYNCHRONIZED,
        outcome=AuditOutcome.PARTIAL if failed else AuditOutcome.SUCCESS,
        resource_type=ResourceType.SOURCE_SYSTEM,
        department=user.department,
        client_ip=client_ip(request),
        details={
            "metadata_only": True,
            "total_cameras": result.total_cameras,
            "per_source": {
                name: {
                    "approved_records_seen": item.approved_records_seen,
                    "synchronized": item.synchronized,
                    "skipped_unregistered": item.skipped_unregistered,
                    "withdrawn": item.withdrawn,
                    "errors": item.errors,
                }
                for name, item in result.sources.items()
            },
        },
    )
    return result
