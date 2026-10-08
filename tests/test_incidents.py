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


# A tiny real JPEG (1x1), so the snapshot path is exercised without image libraries in the test.
_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////"
    "////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAA"
    "AAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


@pytest.mark.asyncio
async def test_an_incident_keeps_its_frame_for_those_who_may_see_the_camera(api, login, traffic_camera):
    edge = await login("traffic.ai")
    cam = traffic_camera["camera_id"]
    r = await _submit(api, edge, cam, track_ids=[41], snapshot_jpeg_b64=_JPEG_B64)
    assert r.json()["accepted"] == 1

    operator = await login("dept.admin")
    one = next(i for i in (await api.get("/api/v1/incidents", headers=operator)).json()
               if i["track_ids"] == [41])
    assert one["has_snapshot"] is True
    img = await api.get(f"/api/v1/incidents/{one['incident_id']}/snapshot", headers=operator)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert img.content.startswith(b"\xff\xd8")

    outsider = await login("municipal.deptadmin")
    assert (await api.get(f"/api/v1/incidents/{one['incident_id']}/snapshot", headers=outsider)).status_code == 403

    audit = (await api.get("/api/v1/audit", headers=await login("state.admin"), params={"limit": 50})).json()
    assert any(a["action"] == "incident_snapshot_viewed" and a["resource_id"] == one["incident_id"] for a in audit)


@pytest.mark.asyncio
async def test_a_bad_snapshot_never_costs_the_incident(api, login, traffic_camera):
    edge = await login("traffic.ai")
    r = await _submit(api, edge, traffic_camera["camera_id"], track_ids=[42], snapshot_jpeg_b64="bm90IGEganBlZw==")
    assert r.json()["accepted"] == 1
    operator = await login("dept.admin")
    one = next(i for i in (await api.get("/api/v1/incidents", headers=operator)).json() if i["track_ids"] == [42])
    assert one["has_snapshot"] is False


@pytest.mark.asyncio
async def test_a_replayed_recording_raises_a_new_incident_not_an_old_one(api, login, traffic_camera):
    """Grid feeds loop: the same stream time and track id can recur hours later as a new event."""
    from datetime import timedelta
    from app.database import get_session_factory
    from app.models import Incident
    from sqlalchemy import select, update

    edge = await login("traffic.ai")
    cam = traffic_camera["camera_id"]
    assert (await _submit(api, edge, cam, track_ids=[77])).json()["accepted"] == 1
    async with get_session_factory()() as db:   # that first one was seen two hours ago
        row = (await db.execute(select(Incident).where(Incident.camera_id == cam))).scalars().all()[-1]
        await db.execute(update(Incident).where(Incident.id == row.id).values(last_seen_utc=row.last_seen_utc - timedelta(hours=2)))
        await db.commit()
    again = (await _submit(api, edge, cam, track_ids=[77])).json()
    assert again["accepted"] == 1 and again["duplicates"] == 0
    assert (await _submit(api, edge, cam, track_ids=[77])).json()["duplicates"] == 1   # and that one now extends
