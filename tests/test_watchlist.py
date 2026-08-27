"""Watchlist, alerting and cross-camera movement.

These are the capabilities the challenge's graded test case is built on, and
they are also the ones with the most room to be quietly wrong: a matcher that
never fires, a permission that leaks a registration number to a role that
should not see it, or a route assembled from sightings the caller could not
have read. Each of those has a test here.

The suite drives the real ingest path rather than writing sightings directly,
because "a plate arrives from the edge and an alert exists a moment later" is
the behaviour under test — not the shape of a row.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "central-api"))

from app.services import plate_matching  # noqa: E402

pytestmark = pytest.mark.asyncio


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def plate_detection(
    camera_id: str,
    plate: str,
    *,
    detection_id: str,
    moment: datetime | None = None,
    confidence: float = 0.88,
    observations: int = 9,
) -> dict:
    """One ingest-shaped detection carrying a plate read."""
    stamp = iso(moment or datetime.now(timezone.utc))
    return {
        "detection_id": detection_id,
        "camera_id": camera_id,
        "timestamp_utc": stamp,
        "class_name": "car",
        "class_id": 2,
        "confidence": 0.91,
        "bbox_xyxy": [100.0, 200.0, 380.0, 420.0],
        "model_name": "test-detector",
        "model_version": "1.0",
        "source_mode": "mock",
        "is_demo_data": True,
        "plate_text": plate,
        "plate_confidence": confidence,
        "plate_bbox_xyxy": [180.0, 360.0, 300.0, 400.0],
        "plate_reader": "test-reader/1.0",
        "provenance": {"plate_observations": observations},
    }


async def commission_second_traffic_camera(api, installer_headers, admin_headers) -> dict:
    """Onboard one more Traffic camera through the real flow, and sync it.

    The Traffic mock ships a single camera, and a cross-camera route needs two.
    Going through onboarding rather than inserting a row keeps the fixture
    honest: the camera arrives the way every camera arrives.
    """
    from conftest import demo_form

    form = demo_form(
        camera_name="Iskcon Circle North",
        latitude=23.0281,
        longitude=72.5070,
        road_or_junction="Iskcon Circle",
        address_or_landmark="Iskcon Circle, north arm",
    )
    draft = await api.post(
        "/api/v1/installation-requests", headers=installer_headers, json={"form": form}
    )
    draft.raise_for_status()
    request_id = draft.json()["request_id"]

    submitted = await api.post(
        f"/api/v1/installation-requests/{request_id}/submit", headers=installer_headers
    )
    submitted.raise_for_status()
    assert submitted.json()["status"] == "REGISTERED", submitted.text

    await api.post("/api/v1/sources/sync", headers=admin_headers)
    listed = await api.get("/api/v1/cameras", headers=admin_headers)
    return next(
        camera
        for camera in listed.json()["items"]
        if camera["external_camera_id"] == form["external_camera_id"]
    )


async def add_watch(api, headers, plate: str, category: str = "stolen", **extra):
    return await api.post(
        "/api/v1/watchlist",
        headers=headers,
        json={
            "plate": plate,
            "category": category,
            "reason": "Reported stolen, FIR 118/2026, Ahmedabad City",
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# The matcher itself - pure, and worth testing without a database
# ---------------------------------------------------------------------------

def test_identical_plates_are_distance_zero():
    assert plate_matching.plate_distance("GJ01AB1234", "GJ 01 AB 1234") == 0.0


def test_a_known_confusion_costs_less_than_an_unrelated_swap():
    """B/8 is a pair the reader is known to swap; B/X is not.

    The tables are letter-to-digit and back, not digit-to-digit, because that
    is what plate slot repair needs: a slot either wants a letter or a digit,
    and the confusion that matters is reading one as the other.
    """
    confused = plate_matching.plate_distance("GJ01AB1234", "GJ01A81234")
    unrelated = plate_matching.plate_distance("GJ01AB1234", "GJ01AX1234")
    assert confused < unrelated
    assert confused == pytest.approx(plate_matching.SUB_CONFUSABLE)
    assert unrelated == pytest.approx(plate_matching.SUB_UNRELATED)


def test_a_dropped_character_is_cheaper_than_a_wrong_one():
    """A clipped plate must not look like a different vehicle.

    ``GJ21RS344`` for ``GJ21RS3344`` was seen repeatedly in real footage. If a
    deletion cost the same as a substitution, that read would rank alongside a
    genuinely different registration.
    """
    dropped = plate_matching.plate_distance("GJ21RS3344", "GJ21RS344")
    substituted = plate_matching.plate_distance("GJ21RS3344", "GJ21RS3744")
    assert dropped < substituted


def test_a_different_vehicle_is_rejected_at_the_default_threshold():
    distance = plate_matching.plate_distance("GJ01AB1234", "MH12XY9876")
    assert distance > plate_matching.DEFAULT_MAX_DISTANCE


def test_a_bogus_state_code_is_not_a_plausible_plate():
    """OCR's `OJ` or `6J` must never be storable as a registration."""
    assert plate_matching.is_plausible("GJ01AB1234")
    assert not plate_matching.is_plausible("OJ01AB1234")
    assert not plate_matching.is_plausible("6J01AB1234")
    assert not plate_matching.is_plausible("XX")


