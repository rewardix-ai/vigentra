"""Shared fixtures.

Every test runs against the real central API, the real Traffic mock and the
real Municipal mock, wired together in-process. `httpx.ASGITransport` routes
adapter and media traffic to the mocks without touching the network. Storage is
SQLite so no database container is required.

Two packaging notes:

  * central-api and edge-worker both use the package name `app`, so the edge
    modules are loaded by file path rather than being put on `sys.path` — the
    two would shadow each other otherwise.
  * the mocks keep module-level state between tests in a session, so anything
    that commissions a camera uses a unique external ID.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
CENTRAL = ROOT / "services" / "central-api"
EDGE = ROOT / "services" / "edge-worker" / "app"
TRAFFIC = ROOT / "services" / "traffic-vms" / "app" / "main.py"
MUNICIPAL = ROOT / "services" / "municipal-vms" / "app" / "main.py"

VIDEO_TRAFFIC = ROOT / "data" / "videos" / "traffic"
VIDEO_MUNICIPAL = ROOT / "data" / "videos" / "municipal"


def _load(module_name: str, path: Path):
    """Import a module by file path, bypassing sys.path entirely."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session", autouse=True)
def _env() -> Iterator[None]:
    tmp = Path(tempfile.mkdtemp(prefix="sentinel-test-"))
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp / 'sentinel.sqlite').as_posix()}")
    os.environ.setdefault("AUTO_SYNC_ON_STARTUP", "false")
    os.environ.setdefault("HEALTH_MONITOR_ENABLED", "false")
    os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-for-hs256")
    # The two mock departments are OFF in a real deployment - the platform must
    # not invent cameras - but the suite is built on them: they are what makes
    # the federation, the cross-unit grant flow and the media proxy testable
    # in-process. Turned on here explicitly rather than relied on by default.
    os.environ.setdefault("TRAFFIC_VMS_ENABLED", "true")
    os.environ.setdefault("MUNICIPAL_VMS_ENABLED", "true")
    # ...and the live Sentinel grid is OFF, so no test reaches the public
    # internet or depends on a third-party sandbox being up.
    os.environ.setdefault("SENTINEL_GRID_ENABLED", "false")
    # ...which also means each department speaks through its OWN adapter here,
    # not the grid one. In deployment both departments federate the shared
    # gateway; the suite drives the bundled mocks in-process instead, and those
    # answer the department endpoints. Without this the grid adapter asks the
    # mocks for /api/ingest, every sync returns nothing, and each fixture that
    # expects cameras yields nothing at all.
    os.environ.setdefault("TRAFFIC_VMS_ADAPTER", "traffic_adapter")
    os.environ.setdefault("MUNICIPAL_VMS_ADAPTER", "municipal_adapter")
    # Point the mocks at the real bundled clips so the media proxy has bytes.
    os.environ.setdefault("TRAFFIC_VIDEO_DIR", str(VIDEO_TRAFFIC))
    os.environ.setdefault("MUNICIPAL_VIDEO_DIR", str(VIDEO_MUNICIPAL))
    os.environ.setdefault("SENTINEL_MEDIA_ROOT", str(VIDEO_TRAFFIC))
    os.environ.setdefault(
        "VEHICLE_REGISTRY_PATH", str(ROOT / "data" / "reference" / "vehicle_registry.json")
    )
    sys.path.insert(0, str(CENTRAL))
    yield


# ---------------------------------------------------------------------------
# Edge-worker modules (loaded by path - see module docstring)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def detectors():
    return _load("edge_detectors", EDGE / "detectors.py")


@pytest.fixture(scope="session")
def frame_quality():
    return _load("edge_frame_quality", EDGE / "frame_quality.py")


# ---------------------------------------------------------------------------
# Department mocks
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def traffic_app():
    return _load("_traffic_vms_mock", TRAFFIC).app


@pytest.fixture(scope="session")
def municipal_app():
    return _load("_municipal_vms_mock", MUNICIPAL).app


class _Router(httpx.AsyncBaseTransport):
    """Route by hostname to the right in-process mock.

    The media proxy follows department-issued URLs like
    `http://traffic-vms:8001/...`, so those hostnames have to resolve to the
    mock apps rather than to DNS.
    """

    def __init__(self, routes: dict[str, httpx.AsyncBaseTransport]) -> None:
        self.routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self.routes.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError(f"no route to {request.url.host}", request=request)
        return await transport.handle_async_request(request)


@pytest_asyncio.fixture
async def clients(traffic_app, municipal_app) -> AsyncIterator[dict[str, httpx.AsyncClient]]:
    """Adapter-side clients that reach the mocks in-process."""
    made = {
        "traffic_vms": httpx.AsyncClient(
            transport=httpx.ASGITransport(app=traffic_app), base_url="http://traffic-vms:8001"
        ),
        "municipal_vms": httpx.AsyncClient(
            transport=httpx.ASGITransport(app=municipal_app), base_url="http://municipal-vms:8002"
        ),
    }
    try:
        yield made
    finally:
        for client in made.values():
            await client.aclose()


