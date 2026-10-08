"""A settled plate belongs to the whole vehicle, not to one frame.

The edge decides a plate once, when the vehicle's track closes, but it detected the vehicle in every
frame before that. Central writes the plate onto those earlier detections of the same vehicle (same
camera, same tracker id, in the few minutes before), so a detections list shows the vehicle with its
plate rather than dozens of plateless rows and one with a plate. Other vehicles are untouched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from test_watchlist import iso, plate_detection


def vehicle_frame(camera_id: str, *, detection_id: str, moment: datetime, track_id: int) -> dict:
    return {
        "detection_id": detection_id,
        "camera_id": camera_id,
        "timestamp_utc": iso(moment),
        "class_name": "car",
        "class_id": 2,
        "confidence": 0.9,
        "bbox_xyxy": [100.0, 200.0, 380.0, 420.0],
        "model_name": "test-detector",
        "model_version": "1.0",
        "source_mode": "mock",
        "is_demo_data": True,
        "provenance": {"track_id": track_id},
    }


async def test_the_plate_reaches_every_frame_of_its_vehicle(api, login, traffic_camera):
    edge = await login("traffic.ai")
    cam = traffic_camera["camera_id"]
    now = datetime.now(timezone.utc)
    frames = [vehicle_frame(cam, detection_id=f"v7-{i}", moment=now - timedelta(seconds=20 - i), track_id=7) for i in range(5)]
    other = [vehicle_frame(cam, detection_id=f"v8-{i}", moment=now - timedelta(seconds=20 - i), track_id=8) for i in range(3)]
    old = [vehicle_frame(cam, detection_id="v7-old", moment=now - timedelta(minutes=10), track_id=7)]
    (await api.post("/api/v1/detections/ingest", headers=edge, json={"detections": frames + other + old})).raise_for_status()

    plate = plate_detection(cam, "GJ01AB1234", detection_id="v7-plate", moment=now)
    plate["provenance"]["track_id"] = 7
    (await api.post("/api/v1/detections/ingest", headers=edge, json={"detections": [plate]})).raise_for_status()

    reader = await login("traffic.state")
    rows = (await api.get(f"/api/v1/cameras/{cam}/detections", headers=reader, params={"limit": 100})).json()
    plates = {r["detection_id"]: r.get("plate_text") for r in rows}
    assert all(plates[f"v7-{i}"] == "GJ01AB1234" for i in range(5))
    assert all(plates[f"v8-{i}"] is None for i in range(3))      # another vehicle keeps no plate
    assert plates["v7-old"] is None                              # same tracker id, long before: not this vehicle


async def test_vehicles_seen_counts_vehicles_not_frames(api, login, traffic_camera):
    edge = await login("traffic.ai")
    cam = traffic_camera["camera_id"]
    now = datetime.now(timezone.utc)
    near = [vehicle_frame(cam, detection_id=f"n{t}-{i}", moment=now - timedelta(seconds=30 - i), track_id=t) for t in (21, 22) for i in range(4)]
    far = [dict(vehicle_frame(cam, detection_id=f"f23-{i}", moment=now - timedelta(seconds=30 - i), track_id=23), bbox_xyxy=[10.0, 10.0, 60.0, 40.0]) for i in range(6)]
    untracked = [dict(vehicle_frame(cam, detection_id="u-1", moment=now, track_id=0), provenance={})]
    (await api.post("/api/v1/detections/ingest", headers=edge, json={"detections": near + far + untracked})).raise_for_status()
    plate = plate_detection(cam, "GJ05CD4321", detection_id="n21-plate", moment=now)
    plate["provenance"]["track_id"] = 21
    (await api.post("/api/v1/detections/ingest", headers=edge, json={"detections": [plate]})).raise_for_status()

    reader = await login("traffic.state")
    body = (await api.get("/api/v1/detections/vehicles", headers=reader, params={"camera_id": cam})).json()
    by_plate = {v["plate_text"]: v for v in body["vehicles"]}
    assert body["vehicles_seen"] == 3 and body["near_enough_to_read"] == 2 and body["identified"] == 1
    assert body["identified_share_of_near"] == 0.5 and body["untracked_frames"] == 1
    assert by_plate["GJ05CD4321"]["frames"] == 5 and by_plate["GJ05CD4321"]["no_plate_reason"] is None
    reasons = sorted(v["no_plate_reason"] for v in body["vehicles"] if not v["plate_text"])
    assert reasons == ["plate not readable", "too far to read"]