def test_bharat_and_diplomatic_series_are_recognised():
    """Special series carry their code in slots 3-4, after a numeric prefix."""
    assert plate_matching.state_of("21BH1234AA") == "BH"
    assert plate_matching.state_of("11CD1234") == "CD"
    assert plate_matching.is_plausible("11CD1234")


def test_best_matches_returns_every_candidate_in_range_closest_first():
    matches = plate_matching.best_matches(
        "GJ01AB1234", ["GJ01AB1234", "GJ01AB1284", "MH12XY9876"]
    )
    assert [match.watch_plate for match in matches] == ["GJ01AB1234", "GJ01AB1284"]
    assert matches[0].exact and not matches[1].exact


# ---------------------------------------------------------------------------
# Watchlist management
# ---------------------------------------------------------------------------

async def test_an_entry_needs_a_reason(api, login):
    headers = await login("traffic.state")
    response = await api.post(
        "/api/v1/watchlist",
        headers=headers,
        json={"plate": "GJ01AB1234", "category": "stolen", "reason": "  "},
    )
    assert response.status_code == 422


async def test_an_implausible_plate_is_refused_rather_than_stored(api, login):
    """A string the matcher can never match is a typo, not a watchlist entry."""
    headers = await login("traffic.state")
    response = await add_watch(api, headers, "OJ01AB1234")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "IMPLAUSIBLE_PLATE"


async def test_adding_twice_conflicts(api, login):
    headers = await login("traffic.state")
    assert (await add_watch(api, headers, "GJ05JV3456")).status_code == 201
    duplicate = await add_watch(api, headers, "GJ05JV3456")
    assert duplicate.status_code == 409


async def test_an_operator_without_manage_cannot_add(api, login):
    """Reading the list and adding to it are different acts."""
    headers = await login("auditor")  # holds watchlist:read, not watchlist:manage
    assert (await api.get("/api/v1/watchlist", headers=headers)).status_code == 200
    assert (await add_watch(api, headers, "GJ01AB1234")).status_code == 403


async def test_municipal_holds_no_watchlist_permission_at_all(api, login):
    """Civic monitoring counts vehicles; it does not identify their owners."""
    headers = await login("municipal.state")
    assert (await api.get("/api/v1/watchlist", headers=headers)).status_code == 403
    assert (await api.get("/api/v1/alerts", headers=headers)).status_code == 403


