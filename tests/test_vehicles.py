"""Vehicle reference registry tests.

Two jobs. First, the registry works: import, lookup, search, facets, audit.

Second, and more important, it stays a *reference table*. The tests at the
bottom assert the separation that keeps this safe — no camera endpoint returns
a registration number, no vehicle endpoint accepts a camera ID, and the
importer refuses any export carrying owner data.
"""
from __future__ import annotations

import json

import pytest

from conftest import password_for_headers

pytestmark = pytest.mark.asyncio

REGISTRY_FILE = "data/reference/vehicle_registry.json"


@pytest.fixture
async def vehicles_loaded(api, admin_headers):
    """The startup import runs against the real file; confirm it landed."""
    response = await api.get("/api/v1/vehicles?limit=500", headers=admin_headers)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

async def test_registry_imports_from_file(vehicles_loaded):
    assert vehicles_loaded["total"] >= 40
    plates = {item["registration_number"] for item in vehicles_loaded["items"]}
    assert "MH05EF3195" in plates

    for item in vehicles_loaded["items"]:
        # Confirmed synthetic, so stored labelled as demo data regardless of
        # the flag the source file carried.
        assert item["is_demo_data"] is True
        assert item["source"] == "sanitized_vehicle_registry"


async def test_import_is_idempotent(api, admin_headers):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.services import vehicle_service

    settings = get_settings()
    async with get_session_factory()() as db:
        first = await vehicle_service.import_from_file(
            db, settings, REGISTRY_FILE, force_demo_flag=True
        )
        second = await vehicle_service.import_from_file(
            db, settings, REGISTRY_FILE, force_demo_flag=True
        )

    # Startup already imported them, so both runs update rather than insert.
    assert second.imported == 0
    assert second.updated >= 40
    assert not second.errors


