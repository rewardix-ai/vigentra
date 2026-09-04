"""Vigentra Central API - metadata-only CCTV registry federation (Module 1).

Scope: federate the CCTV ASSET REGISTERS of independent departments behind one
canonical schema, one approval-gated onboarding pipeline, one permission model,
one health view and one audit trail.

Video is brokered, never republished: a caller with an explicit grant from the
owning department gets a short-lived, watermarked, audited session against an
opaque stream id. Upstream RTSP/NVR credentials and source URLs never leave
this service. See routers/video_sessions.py and services/video_broker.py.

Computer vision runs at the *edge*, not here. This service has no CV
dependency; it accepts detections (and, for callers holding `plate:read`,
read plate characters) over the ingest API and applies retention and
field-level redaction on the way back out.

Plate identity IS in scope, as of the watchlist phase: an ingested plate is
recorded as a sighting, matched against the active watchlist, and can be
assembled into a cross-camera movement history. That was a deliberate reversal
of the earlier position, taken because the platform is answering a policing
problem that requires it. The controls that came with the reversal - a
permission per act, mandatory reasons, shorter retention, and an audit line on
every disclosure - are documented in docs/access-model.md and are not optional.

Explicitly NOT in this service:
  * face recognition, biometric identification, gait recognition.
  * automatic enforcement actions - the platform alerts a human, and stops.
  * live VAHAN or Dharmik integration. The vehicle reference data here is a
    local synthetic file for demo lookups and is not an authoritative
    registration source.
  * make-model-colour recognition, and vehicle re-identification by appearance.
    A track is built from plate reads alone, so a vehicle whose plate was not
    read contributes nothing to it.
  * any join between a sighting and the vehicle reference registry.

See docs/access-model.md.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

import os

import httpx

from .adapters import build_adapters, close_adapters
from .providers import build_provider
from .adapters.base import AdapterError
from .config import GRID_BROWSER_UA, ROLE_PERMISSIONS, ROLE_VISIBILITY, Settings, get_settings
from .database import dispose_engine, get_session_factory, init_models
from .models import Role as RoleRow, User as UserRow
from .routers import (
    audit,
    auth,
    cameras,
    detections,
    directory,
    events,
    health,
    installation_requests,
    overview,
    reports,
    sources,
    vehicles,
    video_grants,
    video_sessions,
    watchlist,
)
from .services import health_monitor, sync_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
)
logger = logging.getLogger("vigentra.api")


async def _seed_identity(settings: Settings) -> None:
    """Materialise the configured roles and accounts into the registry tables.

    Passwords are deliberately NOT stored - authentication reads them from
    configuration. These tables exist so roles, departments and permissions are
    queryable data rather than only code.
    """
    async with get_session_factory()() as db:
        for role_id, permissions in ROLE_PERMISSIONS.items():
            row = (
                await db.execute(select(RoleRow).where(RoleRow.role_id == role_id))
            ).scalar_one_or_none()
            if row is None:
                row = RoleRow(role_id=role_id)
                db.add(row)
            row.permissions = sorted(permissions)
            row.metadata_visibility_level = ROLE_VISIBILITY.get(role_id, "limited")
            # No role grants video access in Module 1. Recorded, not assumed.
            row.grants_video_access = False

        for user in settings.demo_users:
            row = (
                await db.execute(select(UserRow).where(UserRow.username == user.username))
            ).scalar_one_or_none()
            if row is None:
                row = UserRow(username=user.username)
                db.add(row)
            row.display_name = user.display_name
            row.role_id = user.role
            row.department = user.department
            row.unit = user.unit
            row.active = True

        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    await init_models()
    await _seed_identity(settings)

    # One shared client for the grid, handed to BOTH department adapters.
    #
    # The gateway allows a single session per account and both departments use
    # the one grid credential, so a client each meant two logins that
    # invalidated one another. Sharing the client means one login, one cookie
    # jar and one coordinated re-auth - see adapters/grid_adapter._GridGate.
    grid_client: httpx.AsyncClient | None = None
    grid_sources = {
        s.source_system for s in settings.sources if s.adapter == "grid_adapter"
    }
    if grid_sources:
        grid_client = httpx.AsyncClient(
            base_url=settings.sentinel_grid_base_url,
            # One budget for both roles this client now serves: a cold gateway
            # connection (tens of seconds) and a video segment read. Connect is
            # bounded to the grid budget; the read is generous for streaming.
            timeout=httpx.Timeout(30.0, connect=settings.grid_upstream_timeout_seconds),
            follow_redirects=True,
            # The gateway gates its video endpoints on a browser User-Agent -
            # a default client UA is answered 403 on the playlist and segments
            # while the catalogue is served freely. So every request on this
            # client, including the video adapter's HLS availability probe and
            # every proxied segment, carries one. It must NOT carry Referer or
            # Origin: the gateway 403s a video request that does, which is why
            # the proxy builds each upstream request from scratch rather than
            # forwarding the browser's headers.
            headers={"User-Agent": GRID_BROWSER_UA},
        )
    app.state.grid_client = grid_client
    app.state.adapters = build_adapters(
        settings,
        clients={name: grid_client for name in grid_sources} if grid_client else None,
    )

    # The media proxy uses the SAME grid client, not a second one.
    #
    # The gateway allows one session per account, and the cookie jar cannot be
    # shared between two httpx clients (a second client constructed from the
    # first's cookies gets an empty jar in this version). A separate media
    # client would therefore have to sign in again - a second session that
    # invalidates the first. Reusing the one authenticated client is what keeps
    # the catalogue, the HLS probe and the proxied segments all on the single
    # session the gateway permits. A non-grid deployment still gets its own
    # media client below.
    if grid_client is not None:
        app.state.media_client = grid_client
    else:
        app.state.media_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=settings.upstream_timeout_seconds),
            follow_redirects=True,
        )
    # Pay the grid's cold-connection cost here rather than on the first
    # operator request. Sequential on purpose: both department adapters point
    # at the same gateway, and warming them in parallel means two sign-ins
    # racing for one session.
    for adapter in app.state.adapters.values():
        warm = getattr(adapter, "warm", None)
        if warm is not None:
            await warm()

    app.state.media_root = os.getenv("VIGENTRA_MEDIA_ROOT", "/app/videos")
    app.state.provider = build_provider(settings, adapters=app.state.adapters)
    app.state.monitor_task = None

    logger.info(
        "resource provider: %s | video_enabled=%s | yolo_enable=%s",
        app.state.provider.describe(), settings.video_enabled, settings.yolo_enable,
    )

    if settings.auto_sync_on_startup:
        # Best-effort: a department system that is down at boot must not stop
        # the API from serving the rest of the register.
        try:
            async with get_session_factory()() as db:
                result = await sync_service.sync_all(
                    db, app.state.adapters, settings, triggered_by="startup"
                )
            logger.info(
                "startup metadata sync: %s cameras across %s departments",
                result.total_cameras, len(result.sources),
            )
        except Exception:
            logger.exception("startup sync failed - continuing without it")

    if settings.vehicle_registry_enabled:
        # Reference data, loaded once at boot. Failure here must not stop the
        # platform - the registry is a lookup aid, not a dependency.
        try:
            from .services import vehicle_service

            async with get_session_factory()() as db:
                outcome = await vehicle_service.import_from_file(
                    db,
                    settings,
                    # The supplied export is flagged is_demo_data=false, but it
                    # was confirmed as synthetic, so it is stored labelled as
                    # demo data like everything else in this build.
                    force_demo_flag=True,
                )
            logger.info("vehicle reference registry: %s", outcome.as_dict())
        except Exception:
            logger.exception("vehicle registry import failed - continuing without it")

    if settings.health_monitor_enabled:
        app.state.monitor_task = asyncio.create_task(
            health_monitor.monitor_loop(get_session_factory(), app.state.adapters, settings)
        )

    try:
        yield
    finally:
        if app.state.monitor_task is not None:
            app.state.monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await app.state.monitor_task
        provider = getattr(app.state, "provider", None)
        if provider is not None:
            await provider.aclose()
        await close_adapters(app.state.adapters)
        # The grid client is shared by the adapters and doubles as the media
        # client, so neither the adapters nor the media path own it; close it
        # once here. A non-grid deployment has a distinct media client to close.
        grid_client = getattr(app.state, "grid_client", None)
        media_client = getattr(app.state, "media_client", None)
        if media_client is not None and media_client is not grid_client:
            await media_client.aclose()
        if grid_client is not None:
            await grid_client.aclose()
        await dispose_engine()


settings = get_settings()

app = FastAPI(
    title="Vigentra Central API",
    version=settings.service_version,
    description=(
        "Vendor-neutral federated CCTV **metadata** registry. Module 1 covers "
        "installation onboarding, departmental approval, metadata "
        "synchronisation, access policy, health and audit. "
        "**Video access is not available from this system** - footage remains "
        "with the department that owns the camera. All data is synthetic."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Vigentra-Degraded"],
)


@app.middleware("http")
async def stamp_access_model(request: Request, call_next):
    """Advertise the access model on every response.

    Cheap, and it means an integrator cannot mistake this service for a video
    gateway even if they never read the documentation.
    """
    response = await call_next(request)
    response.headers["X-Vigentra-Access-Model"] = "AUTHORIZED_VIDEO"
    response.headers["X-Vigentra-Video-Access"] = "ROLE_AND_SCOPE_GATED"
    return response


@app.exception_handler(AdapterError)
async def adapter_error_handler(_: Request, exc: AdapterError) -> JSONResponse:
    """Turn a department-system failure into a useful central error.

    The client learns which department failed and why, in Vigentra's vocabulary -
    never the vendor's raw envelope, and never a credential.
    """
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.to_dict()})


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(installation_requests.router)
app.include_router(directory.router)
app.include_router(cameras.router)
app.include_router(overview.router)
app.include_router(events.router)
app.include_router(detections.router)
app.include_router(reports.router)
app.include_router(vehicles.router)
app.include_router(audit.router)
app.include_router(video_grants.router)
app.include_router(video_sessions.router)
app.include_router(watchlist.router)


@app.get("/", tags=["system"], summary="Service banner")
async def root() -> dict[str, object]:
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "environment": settings.environment_label,
        "module": "Module 1 - metadata-only CCTV registry federation",
        "access_model": "FEDERATED_METADATA_PLUS_BROKERED_VIDEO",
        "video_access": "BROKERED_ON_GRANT",
        "footage_custody": (
            "Retained by the department that owns the camera; released only "
            "through a short-lived, watermarked, audited, revocable session"
        ),
        "analytics": {
            "object_detection": "ultralytics-yolo, executed at the edge",
            "plate_reading": "ANPR at the edge; characters gated on plate:read",
            "vehicle_reference_data": "local synthetic file, NOT VAHAN",
        },
        "federated_departments": [
            {"source_system": s.source_system, "department": s.department}
            for s in settings.sources
        ],
        "docs": "/docs",
        "data_classification": "SYNTHETIC_DEMO",
        "never_exposed": [
            "RTSP / NVR / VMS credentials",
            "camera source URLs, private IPs, permanent public links",
        ],
        "analytics_in_scope": [
            "generic object detection (person / vehicle class / bicycle)",
            "ANPR - number-plate reading at the edge, behind plate:read",
            "watchlist matching and real-time alerting",
            "cross-camera movement history from plate reads, behind track:read",
        ],
        "excluded": [
            "face recognition / biometric identification",
            "gait recognition",
            "automatic enforcement actions",
            "live VAHAN / Dharmik integration",
            "make-model-colour recognition",
            "vehicle re-identification by appearance",
            "any join between a sighting and the vehicle reference registry",
        ],
    }
