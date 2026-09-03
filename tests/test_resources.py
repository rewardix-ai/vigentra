"""Resource and metadata tests (spec items 1-8).

Covers the provider layer, canonical normalisation, city/department filtering,
idempotency, provenance, and the guarantee that no secret ever appears in a
camera response.
"""
from __future__ import annotations

import json

import pytest

from conftest import assert_no_secrets

pytestmark = pytest.mark.asyncio


# 1. Mock provider imports camera records
async def test_mock_provider_imports_camera_records():
    from app.config import get_settings
    from app.providers.mock import MockCameraResourceProvider

    provider = MockCameraResourceProvider(get_settings())
    records = await provider.list_cameras()

    assert len(records) >= 4
    assert all(record.external_camera_id for record in records)
    assert {r.source_system for r in records} == {"traffic_vms", "municipal_vms"}
    # Every mock record must be labelled synthetic.
    assert all(record.is_demo_data for record in records)


# 2. Camera records normalize correctly
async def test_records_normalize_to_one_canonical_shape(api, admin_headers):
    body = (await api.get("/api/v1/cameras", headers=admin_headers)).json()
    traffic = [c for c in body["items"] if c["source_system"] == "traffic_vms"]
    municipal = [c for c in body["items"] if c["source_system"] == "municipal_vms"]
    assert traffic and municipal

    def shape(record: dict) -> set[str]:
        return set(record) | {f"location.{k}" for k in record["location"]}

    # Two completely different vendor dialects, one identical output shape.
    assert shape(traffic[0]) == shape(municipal[0])

    for camera in body["items"]:
        assert camera["location"]["city"], "every camera must carry a city"
        assert camera["department_code"] in {"TRAFFIC", "MUNICIPAL"}
        assert "metadata" in camera["capabilities"]


# 3. Cameras filter by city
async def test_filter_by_city(api, admin_headers):
    body = (await api.get("/api/v1/cameras?city=Ahmedabad", headers=admin_headers)).json()
    assert body["total"] >= 1
    assert body["filters"]["city"] == "Ahmedabad"
    assert all(c["location"]["city"] == "Ahmedabad" for c in body["items"])

    # Case-insensitive: the query hits the normalised column.
    lowered = (await api.get("/api/v1/cameras?city=ahmedabad", headers=admin_headers)).json()
    assert lowered["total"] == body["total"]

    empty = (await api.get("/api/v1/cameras?city=Nowhere", headers=admin_headers)).json()
    assert empty["total"] == 0 and empty["items"] == []


# 4. Cameras filter by department
async def test_filter_by_department(api, admin_headers):
    body = (
        await api.get("/api/v1/cameras?department=Traffic%20Police", headers=admin_headers)
    ).json()
    assert body["total"] >= 1
    assert all(c["owning_department"] == "Traffic Police" for c in body["items"])

    by_code = (await api.get("/api/v1/cameras?department_code=TRAFFIC", headers=admin_headers)).json()
    assert by_code["total"] == body["total"]


# 5. Combined city/department filter
async def test_filter_by_city_and_department(api, admin_headers):
    body = (
        await api.get(
            "/api/v1/cameras?city=Ahmedabad&department=Municipal%20Corporation",
            headers=admin_headers,
        )
    ).json()
    assert body["filters"] == {"city": "Ahmedabad", "department": "Municipal Corporation"}
    assert all(
        c["location"]["city"] == "Ahmedabad"
        and c["owning_department"] == "Municipal Corporation"
        for c in body["items"]
    )


async def test_city_and_department_directories(api, admin_headers):
    cities = (await api.get("/api/v1/cities", headers=admin_headers)).json()
    assert cities and all(city["camera_count"] > 0 for city in cities)
    assert any(city["city"] == "Ahmedabad" for city in cities)

    departments = (await api.get("/api/v1/departments", headers=admin_headers)).json()
    names = {d["department"] for d in departments}
    assert {"Traffic Police", "Municipal Corporation"} <= names
    for department in departments:
        assert department["department_code"] in {"TRAFFIC", "MUNICIPAL"}
        assert department["video_capable_cameras"] <= department["camera_count"]


# 6. Duplicate imports are idempotent
async def test_repeat_sync_is_idempotent(api, admin_headers):
    before = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["total"]

    first = (await api.post("/api/v1/sources/sync", headers=admin_headers)).json()
    second = (await api.post("/api/v1/sources/sync", headers=admin_headers)).json()

    after = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["total"]
    assert after == before, "re-syncing must not duplicate cameras"

    # A second run updates rather than creating.
    for result in second["sources"].values():
        assert result["created"] == 0
    assert first["metadata_only"] is True


# 7. Source provenance is preserved
async def test_source_provenance_is_preserved(api, admin_headers, cameras):
    camera = cameras[0]
    detail = (
        await api.get(f"/api/v1/cameras/{camera['camera_id']}", headers=admin_headers)
    ).json()

    provenance = detail["provenance"]
    assert provenance["adapter"] in {"traffic_adapter", "municipal_adapter"}
    assert provenance["source_system"] == camera["source_system"]
    assert provenance["adapter_version"]

    # The department's own ID survives alongside the canonical one.
    assert detail["external_camera_id"]
    assert detail["camera_id"].startswith("VIGENTRA-")
    assert detail["external_camera_id"] != detail["camera_id"]


# 8. Secrets do not appear in camera responses
async def test_no_secrets_in_camera_responses(api, admin_headers, cameras):
    assert_no_secrets(json.dumps(cameras), "camera list")

    for camera in cameras:
        detail = await api.get(f"/api/v1/cameras/{camera['camera_id']}", headers=admin_headers)
        assert_no_secrets(json.dumps(detail.json()), f"detail {camera['camera_id']}")

        policy = await api.get(
            f"/api/v1/cameras/{camera['camera_id']}/access-policy", headers=admin_headers
        )
        assert_no_secrets(json.dumps(policy.json()), f"policy {camera['camera_id']}")


async def test_source_listing_redacts_endpoints(api, admin_headers):
    sources = (await api.get("/api/v1/sources", headers=admin_headers)).json()
    for source in sources:
        # Host:port only - never a scheme, path or credential.
        assert "://" not in source["endpoint"]
        assert_no_secrets(json.dumps({k: v for k, v in source.items() if k != "endpoint"}),
                          f"source {source['source_system']}")
