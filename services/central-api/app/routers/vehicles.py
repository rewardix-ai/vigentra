"""Vehicle reference registry.

A standalone lookup of vehicle attributes by registration number. It is
deliberately **not** connected to cameras, detections or video sessions: there
is no endpoint here that takes a camera ID, and no endpoint elsewhere that
returns a registration number.

Every read is audited, and every response repeats the boundary so no consumer
can mistake this for a sightings database.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import SettingsDep, client_ip, require_permission
from ..models import Vehicle
from ..schemas import VehicleFacets, VehicleOut, VehicleSearchResponse
from ..services import audit_service, vehicle_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType

logger = logging.getLogger("sentinel.vehicles.router")

router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicle reference registry"])

#: Repeated on every response. Cheap, and it means an integrator cannot mistake
#: this for something that knows where a vehicle has been.
SCOPE_NOTE = (
    "Vehicle attribute reference only. This registry holds no owner details and "
    "is not linked to any camera, detection or sighting."
)


def _to_out(row: Vehicle) -> VehicleOut:
    return VehicleOut(
        registration_number=row.registration_number,
        registration_date=row.registration_date,
        registration_valid_upto=row.registration_valid_upto,
        vehicle_category_code=row.vehicle_category_code,
        vehicle_class=row.vehicle_class,
        make=row.make,
        model=row.model,
        body_type=row.body_type,
        fuel_type=row.fuel_type,
        colour=row.colour,
        registration_status=row.registration_status,
        registered_at=row.registered_at,
        status_as_on=row.status_as_on,
        source=row.source,
        is_demo_data=row.is_demo_data,
        imported_at=row.imported_at,
    )


@router.get("", response_model=VehicleSearchResponse, summary="Search the vehicle reference registry")
async def search_vehicles(
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.VEHICLE_REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, description="Registration number, make or model"),
    make: str | None = Query(default=None),
    vehicle_class: str | None = Query(default=None),
    registration_status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> VehicleSearchResponse:
    rows, total = await vehicle_service.search(
        db,
        query=q,
        make=make,
        vehicle_class=vehicle_class,
        registration_status=registration_status,
        limit=limit,
        offset=offset,
    )

    # Searching a vehicle register is exactly the kind of read that should
    # leave a trace, so the query itself is recorded, not just the fact of a
    # request.
    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VEHICLE_REGISTRY_SEARCHED,
        resource_type=ResourceType.VEHICLE,
        client_ip=client_ip(request),
        details={
            "query": q,
            "make": make,
            "vehicle_class": vehicle_class,
            "registration_status": registration_status,
            "returned": len(rows),
            "total": total,
        },
    )

    return VehicleSearchResponse(
        items=[_to_out(row) for row in rows],
        total=total,
        filters={
            key: value
            for key, value in {
                "q": q,
                "make": make,
                "vehicle_class": vehicle_class,
                "registration_status": registration_status,
            }.items()
            if value
        },
        scope_note=SCOPE_NOTE,
    )


@router.get("/facets", response_model=VehicleFacets, summary="Distinct filter values")
async def vehicle_facets(
    user: DemoUser = Depends(require_permission(Permission.VEHICLE_REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> VehicleFacets:
    return VehicleFacets(**await vehicle_service.facets(db))


@router.get(
    "/{registration_number}",
    response_model=VehicleOut,
    summary="Look up one registration number",
)
async def get_vehicle(
    registration_number: str,
    request: Request,
    user: DemoUser = Depends(require_permission(Permission.VEHICLE_REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> VehicleOut:
    """Exact lookup. Spacing and hyphenation are ignored."""
    row = await vehicle_service.lookup(db, registration_number)

    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.VEHICLE_RECORD_VIEWED,
        outcome=AuditOutcome.SUCCESS if row else AuditOutcome.ERROR,
        resource_type=ResourceType.VEHICLE,
        resource_id=vehicle_service.normalize_plate(registration_number),
        client_ip=client_ip(request),
        details={"found": row is not None},
    )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No vehicle '{registration_number}' in the reference registry. "
                "This registry is a static reference table, not a live VAHAN query."
            ),
        )
    return _to_out(row)
