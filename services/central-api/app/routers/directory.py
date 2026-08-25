"""City and department directory.

Two browse axes over one normalised table. There is deliberately no per-city
or per-department database: `cameras.city_normalized` and
`cameras.department_code` are indexed, and every query filters rather than
routing to a separate store. Adding a city is inserting rows, not provisioning
infrastructure.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission, SOURCE_BY_DEPARTMENT
from ..database import get_db
from ..dependencies import require_permission
from ..models import Camera as CameraRow
from ..schemas import CameraStatus, CityOut, DepartmentOut
from ..services.policy_service import may_read_camera

router = APIRouter(prefix="/api/v1", tags=["directory"])


def _sorted_unique(values) -> list[str]:
    return sorted({value for value in values if value})


@router.get("/cities", response_model=list[CityOut], summary="Cities present in the registry")
async def list_cities(
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[CityOut]:
    """Every city in the registry, with its departments and zones.

    Federation-wide, matching `/api/v1/cameras`. A directory that hides half
    the registry from the page that lists it would just be a second, quieter
    answer to the same question.
    """
    cameras = [
        row
        for row in (await db.execute(select(CameraRow))).scalars().all()
        if may_read_camera(user, row)
    ]

    grouped: dict[str, list[CameraRow]] = {}
    for row in cameras:
        grouped.setdefault(row.city_normalized or "", []).append(row)

    out: list[CityOut] = []
    for normalized, rows in grouped.items():
        if not normalized:
            continue
        out.append(
            CityOut(
                city=rows[0].city,
                city_normalized=normalized,
                camera_count=len(rows),
                online=sum(1 for r in rows if r.health_status == CameraStatus.ONLINE.value),
                departments=_sorted_unique(r.owning_department for r in rows),
                districts=_sorted_unique(r.district for r in rows),
                zones=_sorted_unique(r.zone for r in rows),
            )
        )
    out.sort(key=lambda item: item.city)
    return out


@router.get(
    "/departments",
    response_model=list[DepartmentOut],
    summary="Departments present in the registry",
)
async def list_departments(
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[DepartmentOut]:
    """Every department this account may see, with its cities and video reach."""
    cameras = [
        row
        for row in (await db.execute(select(CameraRow))).scalars().all()
        if may_read_camera(user, row)
    ]

    grouped: dict[str, list[CameraRow]] = {}
    for row in cameras:
        grouped.setdefault(row.owning_department, []).append(row)

    out = [
        DepartmentOut(
            department=department,
            department_code=rows[0].department_code or "",
            source_system=SOURCE_BY_DEPARTMENT.get(department),
            camera_count=len(rows),
            online=sum(1 for r in rows if r.health_status == CameraStatus.ONLINE.value),
            cities=_sorted_unique(r.city for r in rows),
            # How many of this department's cameras the OWNER has enabled for
            # brokering - not how many this particular account may watch.
            video_capable_cameras=sum(1 for r in rows if r.video_access_enabled),
        )
        for department, rows in grouped.items()
    ]
    out.sort(key=lambda item: item.department)
    return out
