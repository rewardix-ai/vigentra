"""Pursuit: one designated vehicle, followed across the cameras.

The operator starts it with a reason; edge readers learn which cameras are hot (where the vehicle was
just seen, and their neighbours) and send back possible sightings with the plate crop. A possible
sighting is on the route, marked possible, never passed off as a reading.
"""
from __future__ import annotations

from test_incidents import _JPEG_B64
from test_watchlist import plate_detection

PLATE = "GJ01AB4321"


async def test_a_pursuit_from_start_to_end(api, login, traffic_camera):
    cam = traffic_camera["camera_id"]
    operator = await login("traffic.state")
    started = await api.post("/api/v1/pursuits", headers=operator, json={"plate": PLATE, "reason": "Rehearsal: stolen vehicle"})
    assert started.status_code == 201, started.text
    pid = started.json()["id"]

    edge = await login("traffic.ai")
    assert (await api.get("/api/v1/pursuits", headers=edge)).json()["pursuits"][0]["hot_cameras"] == []
    (await api.post("/api/v1/detections/ingest", headers=edge,
                    json={"detections": [plate_detection(cam, PLATE, detection_id="p-1")]})).raise_for_status()
    active = (await api.get("/api/v1/pursuits", headers=edge)).json()["pursuits"][0]
    assert active["plate"] == PLATE and cam in active["hot_cameras"]

    possible = await api.post(f"/api/v1/pursuits/{pid}/possible", headers=edge, json={
        "camera_id": cam, "track": "t7", "read_as": "GJ01AB4821", "target_score": -1.2, "crops": 3,
        "crop_jpeg_b64": _JPEG_B64})
    assert possible.status_code == 201, possible.text
    again = await api.post(f"/api/v1/pursuits/{pid}/possible", headers=edge, json={
        "camera_id": cam, "track": "t7", "target_score": -1.2})
    assert again.json() == {"duplicate": True}

    route = (await api.get(f"/api/v1/plates/{PLATE}/track", headers=operator, params={"reason": "Rehearsal"})).json()
    # same camera, same pass: the confirmed reading stands, the guess beside it adds nothing
    assert [p["possible"] for p in route["points"]] == [False]
    sid = possible.json()["sighting_id"]
    img = await api.get(f"/api/v1/pursuits/evidence/{sid}", headers=operator)
    assert img.status_code == 200 and img.content.startswith(b"\xff\xd8")

    ended = await api.post(f"/api/v1/pursuits/{pid}/end", headers=operator)
    assert ended.json()["active"] is False
    assert (await api.get("/api/v1/pursuits", headers=edge)).json()["pursuits"] == []


async def test_edge_cannot_start_a_pursuit(api, login):
    edge = await login("traffic.ai")
    r = await api.post("/api/v1/pursuits", headers=edge, json={"plate": PLATE, "reason": "not mine to start"})
    assert r.status_code == 403


async def test_a_possible_sighting_alone_is_on_the_route_marked_possible(api, login, traffic_camera):
    operator = await login("traffic.state")
    pid = (await api.post("/api/v1/pursuits", headers=operator, json={"plate": "GJ05XY7788", "reason": "Rehearsal"})).json()["id"]
    edge = await login("traffic.ai")
    (await api.post(f"/api/v1/pursuits/{pid}/possible", headers=edge, json={
        "camera_id": traffic_camera["camera_id"], "track": "t9", "target_score": -0.8})).raise_for_status()
    route = (await api.get("/api/v1/plates/GJ05XY7788/track", headers=operator, params={"reason": "Rehearsal"})).json()
    assert [p["possible"] for p in route["points"]] == [True]


def test_the_watched_circle_grows_with_time_since_the_sighting():
    from app.routers.pursuits import MAX_RADIUS_KM, MIN_RADIUS_KM, reach_km
    assert reach_km(0) == MIN_RADIUS_KM            # just seen: the cameras right around it
    assert reach_km(10) == 10.0                    # ten minutes at city speed
    assert reach_km(120) == MAX_RADIUS_KM          # capped: a whole region hot is no priority
