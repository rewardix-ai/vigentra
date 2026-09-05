"""Incident candidates: ingest, scope, dedupe, review.

An incident is never a finding. These tests hold the boundary that the whole
feature turns on: the edge may raise a CANDIDATE, only a human may dispose of
it, and an account may only see or submit candidates for cameras it may already
read detections for.
"""
import pytest


async def _submit(api, headers, camera_id, **over):
    body = {
        "camera_id": camera_id,
        "kind": "WRONG_WAY",
        "severity": "MEDIUM",
        "track_ids": [7],
        "first_seen": 10.0,
        "last_seen": 13.5,
        "reason": "car against this camera's established flow",
        "evidence": {"difference_deg": 175.0, "vehicle": "car"},
        **over,
    }
    return await api.post(
        "/api/v1/incidents/ingest",
        headers=headers,
        json={"incidents": [body]},
    )


@pytest.mark.asyncio
async def test_edge_can_raise_and_operator_can_read(api, login, traffic_camera):
    edge = await login("traffic.ai")
    r = await _submit(api, edge, traffic_camera["camera_id"])
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1

    operator = await login("dept.admin")
    listed = (await api.get("/api/v1/incidents", headers=operator)).json()
    assert any(i["camera_id"] == traffic_camera["camera_id"] for i in listed)
    one = next(i for i in listed if i["camera_id"] == traffic_camera["camera_id"])
    assert one["status"] == "CANDIDATE"
    # Duration is preserved from the PTS window even though the instant is anchored.
    assert one["duration_s"] == pytest.approx(3.5, abs=0.2)


@pytest.mark.asyncio
async def test_the_same_incident_does_not_duplicate(api, login, traffic_camera):
    edge = await login("traffic.ai")
    first = await _submit(api, edge, traffic_camera["camera_id"])
    second = await _submit(api, edge, traffic_camera["camera_id"])
    assert first.json()["accepted"] == 1
    # Re-raised: extended, not duplicated.
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 1


@pytest.mark.asyncio
async def test_an_unknown_kind_is_refused(api, login, traffic_camera):
    edge = await login("traffic.ai")
    r = await _submit(api, edge, traffic_camera["camera_id"], kind="METEOR_STRIKE")
    assert r.status_code == 200
    assert r.json()["rejected"] == 1
    assert r.json()["errors"][0]["code"] == "UNKNOWN_KIND"


@pytest.mark.asyncio
async def test_municipal_edge_cannot_raise_for_a_traffic_camera(api, login, traffic_camera):
    municipal_edge = await login("municipal.ai")
    r = await _submit(api, municipal_edge, traffic_camera["camera_id"])
    assert r.status_code == 200
    assert r.json()["accepted"] == 0
    assert r.json()["rejected"] == 1
    assert r.json()["errors"][0]["code"] == "CAMERA_OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_a_human_confirms_and_it_is_recorded(api, login, traffic_camera):
    edge = await login("traffic.ai")
    await _submit(api, edge, traffic_camera["camera_id"])
    operator = await login("dept.admin")
    listed = (await api.get("/api/v1/incidents", headers=operator)).json()
    iid = listed[0]["incident_id"]

    r = await api.patch(
        f"/api/v1/incidents/{iid}",
        headers=operator,
        json={"status": "CONFIRMED", "note": "checked the clip"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "CONFIRMED"
    assert body["reviewed_by"] == "dept.admin"
    assert body["review_note"] == "checked the clip"


@pytest.mark.asyncio
async def test_review_status_is_validated(api, login, traffic_camera):
    edge = await login("traffic.ai")
    await _submit(api, edge, traffic_camera["camera_id"])
    operator = await login("dept.admin")
    iid = (await api.get("/api/v1/incidents", headers=operator)).json()[0]["incident_id"]
    r = await api.patch(
        f"/api/v1/incidents/{iid}", headers=operator, json={"status": "OBLITERATED"}
    )
    assert r.status_code == 422