async def test_deactivating_keeps_the_entry_and_records_who(api, login):
    headers = await login("traffic.state")
    created = await add_watch(api, headers, "GJ18KK9090")
    entry_id = created.json()["entry_id"]

    response = await api.request(
        "DELETE",
        f"/api/v1/watchlist/{entry_id}",
        headers=headers,
        json={"reason": "Vehicle recovered"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is False
    assert body["deactivated_by"] == "traffic.state"

    listed = await api.get("/api/v1/watchlist?active_only=false", headers=headers)
    assert any(item["entry_id"] == entry_id for item in listed.json())


# ---------------------------------------------------------------------------
# Ingest raises alerts
# ---------------------------------------------------------------------------

async def test_an_exact_plate_read_raises_an_alert(api, login, traffic_camera):
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ01AB1234")

    edge = await login("traffic.ai")
    ingest = await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ01AB1234", detection_id="det-exact-1"
                )
            ]
        },
    )
    assert ingest.status_code == 200, ingest.text
    body = ingest.json()
    assert body["sightings_recorded"] == 1
    assert body["alerts_raised"] == 1

    alerts = await api.get("/api/v1/alerts", headers=watcher)
    hits = [a for a in alerts.json() if a["sighting_id"]]
    assert hits and hits[0]["exact"] is True
    assert hits[0]["watch_plate"] == "GJ01AB1234"
    assert hits[0]["category"] == "stolen"


async def test_a_single_misread_still_alerts_but_is_marked_inexact(
    api, login, traffic_camera
):
    """The camera that read one character wrong is the one that matters most."""
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ01AB1234")

    edge = await login("traffic.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ01AB1284", detection_id="det-near-1"
                )
            ]
        },
    )

    alerts = (await api.get("/api/v1/alerts", headers=watcher)).json()
    assert len(alerts) == 1
    assert alerts[0]["exact"] is False
    assert 0 < alerts[0]["distance"] <= plate_matching.DEFAULT_MAX_DISTANCE
    assert alerts[0]["seen_plate"] == "GJ01AB1284"


async def test_a_different_vehicle_raises_nothing(api, login, traffic_camera):
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ01AB1234")

    edge = await login("traffic.ai")
    result = await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "MH12XY9876", detection_id="det-other-1"
                )
            ]
        },
    )
    assert result.json()["sightings_recorded"] == 1
    assert result.json()["alerts_raised"] == 0


async def test_an_entry_added_after_the_pass_does_not_invent_an_alert(
    api, login, traffic_camera
):
    """Matching happens on ingest. Retroactive alerts would be a lie about time."""
    edge = await login("traffic.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ07LL7777", detection_id="det-early-1"
                )
            ]
        },
    )

    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ07LL7777")

    alerts = (await api.get("/api/v1/alerts", headers=watcher)).json()
    assert alerts == []


async def test_a_new_entry_is_live_for_the_very_next_batch(api, login, traffic_camera):
    """The matcher's cache must not delay a stolen-vehicle entry."""
    watcher = await login("traffic.state")
    edge = await login("traffic.ai")

    # Warm the cache with a batch that hits nothing.
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ09ZZ1111", detection_id="det-warm-1"
                )
            ]
        },
    )
    await add_watch(api, watcher, "GJ09ZZ2222")

    result = await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ09ZZ2222", detection_id="det-warm-2"
                )
            ]
        },
    )
    assert result.json()["alerts_raised"] == 1


async def test_replaying_a_batch_does_not_double_count(api, login, traffic_camera):
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ11RE1234")
    edge = await login("traffic.ai")

    batch = {
        "detections": [
            plate_detection(
                traffic_camera["camera_id"], "GJ11RE1234", detection_id="det-replay-1"
            )
        ]
    }
    first = await api.post("/api/v1/detections/ingest", headers=edge, json=batch)
    second = await api.post("/api/v1/detections/ingest", headers=edge, json=batch)

    assert first.json()["sightings_recorded"] == 1
    assert second.json()["sightings_recorded"] == 0
    assert second.json()["alerts_raised"] == 0
    assert len((await api.get("/api/v1/alerts", headers=watcher)).json()) == 1


async def test_an_unreadable_plate_is_dropped_not_stored(api, login, traffic_camera):
    """Half-read text is worse than no text: it looks like evidence."""
    edge = await login("traffic.ai")
    result = await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "XZ9", detection_id="det-junk-1"
                )
            ]
        },
    )
    body = result.json()
    assert body["accepted"] == 1, "the detection itself is still a valid observation"
    assert body["sightings_recorded"] == 0
    assert body["sightings_rejected"] == 1


