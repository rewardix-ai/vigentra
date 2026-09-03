"""Platform health.

Unauthenticated on purpose: the Compose healthcheck and the sign-in page both
need it, and it exposes no camera data - only whether Vigentra and each
department system are reachable.

Vigentra reports `ok` while it can serve its own registry. A dead department
system makes the platform `degraded`, never `down`: that is the point of
federating rather than consolidating.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import AdaptersDep, SettingsDep
from ..schemas import DependencyHealth, HealthResponse

router = APIRouter(tags=["system"])


async def _database_health(db: AsyncSession) -> DependencyHealth:
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        return DependencyHealth(name="database", status="down", detail=str(exc)[:200])
    return DependencyHealth(
        name="database", status="up", latency_ms=round((time.perf_counter() - started) * 1000, 1)
    )


@router.get("/health", response_model=HealthResponse, summary="Platform and dependency health")
async def health(
    settings: SettingsDep,
    adapters: AdaptersDep,
    db: AsyncSession = Depends(get_db),
    deep: bool = Query(default=True, description="Also probe each department system"),
) -> HealthResponse:
    dependencies = [await _database_health(db)]

    if deep:
        probes = await asyncio.gather(
            *(adapter.check_source_health() for adapter in adapters.values()),
            return_exceptions=True,
        )
        for (name, _), probe in zip(adapters.items(), probes):
            if isinstance(probe, BaseException):
                dependencies.append(DependencyHealth(name=name, status="down", detail=str(probe)[:200]))
            elif probe["reachable"]:
                dependencies.append(
                    DependencyHealth(name=name, status="up", latency_ms=probe.get("latency_ms"))
                )
            else:
                dependencies.append(
                    DependencyHealth(
                        name=name,
                        status="down",
                        detail=str(probe.get("error"))[:200],
                        latency_ms=probe.get("latency_ms"),
                    )
                )

    database_up = dependencies[0].status == "up"
    any_source_down = any(dep.status != "up" for dep in dependencies[1:])
    overall = "down" if not database_up else ("degraded" if any_source_down else "ok")

    return HealthResponse(
        status=overall,
        service=settings.service_name,
        version=settings.service_version,
        environment=settings.environment_label,
        time_utc=datetime.now(timezone.utc),
        dependencies=dependencies,
    )