@pytest_asyncio.fixture
async def api(clients, traffic_app, municipal_app) -> AsyncIterator[httpx.AsyncClient]:
    """A fully wired central API with both departments synced.

    Fresh SQLite per test so registry state cannot leak between cases.
    """
    tmp = Path(tempfile.mkdtemp(prefix="sentinel-test-run-"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(tmp / 'sentinel.sqlite').as_posix()}"

    from app.adapters import build_adapters, close_adapters
    from app.config import get_settings
    from app.database import dispose_engine, get_session_factory, init_models
    from app.main import _seed_identity, app as central_app
    from app.services import sync_service

    get_settings.cache_clear()
    settings = get_settings()

    await init_models()
    await _seed_identity(settings)

    central_app.state.adapters = build_adapters(settings, clients=clients)
    central_app.state.media_client = httpx.AsyncClient(
        transport=_Router(
            {
                "traffic-vms": httpx.ASGITransport(app=traffic_app),
                "municipal-vms": httpx.ASGITransport(app=municipal_app),
            }
        ),
        follow_redirects=True,
    )
    central_app.state.media_root = str(VIDEO_TRAFFIC)
    central_app.state.monitor_task = None

    from app.services import vehicle_service

    async with get_session_factory()() as db:
        await sync_service.sync_all(
            db, central_app.state.adapters, settings, triggered_by="pytest"
        )
        # Reference data, loaded the same way the app does at boot.
        await vehicle_service.import_from_file(db, settings, force_demo_flag=True)

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=central_app), base_url="http://sentinel-test"
    )
    try:
        yield client
    finally:
        await client.aclose()
        await central_app.state.media_client.aclose()
        await close_adapters(central_app.state.adapters)
        await dispose_engine()


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