# ---------------------------------------------------------------------------
# Disclosure and scope
# ---------------------------------------------------------------------------

async def test_an_alert_reader_without_plate_read_sees_the_hit_but_not_the_plate(
    api, login, traffic_camera, monkeypatch
):
    """The alert is not refused; the identifying field is."""
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ22PW4321")
    edge = await login("traffic.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ22PW4321", detection_id="det-withheld-1"
                )
            ]
        },
    )

    from app.config import Permission, ROLE_PERMISSIONS, Role

    original = ROLE_PERMISSIONS[Role.TRAFFIC_OPERATOR]
    monkeypatch.setitem(
        ROLE_PERMISSIONS, Role.TRAFFIC_OPERATOR, original - {Permission.PLATE_READ}
    )

    stripped = await login("traffic.state")
    alerts = (await api.get("/api/v1/alerts", headers=stripped)).json()
    assert len(alerts) == 1
    assert alerts[0]["plate_withheld"] is True
    assert alerts[0]["watch_plate"] is None
    assert alerts[0]["seen_plate"] is None
    # ...but the operational facts are still there.
    assert alerts[0]["camera_id"] == traffic_camera["camera_id"]
    assert alerts[0]["category"] == "stolen"


async def test_an_alert_on_another_units_camera_is_out_of_scope(
    api, login, municipal_camera
):
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ33OO5555")

    edge = await login("municipal.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    municipal_camera["camera_id"], "GJ33OO5555", detection_id="det-scope-1"
                )
            ]
        },
    )

    # The alert exists, but a Traffic operator scoped to Traffic cannot see it.
    visible = (await api.get("/api/v1/alerts", headers=watcher)).json()
    assert all(a["camera_id"] != municipal_camera["camera_id"] for a in visible)


async def test_acknowledging_records_the_human_who_looked(api, login, traffic_camera):
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ44QQ6666")
    edge = await login("traffic.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ44QQ6666", detection_id="det-ack-1"
                )
            ]
        },
    )

    alert_id = (await api.get("/api/v1/alerts", headers=watcher)).json()[0]["alert_id"]
    response = await api.post(
        f"/api/v1/alerts/{alert_id}/acknowledge",
        headers=watcher,
        json={"dismissed_reason": "Reviewed the frame - different vehicle"},
    )
    assert response.status_code == 200
    assert response.json()["acknowledged_by"] == "traffic.state"
    assert response.json()["dismissed_reason"].startswith("Reviewed")

    audit = await api.get("/api/v1/audit?limit=200", headers=await login("auditor"))
    actions = [entry["action"] for entry in audit.json()]
    assert "alert_dismissed" in actions
    assert "watchlist_alert_raised" in actions


# ---------------------------------------------------------------------------
# Movement history - the graded clause
# ---------------------------------------------------------------------------

async def test_a_route_is_ordered_by_time_across_cameras(
    api, login, cameras, traffic_installer_headers, admin_headers
):
    """The graded test case: trace one registration across the network.

    Commissions a second Traffic camera through the real onboarding flow rather
    than skipping, because a route over one camera proves nothing about the
    part that is actually hard — ordering sightings from different sources into
    one timeline.
    """
    traffic = [
        camera
        for camera in cameras
        if camera["source_system"] == "traffic_vms"
        and camera["installation"]["installation_status"] == "COMMISSIONED"
    ]
    if len(traffic) < 2:
        traffic.append(
            await commission_second_traffic_camera(
                api, traffic_installer_headers, admin_headers
            )
        )

    edge = await login("traffic.ai")
    base = datetime.now(timezone.utc) - timedelta(minutes=30)
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                # Deliberately submitted out of order.
                plate_detection(
                    traffic[1]["camera_id"], "GJ55TR8888",
                    detection_id="det-route-2", moment=base + timedelta(minutes=6),
                ),
                plate_detection(
                    traffic[0]["camera_id"], "GJ55TR8888",
                    detection_id="det-route-1", moment=base,
                ),
            ]
        },
    )

    operator = await login("traffic.state")
    response = await api.get(
        "/api/v1/plates/GJ55TR8888/track",
        headers=operator,
        params={"reason": "Tracing a designated vehicle for the technical evaluation"},
    )
    assert response.status_code == 200
    track = response.json()
    assert len(track["points"]) == 2
    assert track["cameras_seen"] == 2
    stamps = [point["timestamp_utc"] for point in track["points"]]
    assert stamps == sorted(stamps), "a route must be in time order"
    assert track["points"][1]["seconds_from_previous"] == pytest.approx(360, abs=2)
    assert track["caveat"]


