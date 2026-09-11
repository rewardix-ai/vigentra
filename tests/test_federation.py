"""Federation guarantees: normalisation, isolation, permission, audit.

Covers requirements #14-20.
"""
from __future__ import annotations

import pytest

from conftest import demo_form, token_for

pytestmark = pytest.mark.asyncio


async def test_traffic_and_municipal_normalise_to_the_same_shape(api, admin_headers):
    await api.post("/api/v1/sources/sync", headers=admin_headers)
    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]

    traffic = [c for c in cameras if c["source_system"] == "traffic_vms"]
    municipal = [c for c in cameras if c["source_system"] == "municipal_vms"]
    assert traffic and municipal

    def keys(record: dict) -> set:
        return set(record.keys()) | {f"location.{key}" for key in record["location"].keys()}

    assert keys(traffic[0]) == keys(municipal[0])


async def test_cross_department_metadata_is_readable_but_shallower(
    api, admin_headers, traffic_installer_headers, municipal_admin_headers
):
    """The registry is federation-wide; the depth is not.

    This replaces an earlier assertion that a Traffic account got a 403 on a
    Municipal record. That made the central registry useless for the case it
    exists to serve - you cannot ask a unit for footage from a camera you were
    never allowed to know about. What survives is the narrower and more useful
    guarantee: the record is readable, the owning unit's internal operational
    data is not.
    """
    await api.post("/api/v1/sources/sync", headers=admin_headers)
    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    municipal = next(c for c in cameras if c["source_system"] == "municipal_vms")

    outsider = await api.get(
        f"/api/v1/cameras/{municipal['camera_id']}", headers=traffic_installer_headers
    )
    assert outsider.status_code == 200
    seen = outsider.json()
    assert seen["visibility_level"] == "standard", "capped regardless of role"
    assert seen["installation_vendor"] is None
    assert "installation_vendor" in seen["redacted_fields"]
    # Enough to identify and locate the camera, which is the point.
    assert seen["location"]["latitude"] is not None

    owner = await api.get(
        f"/api/v1/cameras/{municipal['camera_id']}", headers=municipal_admin_headers
    )
    assert owner.status_code == 200
    assert owner.json()["visibility_level"] == "full"
    assert owner.json()["installation_vendor"], "the owning unit sees its own record whole"


async def test_local_roles_are_a_summary_only(api, admin_headers):
    await api.post("/api/v1/sources/sync", headers=admin_headers)
    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    for camera in cameras:
        summary = camera["access_policy_summary"]
        assert isinstance(summary["permitted_local_roles"], list)
        # Now a real owner decision rather than a constant. What must still
        # hold is that it is a bool and never leaks a link or a token.
        assert isinstance(summary["vigentra_video_access"], bool)
        # Any value that looks like a URL or a token would betray the boundary.
        for role in summary["permitted_local_roles"]:
            assert "http" not in role and "token" not in role.lower()


async def test_camera_view_creates_audit_entry(api, admin_headers):
    await api.post("/api/v1/sources/sync", headers=admin_headers)
    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    await api.get(f"/api/v1/cameras/{cameras[0]['camera_id']}", headers=admin_headers)

    audit = (
        await api.get(
            f"/api/v1/audit?action=camera_details_viewed&resource_id={cameras[0]['camera_id']}",
            headers=admin_headers,
        )
    ).json()
    assert audit
    assert audit[0]["username"] == "system.admin"


async def test_metadata_sync_creates_audit_entry(api, admin_headers):
    await api.post("/api/v1/sources/sync", headers=admin_headers)
    audit = (
        await api.get(
            "/api/v1/audit?action=camera_metadata_synchronized", headers=admin_headers
        )
    ).json()
    assert audit
    assert audit[0]["details"]["metadata_only"] is True


async def test_one_source_offline_does_not_break_the_other(api, admin_headers, traffic_app):
    """Break the Traffic mock mid-flight and prove the Municipal source keeps working."""
    from app.adapters import build_adapters
    from app.config import get_settings
    import httpx

    class Broken(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("simulated Traffic outage", request=request)

    from app.main import app as central_app

    settings = get_settings()
    # Point only the Traffic adapter at a transport that always fails.
    replacements = {
        "traffic_vms": httpx.AsyncClient(transport=Broken(), base_url="http://traffic-vms:8001"),
        # Reuse Municipal from the app state so it stays healthy.
        "municipal_vms": central_app.state.adapters["municipal_vms"]._client,
    }
    central_app.state.adapters = build_adapters(settings, clients=replacements)

    response = await api.post("/api/v1/sources/sync", headers=admin_headers)
    response.raise_for_status()
    body = response.json()

    assert body["success"] is True
    assert body["sources"]["traffic_vms"]["errors"]
    assert body["sources"]["traffic_vms"]["synchronized"] == 0
    assert body["sources"]["municipal_vms"]["synchronized"] >= 1

    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    assert any(c["source_system"] == "municipal_vms" for c in cameras)


async def test_source_errors_become_useful_central_errors(api, admin_headers):
    """A vendor-shaped error must be translated into Vigentra's vocabulary."""
    # Ask the Traffic system for a record it does not know about.
    response = await api.get(
        "/api/v1/installation-requests/TRF-REQ-DOES-NOT-EXIST", headers=admin_headers
    )
    assert response.status_code in (404, 502)
    detail = response.json().get("detail")
    if isinstance(detail, dict):
        # Central-translated error, not the raw vendor envelope.
        assert "message" in detail
        assert "code" in detail
        assert "status" not in detail  # "status" is the vendor's key, not ours


async def test_denied_operator_form_creation_records_permission_denial(api):
    """A read-only account may not create an installation form."""
    # Health monitor is read-only.
    hm = await token_for(api, "health.monitor", "Health@2026")
    response = await api.post(
        "/api/v1/installation-requests", headers=hm, json={"form": demo_form()}
    )
    assert response.status_code == 403
