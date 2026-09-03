"""Reports over the federated camera registry.

Gap analysis today; future reports (utilisation, sensor mix, maintenance
overdue) attach here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Permission
from ..database import get_db
from ..dependencies import require_permission
from ..models import Camera as CameraRow, Source as SourceRow
from ..schemas import AgeingCamera, CameraStatus, DistrictCoverage, GapAnalysisResponse
from ..services.policy_service import may_read_camera

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def _age_years(installation: date | None) -> float | None:
    if installation is None:
        return None
    delta = date.today() - installation
    # ~365.25 to average out leap years - accurate enough for an ageing report.
    return round(delta.days / 365.25, 2)


@router.get(
    "/gap-analysis",
    response_model=GapAnalysisResponse,
    summary="Coverage gaps and ageing infrastructure across the registry",
)
async def gap_analysis(
    user: DemoUser = Depends(require_permission(Permission.REGISTRY_READ)),
    db: AsyncSession = Depends(get_db),
    min_cameras_per_district: int = Query(default=3, ge=1, le=1000),
    ageing_years: int = Query(default=5, ge=1, le=50, description="Cameras older than this are flagged"),
) -> GapAnalysisResponse:
    """Aggregates for a coverage overview page.

    Only cameras from departments this account may read are counted.
    Suspended and decommissioned cameras are counted for the register (they
    are still recorded assets) but not counted as active coverage.
    """
    cameras = [
        row
        for row in (await db.execute(select(CameraRow))).scalars().all()
        if may_read_camera(user, row)
    ]
    sources = [
        row
        for row in (await db.execute(select(SourceRow))).scalars().all()
        if user.may_access_department(row.department)
    ]

    coverage: dict[str, DistrictCoverage] = {}
    for camera in cameras:
        entry = coverage.setdefault(
            camera.district,
            DistrictCoverage(
                district=camera.district,
                cameras=0,
                online=0,
                degraded=0,
                offline=0,
                unavailable=0,
                is_thin=False,
            ),
        )
        entry.cameras += 1
        entry.by_department[camera.owning_department] = (
            entry.by_department.get(camera.owning_department, 0) + 1
        )
        status = camera.health_status
        if status == CameraStatus.ONLINE.value:
            entry.online += 1
        elif status == CameraStatus.DEGRADED.value:
            entry.degraded += 1
        elif status == CameraStatus.OFFLINE.value:
            entry.offline += 1
        elif status == CameraStatus.UNAVAILABLE.value:
            entry.unavailable += 1

    for row in coverage.values():
        # A district is "thin" if its ACTIVE (non-withdrawn) count is below the
        # threshold - a district full of retired cameras is a gap, not coverage.
        active = row.cameras - row.unavailable
        row.is_thin = active < min_cameras_per_district

    ageing = [
        AgeingCamera(
            camera_id=camera.camera_id,
            name=camera.name,
            owning_department=camera.owning_department,
            district=camera.district,
            installation_date=camera.installation_date,
            age_years=_age_years(camera.installation_date),
            health_status=camera.health_status,
        )
        for camera in cameras
        if camera.installation_date and _age_years(camera.installation_date) is not None
        and (_age_years(camera.installation_date) or 0) >= ageing_years
    ]
    ageing.sort(key=lambda item: item.age_years or 0, reverse=True)

    # Departments that Vigentra knows about but that have no live cameras in
    # the registry - a stronger signal than "no rows for this district".
    known_departments = {source.department for source in sources}
    covered_departments = {camera.owning_department for camera in cameras}
    departments_without_coverage = sorted(known_departments - covered_departments)

    districts_sorted = sorted(coverage.values(), key=lambda item: item.cameras, reverse=True)
    thin_districts = [row for row in districts_sorted if row.is_thin]

    return GapAnalysisResponse(
        generated_at=datetime.now(timezone.utc),
        thresholds={
            "min_cameras_per_district": min_cameras_per_district,
            "ageing_years": ageing_years,
        },
        totals={
            "cameras": len(cameras),
            "active_cameras": sum(1 for c in cameras if c.health_status not in (CameraStatus.UNAVAILABLE.value,)),
            "districts_covered": len(coverage),
            "thin_districts": len(thin_districts),
            "ageing_cameras": len(ageing),
        },
        districts_covered=districts_sorted,
        thin_districts=thin_districts,
        ageing_cameras=ageing,
        departments_without_coverage=departments_without_coverage,
    )
