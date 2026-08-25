"""Installation-onboarding lifecycle tests.

Covers form creation, validation, ownership/permission checks, registration
on successful validation, and withdrawal.

The lifecycle has no approval step. A unit fills the form; if its own
system's validation passes, the camera is REGISTERED and reaches the central
registry on the next sync. What still requires a human decision is footage -
see test_video_access.py, where a unit outside the owning department must ask
for and be granted access.
"""
from __future__ import annotations

import pytest

from conftest import demo_form

pytestmark = pytest.mark.asyncio


async def _create(api, headers, **overrides) -> dict:
    response = await api.post(
        "/api/v1/installation-requests", headers=headers, json={"form": demo_form(**overrides)}
    )
    response.raise_for_status()
    return response.json()


async def test_installation_form_creation(api, traffic_installer_headers):
    record = await _create(api, traffic_installer_headers)
    assert record["status"] == "DRAFT"
    assert record["owning_department"] == "Traffic Police"
    assert record["created_by"] == "traffic.installer"
    # Sentinel never advertises video access on any installation record.
    assert record["sentinel_video_access"] is False


async def test_missing_required_field_is_rejected(api, traffic_installer_headers):
    response = await api.post(
        "/api/v1/installation-requests",
        headers=traffic_installer_headers,
        json={"form": {**demo_form(), "camera_name": ""}},
    )
    assert response.status_code == 422


async def test_invalid_coordinates_are_rejected(api, traffic_installer_headers):
    response = await api.post(
        "/api/v1/installation-requests",
        headers=traffic_installer_headers,
        json={"form": {**demo_form(), "latitude": 400.0}},
    )
    assert response.status_code == 422


async def test_draft_can_be_edited_by_owner(api, traffic_installer_headers):
    draft = await _create(api, traffic_installer_headers)
    response = await api.patch(
        f"/api/v1/installation-requests/{draft['request_id']}",
        headers=traffic_installer_headers,
        json={"form": {"camera_name": "Sarkhej Circle Renamed"}},
    )
    assert response.status_code == 200
    assert response.json()["camera_name"] == "Sarkhej Circle Renamed"


async def test_valid_submission_registers_immediately(api, traffic_installer_headers):
    """The whole point of the change: no queue between submit and registered."""
    draft = await _create(api, traffic_installer_headers)
    submit = await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    assert submit.status_code == 200
    assert submit.json()["status"] == "REGISTERED"


async def test_registered_form_is_not_editable(api, traffic_installer_headers):
    """Registration is the point of no return for free-hand edits.

    Once the record is live centrally, an installer quietly renaming the camera
    would change what every other unit sees without leaving a trace in the
    onboarding pipeline. Corrections after this point go through suspension or
    a fresh record.
    """
    draft = await _create(api, traffic_installer_headers)
    await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    edit = await api.patch(
        f"/api/v1/installation-requests/{draft['request_id']}",
        headers=traffic_installer_headers,
        json={"form": {"camera_name": "should not stick"}},
    )
    assert edit.status_code == 403


async def test_failed_validation_returns_the_form_for_correction(
    api, traffic_installer_headers
):
    """Validation is the only gate left, so it has to actually hold.

    The department's own system rejects the record and hands back its error
    list; the form becomes editable again rather than entering the registry.
    """
    draft = await _create(api, traffic_installer_headers, latitude=8.0, longitude=60.0)
    submit = await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    assert submit.status_code == 200
    body = submit.json()
    assert body["status"] == "VALIDATION_FAILED"
    assert body["validation_errors"], "the department must say what was wrong"

    fixed = await api.patch(
        f"/api/v1/installation-requests/{draft['request_id']}",
        headers=traffic_installer_headers,
        json={"form": {"latitude": 23.0225, "longitude": 72.5714}},
    )
    assert fixed.status_code == 200
    again = await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    assert again.json()["status"] == "REGISTERED"


async def test_registered_camera_reaches_the_central_registry(
    api, admin_headers, traffic_installer_headers
):
    """Metadata crosses the federation boundary without anyone signing it off.

    This is the behaviour the change exists to produce: the unit fills the
    form, and the central registry can see the camera. Note what it still does
    not get - `video_access_enabled` is the owning unit's call, and a freshly
    registered camera does not carry it.
    """
    draft = await _create(api, traffic_installer_headers)
    await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    await api.post("/api/v1/sources/sync", headers=admin_headers)

    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    match = [
        c for c in cameras
        if c["installation"]["installation_request_id"] == draft["request_id"]
    ]
    assert match, "a registered camera must be visible centrally after a sync"
    assert match[0]["installation"]["installation_status"] == "COMMISSIONED"


async def test_cross_department_withdrawal_is_refused(
    api, traffic_installer_headers, municipal_approver_headers
):
    """Removing the approval gate did not widen who may act on a record."""
    draft = await _create(api, traffic_installer_headers)
    await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    response = await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/suspend",
        headers=municipal_approver_headers,
        json={"reason": "not this department's camera"},
    )
    assert response.status_code == 403


async def test_only_registered_records_synchronise(api, admin_headers):
    """A form still being drafted is not a camera, and must not become one."""
    result = await api.post("/api/v1/sources/sync", headers=admin_headers)
    result.raise_for_status()
    body = result.json()
    for name, source in body["sources"].items():
        # Both mocks seed a record that has not been submitted yet.
        assert source["skipped_unregistered"] >= 1, f"{name} skipped nothing"

    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    statuses = {camera["approval"]["status"] for camera in cameras}
    assert statuses.issubset(
        {"REGISTERED", "SYNCHRONIZED", "SUSPENDED", "DECOMMISSIONED"}
    )


async def test_suspended_camera_is_marked_unavailable(
    api, admin_headers, traffic_installer_headers, traffic_approver_headers
):
    draft = await _create(api, traffic_installer_headers)
    await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/submit",
        headers=traffic_installer_headers,
    )
    await api.post("/api/v1/sources/sync", headers=admin_headers)

    # Suspend it and re-sync.
    await api.post(
        f"/api/v1/installation-requests/{draft['request_id']}/suspend",
        headers=traffic_approver_headers,
        json={"reason": "Pole taken down for road work"},
    )
    await api.post("/api/v1/sources/sync", headers=admin_headers)

    cameras = (await api.get("/api/v1/cameras", headers=admin_headers)).json()["items"]
    suspended = [c for c in cameras if c["installation"]["installation_request_id"] == draft["request_id"]]
    assert suspended, "the camera should still be in the registry"
    assert suspended[0]["installation"]["installation_status"] == "SUSPENDED"
    assert suspended[0]["health"]["status"] == "unavailable"