async def test_a_track_needs_a_stated_reason(api, login):
    """The most revealing query the platform answers, so the reason is required."""
    operator = await login("traffic.state")
    response = await api.get("/api/v1/plates/GJ01AB1234/track", headers=operator)
    assert response.status_code == 422


async def test_tracing_is_audited_against_the_account(api, login, traffic_camera):
    operator = await login("traffic.state")
    await api.get(
        "/api/v1/plates/GJ66UU1010/track",
        headers=operator,
        params={"reason": "Complaint 44/2026, vehicle seen leaving the scene"},
    )
    audit = await api.get("/api/v1/audit?limit=100", headers=await login("auditor"))
    entries = [
        entry
        for entry in audit.json()
        if entry["action"] == "vehicle_movement_viewed"
    ]
    assert entries, "a movement query must leave a trail"
    assert entries[0]["username"] == "traffic.state"
    assert "44/2026" in (entries[0]["case_or_reason"] or "")


async def test_repeated_reads_at_one_camera_collapse_into_one_pass(
    api, login, traffic_camera
):
    """A vehicle waiting at a signal is one point, not forty."""
    edge = await login("traffic.ai")
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ77WW2020",
                    detection_id=f"det-pass-{index}",
                    moment=base + timedelta(seconds=index * 5),
                )
                for index in range(6)
            ]
        },
    )

    operator = await login("traffic.state")
    track = (
        await api.get(
            "/api/v1/plates/GJ77WW2020/track",
            headers=operator,
            params={"reason": "Checking pass collapsing behaviour"},
        )
    ).json()
    assert len(track["points"]) == 1
    assert track["points"][0]["observations"] >= 6


async def test_search_ranks_near_plates_the_network_actually_saw(
    api, login, traffic_camera
):
    edge = await login("traffic.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ88XY3030", detection_id="det-search-1"
                ),
                plate_detection(
                    traffic_camera["camera_id"], "GJ88XY3080", detection_id="det-search-2"
                ),
            ]
        },
    )

    operator = await login("traffic.state")
    hits = (
        await api.get("/api/v1/plates/search", headers=operator, params={"q": "GJ88XY3030"})
    ).json()
    assert hits[0]["plate"] == "GJ88XY3030"
    assert hits[0]["exact"] is True
    assert any(hit["plate"] == "GJ88XY3080" and not hit["exact"] for hit in hits)


async def test_the_anpr_report_carries_plates_places_and_timestamps(
    api, login, traffic_camera
):
    """The artefact the challenge asks to be submitted with the feed demo."""
    watcher = await login("traffic.state")
    await add_watch(api, watcher, "GJ99ZR4040", category="wanted")
    edge = await login("traffic.ai")
    await api.post(
        "/api/v1/detections/ingest",
        headers=edge,
        json={
            "detections": [
                plate_detection(
                    traffic_camera["camera_id"], "GJ99ZR4040", detection_id="det-report-1"
                )
            ]
        },
    )

    report = (await api.get("/api/v1/reports/anpr", headers=watcher)).json()
    assert report
    row = next(line for line in report if line["plate"] == "GJ99ZR4040")
    assert row["camera_id"] == traffic_camera["camera_id"]
    assert row["timestamp_utc"].endswith("Z")
    assert row["watchlist_hit"] is True
    assert row["watchlist_category"] == "wanted"

    csv_response = await api.get("/api/v1/reports/anpr?as_csv=true", headers=watcher)
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "GJ99ZR4040" in csv_response.text
    assert "attachment" in csv_response.headers["content-disposition"]