async def test_import_refuses_owner_data(tmp_path):
    """A future export quietly including owner data must fail loudly.

    Stripping it silently would hide the fact that someone exported personal
    data in the first place - which is the thing worth knowing about.
    """
    from app.config import get_settings
    from app.database import get_session_factory
    from app.services import vehicle_service
    from app.services.vehicle_service import OwnerDataRejected

    tainted = tmp_path / "tainted.json"
    tainted.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source": "test",
                "is_demo_data": True,
                "vehicles": [
                    {
                        "registration_number": "GJ01AA0001",
                        "make": "TEST",
                        "owner_name": "A. Person",
                        "address": "12 Somewhere Road",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async with get_session_factory()() as db:
        with pytest.raises(OwnerDataRejected) as excinfo:
            await vehicle_service.import_from_file(db, get_settings(), tainted)

    message = str(excinfo.value)
    assert "owner_name" in message and "address" in message
    assert "vehicle attributes only" in message


@pytest.mark.asyncio
async def test_plate_normalisation():
    from app.services.vehicle_service import normalize_plate

    for written in ("MH05EF3195", "mh05ef3195", "MH 05 EF 3195", "mh-05-ef-3195"):
        assert normalize_plate(written) == "MH05EF3195"
    assert normalize_plate(None) == ""


# ---------------------------------------------------------------------------
# Lookup and search
# ---------------------------------------------------------------------------

async def test_lookup_by_registration_number(api, admin_headers):
    response = await api.get("/api/v1/vehicles/MH05EF3195", headers=admin_headers)
    assert response.status_code == 200

    vehicle = response.json()
    assert vehicle["make"].startswith("SUZUKI")
    assert vehicle["vehicle_class"] == "M-Cycle/Scooter"
    assert vehicle["colour"] == "PEARL MIRAGE WHITE"
    assert vehicle["registration_status"] == "ACTIVE"


async def test_lookup_ignores_spacing(api, admin_headers):
    spaced = await api.get("/api/v1/vehicles/mh%2005%20ef%203195", headers=admin_headers)
    assert spaced.status_code == 200
    assert spaced.json()["registration_number"] == "MH05EF3195"


async def test_unknown_plate_returns_404_and_says_why(api, admin_headers):
    response = await api.get("/api/v1/vehicles/XX00XX0000", headers=admin_headers)
    assert response.status_code == 404
    # The message makes clear this is a static table, not a live VAHAN query.
    assert "static reference table" in response.json()["detail"]


async def test_search_and_facets(api, admin_headers):
    by_make = (
        await api.get("/api/v1/vehicles?make=SUZUKI", headers=admin_headers)
    ).json()
    assert by_make["total"] >= 1
    assert all("SUZUKI" in item["make"].upper() for item in by_make["items"])
    assert by_make["filters"]["make"] == "SUZUKI"

    by_status = (
        await api.get(
            "/api/v1/vehicles?registration_status=FITNESS%20EXPIRED", headers=admin_headers
        )
    ).json()
    assert all(
        item["registration_status"] == "FITNESS EXPIRED" for item in by_status["items"]
    )

    facets = (await api.get("/api/v1/vehicles/facets", headers=admin_headers)).json()
    assert "M-Cycle/Scooter" in facets["classes"]
    assert "ACTIVE" in facets["statuses"]
    assert facets["makes"]


# ---------------------------------------------------------------------------
# Permission and audit
# ---------------------------------------------------------------------------

async def test_registry_requires_its_own_permission(api, login):
    """Reading a vehicle register is a deliberate grant, not a side effect.

    A traffic operator can watch a camera but cannot look up a plate; the
    vehicle registry desk can look up a plate but cannot watch anything.
    """
    denied = await login("traffic.operator")
    assert (await api.get("/api/v1/vehicles", headers=denied)).status_code == 403

    allowed = await login("vehicle.registry")
    assert (await api.get("/api/v1/vehicles", headers=allowed)).status_code == 200


async def test_registry_viewer_cannot_reach_cameras_or_video(api, login, traffic_camera):
    """The narrow role is genuinely narrow."""
    headers = await login("vehicle.registry")

    assert (await api.get("/api/v1/cameras", headers=headers)).status_code == 403
    assert (await api.get("/api/v1/audit", headers=headers)).status_code == 403

    session = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={
            "camera_id": traffic_camera["camera_id"],
            "mode": "live",
            "reason": "Should never be permitted",
            # Supplied so the refusal below is a permission decision and not a
            # body-validation error that would pass for the wrong reason.
            "password": password_for_headers(headers),
        },
    )
    assert session.status_code == 403


async def test_lookups_are_audited(api, login, admin_headers):
    headers = await login("vehicle.registry")
    await api.get("/api/v1/vehicles/MH05EF3195", headers=headers)
    await api.get("/api/v1/vehicles?q=SUZUKI", headers=headers)

    viewed = (
        await api.get("/api/v1/audit?action=vehicle_record_viewed", headers=admin_headers)
    ).json()
    assert viewed
    assert viewed[0]["username"] == "vehicle.registry"
    assert viewed[0]["resource_id"] == "MH05EF3195"

    searched = (
        await api.get("/api/v1/audit?action=vehicle_registry_searched", headers=admin_headers)
    ).json()
    assert searched
    # The query itself is recorded, not merely the fact of a request.
    assert searched[0]["details"]["query"] == "SUZUKI"


# ---------------------------------------------------------------------------
# The separation that keeps this safe
# ---------------------------------------------------------------------------

async def test_camera_records_never_carry_a_registration_number(api, admin_headers, cameras):
    """No camera-side payload may reference a vehicle.

    This is the guarantee that keeps the registry a reference table rather than
    a sightings database.
    """
    import re

    plate_pattern = re.compile(r"\b[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{3,4}\b")

    for camera in cameras:
        for path in ("", "/access-policy", "/detections"):
            response = await api.get(
                f"/api/v1/cameras/{camera['camera_id']}{path}", headers=admin_headers
            )
            if response.status_code != 200:
                continue
            blob = json.dumps(response.json())
            assert "registration_number" not in blob
            assert not plate_pattern.search(blob), (
                f"{camera['camera_id']}{path} contains something shaped like a plate"
            )


async def test_detections_never_carry_a_registration_number(
    api, login, traffic_camera, detectors
):
    from datetime import datetime, timezone

    headers = await login("ai.operator")
    detector = detectors.MockDetector()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = [
        d.to_payload(traffic_camera["camera_id"], stamp, i)
        for i, d in enumerate(detector.detect(None))
    ]
    await api.post("/api/v1/detections/ingest", headers=headers, json={"detections": payload})

    stored = (await api.get("/api/v1/detections", headers=headers)).json()
    blob = json.dumps(stored)
    assert "registration_number" not in blob
    # And no detection class is plate-related in this phase.
    assert all(d["class_name"] in detectors.DETECTION_CLASSES for d in stored)


async def test_vehicle_endpoints_accept_no_camera_parameter(api, admin_headers):
    """A camera ID must not be a way into the vehicle registry."""
    from app.main import app

    paths = app.openapi()["paths"]
    vehicle_paths = {p: spec for p, spec in paths.items() if "/vehicles" in p}
    assert vehicle_paths

    for path, spec in vehicle_paths.items():
        assert "camera" not in path.lower()
        for method in spec.values():
            names = {p["name"].lower() for p in method.get("parameters", [])}
            assert not any("camera" in name for name in names), (
                f"{path} exposes a camera-shaped parameter"
            )