async def token_for(api: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await api.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


#: username -> password, for the accounts the suite drives.
ACCOUNTS = {
    "state.admin": "State@2026",
    "registry.viewer": "Registry@2026",
    "health.monitor": "Health@2026",
    "auditor": "Auditor@2026",
    "vehicle.registry": "Vehicle@2026",
    "system.admin": "SysAdmin@2026",
    "ahmedabad.cityadmin": "City@2026",
    "traffic.operator": "Traffic@2026",
    "traffic.zone3": "Traffic@2026",
    "municipal.operator": "Municipal@2026",
    "traffic.state": "Traffic@2026",
    "municipal.state": "Municipal@2026",
    "dept.admin": "DeptAdmin@2026",
    "municipal.deptadmin": "DeptAdmin@2026",
    "traffic.ai": "AiOps@2026",
    # Statewide analytics accounts - the ones the watchlist suite ingests
    # through, because a department-scoped edge account cannot submit for
    # cameras in another district.
    "traffic.ai": "AiOps@2026",
    "municipal.ai": "MuniOps@2026",
    "traffic.installer": "Install@2026",
    "municipal.installer": "Install@2026",
}



def password_for_headers(headers: dict[str, str]) -> str:
    """The demo password of whoever holds this bearer token.

    Opening a camera requires the operator to re-enter their password, so a
    test that opens a session has to supply it. Reading the username back out
    of the token keeps every existing `open_session(api, headers, ...)` call
    site working unchanged.
    """
    import jwt

    token = headers["Authorization"].split(" ", 1)[1]
    username = jwt.decode(token, options={"verify_signature": False})["sub"]
    return ACCOUNTS[username]


async def open_video_session(api, headers, camera_id: str, **overrides):
    """POST a video session with the step-up password already filled in."""
    payload = {
        "camera_id": camera_id,
        "mode": "live",
        "reason": "Routine department monitoring",
        "password": password_for_headers(headers),
        **overrides,
    }
    return await api.post("/api/v1/video-sessions", headers=headers, json=payload)


@pytest_asyncio.fixture
async def login(api):
    """`await login("traffic.operator")` -> auth headers."""

    async def _login(username: str, password: str | None = None) -> dict[str, str]:
        return await token_for(api, username, password or ACCOUNTS[username])

    return _login


@pytest_asyncio.fixture
async def admin_headers(login) -> dict[str, str]:
    return await login("system.admin")


@pytest_asyncio.fixture
async def traffic_installer_headers(login) -> dict[str, str]:
    return await login("traffic.installer")


@pytest_asyncio.fixture
async def traffic_approver_headers(login) -> dict[str, str]:
    """Whoever decides Traffic Police video requests.

    There is no separate approver role: `video:grant` sits with the operators
    who run the unit's cameras, so the operator IS the approver.
    """
    return await login("traffic.state")


@pytest_asyncio.fixture
async def municipal_admin_headers(login) -> dict[str, str]:
    """A Municipal account that reads its own unit's records at full depth."""
    return await login("municipal.deptadmin")


@pytest_asyncio.fixture
async def municipal_approver_headers(login) -> dict[str, str]:
    """Whoever decides Municipal Corporation video requests."""
    return await login("municipal.state")


@pytest_asyncio.fixture
async def health_monitor_headers(login) -> dict[str, str]:
    return await login("health.monitor")


@pytest_asyncio.fixture
async def cameras(api, admin_headers) -> list[dict]:
    """Every camera in the registry, as the system admin sees it."""
    response = await api.get("/api/v1/cameras", headers=admin_headers)
    response.raise_for_status()
    return response.json()["items"]


@pytest_asyncio.fixture
async def traffic_camera(cameras) -> dict:
    """A commissioned, video-enabled Traffic camera."""
    return next(
        camera
        for camera in cameras
        if camera["source_system"] == "traffic_vms"
        and camera["installation"]["installation_status"] == "COMMISSIONED"
        and camera["access_policy_summary"]["sentinel_video_access"]
    )


@pytest_asyncio.fixture
async def municipal_camera(cameras) -> dict:
    return next(
        camera
        for camera in cameras
        if camera["source_system"] == "municipal_vms"
        and camera["installation"]["installation_status"] == "COMMISSIONED"
        and camera["access_policy_summary"]["sentinel_video_access"]
    )


# ---------------------------------------------------------------------------
# Installation form
# ---------------------------------------------------------------------------

_COUNTER = [0]


def unique_external_id(prefix: str = "TRF-AHM-T") -> str:
    """Distinct camera ID per record.

    Session-scoped mocks keep state between tests, so reusing an external ID
    lets an earlier test's commissioned camera fail a later test's validation.
    """
    _COUNTER[0] += 1
    return f"{prefix}{_COUNTER[0]:04d}"


def demo_form(**overrides) -> dict:
    """A valid Traffic Police installation form."""
    form = {
        "camera_name": "Sarkhej Circle West",
        "external_camera_id": unique_external_id(),
        "camera_serial_number": "GTPX7712345678",
        "camera_vendor": "Demo Vendor",
        "camera_model": "Demo IP Camera",
        "camera_type": "fixed",
        "installation_purpose": "junction monitoring",
        "owning_department": "Traffic Police",
        "owning_unit": "Ahmedabad Traffic Zone 1",
        "district": "Ahmedabad",
        "police_station_or_zone": "Sarkhej PS",
        "local_admin_contact": "+91 79 2650 9999",
        "maintenance_agency": "Gujarat Infotech Services",
        "installation_vendor": None,
        "latitude": 22.9955,
        "longitude": 72.5012,
        "address_or_landmark": "Sarkhej Circle, west arm",
        "road_or_junction": "Sarkhej Circle",
        "view_direction": "westbound",
        "coverage_description": "Circle west approach",
        "entry_exit_zone_description": None,
        "source_type": "RTSP",
        "vms_name": "Traffic VMS Demo",
        "vms_vendor": "GTP-VMS",
        "resolution": "1920x1080",
        "fps": 25,
        "codec": "H.264",
        "supports_live": True,
        "supports_playback": True,
        "retention_days": 30,
        "timezone": "Asia/Kolkata",
        "installation_date": "2026-08-19",
        "commissioning_date": None,
        "permitted_local_roles": ["department_operator", "investigator"],
        "attachments": [
            {"document_type": "site_survey", "reference": "DOC-TRF-SS-9999", "filename": "s.pdf"}
        ],
    }
    form.update(overrides)
    return form


# ---------------------------------------------------------------------------
# Secret scanning
# ---------------------------------------------------------------------------

import re as _re

#: Anything here appearing in a client-facing payload is a leak. Covers vendor
#: credentials, media tickets, stream URLs and internal service addresses.
SECRET_PATTERNS = [
    _re.compile(r"rtsp://", _re.IGNORECASE),
    _re.compile(r"password", _re.IGNORECASE),
    _re.compile(r"api[_-]?key", _re.IGNORECASE),
    _re.compile(r"bearer\s", _re.IGNORECASE),
    _re.compile(r"traffic-demo-key", _re.IGNORECASE),
    _re.compile(r"municipal-demo-token", _re.IGNORECASE),
    _re.compile(r"playback_url", _re.IGNORECASE),
    _re.compile(r"ticket", _re.IGNORECASE),
    _re.compile(r"traffic-vms:\d+", _re.IGNORECASE),
    _re.compile(r"municipal-vms:\d+", _re.IGNORECASE),
]


def assert_no_secrets(blob: str, context: str) -> None:
    """Fail if a client-facing payload carries anything it must not."""
    hits = [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(blob)]
    assert not hits, f"{context} leaked: {hits}"
