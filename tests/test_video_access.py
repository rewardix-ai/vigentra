"""Video permission and session-security tests (spec items 9-20).

This file replaces the Module 1 `test_no_video_access.py`, which asserted that
video was refused for everyone. This phase adds *authorized* video, so the
guarantee under test changes shape: video works for exactly the right accounts
on exactly the right cameras, and is refused everywhere else.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from _media import requires_demo_clips

from conftest import assert_no_secrets, open_video_session, password_for_headers

pytestmark = pytest.mark.asyncio


LIVE = {"mode": "live", "reason": "Routine department monitoring"}


async def open_session(api, headers, camera_id: str, **overrides):
    return await open_video_session(api, headers, camera_id, **{**LIVE, **overrides})


# 9. Authorized Traffic operator can view permitted Traffic video
@requires_demo_clips
async def test_traffic_operator_can_view_traffic_video(api, login, traffic_camera):
    headers = await login("traffic.operator")
    response = await open_session(api, headers, traffic_camera["camera_id"])
    assert response.status_code == 201, response.text

    session = response.json()
    assert session["stream_url"] == f"/api/v1/streams/{session['session_id']}"
    assert session["expires_in_seconds"] > 0
    assert session["watermark"]
    assert session["audit_id"]

    stream = await api.get(session["stream_url"], headers=headers)
    assert stream.status_code == 200
    assert stream.headers["content-type"] == "video/mp4"
    assert len(stream.content) > 1000, "expected real media bytes"


# 10. Authorized Municipal operator can view permitted Municipal video
@requires_demo_clips
async def test_municipal_operator_can_view_municipal_video(api, login, municipal_camera):
    headers = await login("municipal.operator")
    response = await open_session(api, headers, municipal_camera["camera_id"])
    assert response.status_code == 201, response.text

    stream = await api.get(response.json()["stream_url"], headers=headers)
    assert stream.status_code == 200


# 11. Traffic operator cannot view Municipal video
async def test_traffic_operator_cannot_view_municipal_video(api, login, municipal_camera):
    headers = await login("traffic.operator")
    response = await open_session(api, headers, municipal_camera["camera_id"])
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "VIDEO_ACCESS_DENIED"


# 12. Municipal operator cannot view Traffic video
async def test_municipal_operator_cannot_view_traffic_video(api, login, traffic_camera):
    headers = await login("municipal.operator")
    response = await open_session(api, headers, traffic_camera["camera_id"])
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "VIDEO_ACCESS_DENIED"


# 13. State metadata viewer cannot create a video session
async def test_state_registry_viewer_cannot_open_video(api, login, traffic_camera):
    headers = await login("registry.viewer")
    response = await open_session(api, headers, traffic_camera["camera_id"])
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "VIDEO_ACCESS_DENIED"


async def test_state_admin_video_is_opt_in(api, login, traffic_camera):
    """Breadth of oversight is not breadth of viewing.

    A statewide admin sees every camera exists without being able to watch any
    of them. What stands between it and the footage is not a deployment flag
    but the owning unit: it has to ask, and an operator has to agree.
    """
    headers = await login("state.admin")
    detail = await api.get(f"/api/v1/cameras/{traffic_camera['camera_id']}", headers=headers)
    assert detail.status_code == 200, "state admin reads metadata"
    # Not a flat `denied`: there is a route to the footage, and it runs through
    # the owning unit. Collapsing the two answers costs the operator a call.
    assert detail.json()["video_access"] == "needs_unit_approval"

    response = await open_session(api, headers, traffic_camera["camera_id"])
    assert response.status_code == 403
    body = response.json()["detail"]
    assert body["state"] == "needs_unit_approval"
    assert body["owning_department"] == traffic_camera["owning_department"]
    assert body["request_access_at"] == "/api/v1/video-access-requests"


# 14. Health monitor cannot create a video session
async def test_health_monitor_cannot_open_video(api, login, traffic_camera):
    headers = await login("health.monitor")
    response = await open_session(api, headers, traffic_camera["camera_id"])
    assert response.status_code == 403


# 15. City/zone scope is enforced
async def test_zone_scope_is_enforced(api, login, traffic_camera):
    """Same department, same city, different zone - still refused.

    `traffic.zone3` is scoped to a zone that holds no cameras, so this isolates
    zone scope from department and city scope.
    """
    in_zone = await login("traffic.operator")
    out_of_zone = await login("traffic.zone3")

    assert (await open_session(api, in_zone, traffic_camera["camera_id"])).status_code == 201

    response = await open_session(api, out_of_zone, traffic_camera["camera_id"])
    assert response.status_code == 403
    assert "zone" in response.json()["detail"]["message"].lower()


async def test_camera_with_video_disabled_by_owner_is_refused(traffic_camera):
    """The owning department's flag is a hard gate no role can override.

    Asserted against the decision function directly rather than against
    whichever cameras happen to be seeded. This used to hunt the fixture set
    for a video-disabled camera and skip when it found none - which is exactly
    what happened when the demo was trimmed, quietly retiring the check on a
    guarantee that must never lapse.

    The account here is the most privileged one in the system, so a pass means
    the flag genuinely cannot be overridden from above.
    """
    from app.config import get_settings
    from app.services import video_permissions

    settings = get_settings()
    admin = next(u for u in settings.demo_users if u.username == "system.admin")
    camera = SimpleNamespace(
        camera_id=traffic_camera["camera_id"],
        owning_department=traffic_camera["owning_department"],
        installation_status="COMMISSIONED",
        video_access_enabled=False,  # the owner's switch, off
        health_status="online",
        city=traffic_camera["location"]["city"],
        zone=traffic_camera["location"].get("zone"),
        capabilities=["metadata", "health", "live", "playback"],
    )

    decision = video_permissions.evaluate(admin, camera, settings)
    assert decision.allowed is False
    assert decision.state.value == "not_enabled_by_owner"
    assert decision.allowed_modes == ()


# 16. Expired video session cannot be used
async def test_expired_session_cannot_stream(api, login, traffic_camera, monkeypatch):
    headers = await login("traffic.operator")
    session = (await open_session(api, headers, traffic_camera["camera_id"])).json()

    # Wind the stored expiry into the past rather than sleeping five minutes.
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.database import get_session_factory
    from app.models import VideoSession as VideoSessionRow

    async with get_session_factory()() as db:
        row = (
            await db.execute(
                select(VideoSessionRow).where(
                    VideoSessionRow.session_id == session["session_id"]
                )
            )
        ).scalar_one()
        row.expires_at_utc = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()

    stream = await api.get(session["stream_url"], headers=headers)
    assert stream.status_code == 410
    assert stream.json()["detail"]["code"] == "VIDEO_SESSION_ENDED"

    status = await api.get(
        f"/api/v1/video-sessions/{session['session_id']}/status", headers=headers
    )
    assert status.json()["status"] == "expired"


# 17. Stopped session cannot be used
@requires_demo_clips
async def test_revoked_session_cannot_stream(api, login, traffic_camera):
    headers = await login("traffic.operator")
    session = (await open_session(api, headers, traffic_camera["camera_id"])).json()

    assert (await api.get(session["stream_url"], headers=headers)).status_code == 200

    revoked = await api.delete(
        f"/api/v1/video-sessions/{session['session_id']}", headers=headers
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    assert (await api.get(session["stream_url"], headers=headers)).status_code == 410


async def test_session_is_bound_to_its_operator(api, login, traffic_camera):
    """Sharing a session id must not be a way to share footage."""
    owner = await login("traffic.operator")
    other = await login("municipal.operator")

    session = (await open_session(api, owner, traffic_camera["camera_id"])).json()

    stolen = await api.get(session["stream_url"], headers=other)
    assert stolen.status_code == 403


# 18. Video session creates an audit record
async def test_video_session_is_audited(api, login, admin_headers, traffic_camera):
    headers = await login("traffic.operator")
    session = (
        await open_session(
            api, headers, traffic_camera["camera_id"], case_id="CASE-DEMO-001"
        )
    ).json()
    await api.get(session["stream_url"], headers=headers)

    audit = (
        await api.get("/api/v1/audit?action=video_session_opened", headers=admin_headers)
    ).json()
    assert audit, "opening a session must be audited"

    entry = audit[0]
    assert entry["username"] == "traffic.operator"
    assert entry["resource_id"] == traffic_camera["camera_id"]
    assert entry["details"]["session_id"] == session["session_id"]
    assert entry["details"]["case_id"] == "CASE-DEMO-001"
    assert entry["case_or_reason"] == LIVE["reason"]

    reads = (
        await api.get("/api/v1/audit?action=video_stream_accessed", headers=admin_headers)
    ).json()
    assert reads, "the first read of a session must be audited"


async def test_denied_video_attempts_are_audited(api, login, admin_headers, municipal_camera):
    headers = await login("traffic.operator")
    await open_session(api, headers, municipal_camera["camera_id"])

    audit = (
        await api.get("/api/v1/audit?action=video_access_denied", headers=admin_headers)
    ).json()
    assert audit
    assert audit[0]["outcome"] == "denied"
    assert audit[0]["details"]["denial_reason"]


# 19. Response never exposes a raw RTSP URL or credentials
async def test_session_response_exposes_no_source_url_or_credential(
    api, login, traffic_camera
):
    headers = await login("traffic.operator")
    session = (await open_session(api, headers, traffic_camera["camera_id"])).json()

    assert_no_secrets(json.dumps(session), "video session response")

    # The only address the client receives is Vigentra's own opaque route.
    assert session["stream_url"].startswith("/api/v1/streams/")
    assert "http" not in session["stream_url"]

    status = await api.get(
        f"/api/v1/video-sessions/{session['session_id']}/status", headers=headers
    )
    assert_no_secrets(json.dumps(status.json()), "session status response")

    # And the stream response headers must not carry it either.
    stream = await api.get(session["stream_url"], headers=headers)
    assert_no_secrets(json.dumps(dict(stream.headers)), "stream response headers")


async def test_playback_window_is_validated(api, login, traffic_camera):
    from datetime import datetime, timedelta, timezone

    headers = await login("traffic.operator")
    now = datetime.now(timezone.utc)

    def iso(moment):
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Missing window
    response = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={
            "password": password_for_headers(headers),
            "camera_id": traffic_camera["camera_id"],
            "mode": "playback",
            "reason": "Authorized incident review",
        },
    )
    assert response.status_code == 422

    # Inverted window
    response = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={
            "password": password_for_headers(headers),
            "camera_id": traffic_camera["camera_id"],
            "mode": "playback",
            "reason": "Authorized incident review",
            "start_time_utc": iso(now - timedelta(minutes=5)),
            "end_time_utc": iso(now - timedelta(minutes=30)),
        },
    )
    assert response.status_code == 422

    # Valid five-minute window
    response = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={
            "password": password_for_headers(headers),
            "camera_id": traffic_camera["camera_id"],
            "mode": "playback",
            "reason": "Authorized incident review",
            "case_id": "CASE-DEMO-001",
            "start_time_utc": iso(now - timedelta(minutes=10)),
            "end_time_utc": iso(now - timedelta(minutes=5)),
        },
    )
    assert response.status_code == 201
    assert response.json()["mode"] == "playback"
    # Playback gets a longer ceiling than live (900s vs 300s), but the broker
    # clamps every session to the department's own ticket expiry when that is
    # sooner - a session that outlives its upstream ticket cannot fetch bytes.
    assert 0 < response.json()["expires_in_seconds"] <= 900


# 20. Missing official adapter returns a clear configuration state
async def test_official_provider_reports_not_configured():
    """No credentials were supplied, so the official path must refuse cleanly.

    Critically it must NOT attempt a request against a guessed endpoint.
    """
    from app.config import get_settings
    from app.providers.base import ProviderNotConfigured
    from app.providers.official import OfficialVigentraProvider

    settings = get_settings()
    assert settings.official_provider_configured is False

    provider = OfficialVigentraProvider(settings)
    described = provider.describe()
    assert described["status"] == "SOURCE_ACCESS_NOT_CONFIGURED"
    assert described["configured"] is False
    assert described["endpoint"] is None

    with pytest.raises(ProviderNotConfigured) as excinfo:
        await provider.list_cameras()
    assert excinfo.value.code == "SOURCE_ACCESS_NOT_CONFIGURED"

    with pytest.raises(ProviderNotConfigured):
        await provider.create_video_session("any-camera", "live")


async def test_official_video_adapter_reports_not_configured():
    from app.config import get_settings
    from app.video_adapters import OfficialVigentraVideoAdapter, VideoNotConfigured

    settings = get_settings()
    config = settings.sources[0]
    adapter = OfficialVigentraVideoAdapter(config, settings)
    try:
        with pytest.raises(VideoNotConfigured) as excinfo:
            await adapter.create_session("any-camera", "live")
        assert excinfo.value.code == "SOURCE_ACCESS_NOT_CONFIGURED"
        assert "mock/demo mode" in excinfo.value.message
    finally:
        await adapter.aclose()


# ---------------------------------------------------------------------------
# Platform and oversight accounts
# ---------------------------------------------------------------------------

async def test_system_admin_must_still_ask_the_owning_unit(
    api, login, traffic_camera, municipal_camera
):
    """Being the platform account is not a way around the owning unit.

    The system admin is statewide and holds every video permission, which used
    to put it straight through on any camera. It no longer does: a central
    account never counts as "inside the unit", so the request reaches a human
    on both departments before any footage moves.
    """
    headers = await login("system.admin")

    for camera in (traffic_camera, municipal_camera):
        response = await open_session(api, headers, camera["camera_id"])
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["state"] == "needs_unit_approval"


async def test_system_admin_refusal_is_audited_like_anyone_else(api, login, traffic_camera):
    """No quiet path for the privileged account - including when it is refused.

    A refusal that leaves no trace is how a privileged account gets probed
    without anyone noticing, so the denial is recorded as carefully as a grant.
    """
    headers = await login("system.admin")
    refused = await open_session(api, headers, traffic_camera["camera_id"])
    assert refused.status_code == 403

    entries = (await api.get("/api/v1/audit", headers=await login("auditor"))).json()
    mine = [
        entry for entry in entries
        if entry["username"] == "system.admin" and entry["action"] == "video_access_denied"
    ]
    assert mine, "a refused system admin request must appear in the audit trail"


async def test_oversight_roles_stay_metadata_only_by_default(api, login, traffic_camera):
    """State and city admins are not viewing accounts unless switched on."""
    for username in ("state.admin", "ahmedabad.cityadmin"):
        headers = await login(username)
        response = await open_session(api, headers, traffic_camera["camera_id"])
        assert response.status_code == 403, f"{username} should not hold video by default"


async def test_state_admin_opt_in_still_cannot_bypass_the_owning_unit(
    monkeypatch, traffic_camera
):
    """A deployment flag cannot stand in for the owner's consent.

    `VIGENTRA_STATE_ADMIN_VIDEO` decides whether the role may hold footage at
    all. It deliberately does NOT decide whose footage: a central account still
    reaches every camera through the owning unit. Flipping a switch in a config
    file is not a person agreeing.
    """
    from app.config import Role, get_settings
    from app.services import video_permissions

    settings = get_settings()
    monkeypatch.setattr(settings, "vigentra_state_admin_video", True, raising=False)
    assert settings.role_video_opt_in(Role.STATE_ADMIN) is True
    assert settings.role_grants_video(Role.STATE_ADMIN) is True

    user = next(u for u in settings.demo_users if u.username == "state.admin")
    camera = SimpleNamespace(
        camera_id=traffic_camera["camera_id"],
        owning_department=traffic_camera["owning_department"],
        installation_status="COMMISSIONED",
        video_access_enabled=True,
        health_status="online",
        city=traffic_camera["location"]["city"],
        zone=traffic_camera["location"].get("zone"),
        capabilities=["metadata", "health", "live", "playback"],
    )
    # The flag opens the role gate...
    decision = video_permissions.evaluate(user, camera, settings)
    assert decision.allowed is False
    assert decision.state.value == "needs_unit_approval", decision.reason

    # ...and the owning unit's grant is what actually opens the footage.
    granted = video_permissions.evaluate(user, camera, settings, grant=["live"])
    assert granted.allowed is True, granted.reason
    assert "live" in granted.allowed_modes


# ---------------------------------------------------------------------------
# Recorded playback: windows, repetition, and the owner's limits
# ---------------------------------------------------------------------------

def _window(hours_ago: float, minutes: int = 5) -> dict:
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    start = end - timedelta(minutes=minutes)
    return {
        "mode": "playback",
        "start_time_utc": start.isoformat(),
        "end_time_utc": end.isoformat(),
        "reason": "Reviewing a reported incident on this approach",
    }


async def test_playback_returns_the_segment_for_the_window(api, login, traffic_camera):
    """A window has to change the footage, or the form is theatre."""
    headers = await login("traffic.operator")

    first = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={"camera_id": traffic_camera["camera_id"],
              "password": password_for_headers(headers), **_window(2)},
    )
    assert first.status_code == 201, first.text
    a = first.json()
    assert a["segment_start_seconds"] is not None
    assert a["segment_end_seconds"] > a["segment_start_seconds"]
    assert a["start_time_utc"] and a["end_time_utc"]

    second = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={"camera_id": traffic_camera["camera_id"],
              "password": password_for_headers(headers), **_window(30)},
    )
    assert second.status_code == 201
    b = second.json()
    assert b["segment_start_seconds"] != a["segment_start_seconds"], (
        "a different window must return different footage"
    )

    # And the archive is stable: the same window twice gives the same segment,
    # so two officers reviewing the same minutes see the same thing.
    repeat = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={"camera_id": traffic_camera["camera_id"],
              "password": password_for_headers(headers), **_window(30)},
    )
    assert repeat.json()["segment_start_seconds"] == b["segment_start_seconds"]


@requires_demo_clips
async def test_playback_can_be_requested_repeatedly(api, login, traffic_camera):
    """No quota. The limits are the owner's, not a counter.

    Five different windows back to back, each one a fresh audited session.
    """
    headers = await login("traffic.operator")
    seen = []
    for hours in (1, 3, 6, 12, 24):
        response = await api.post(
            "/api/v1/video-sessions",
            headers=headers,
            json={"camera_id": traffic_camera["camera_id"],
              "password": password_for_headers(headers), **_window(hours)},
        )
        assert response.status_code == 201, f"{hours}h window refused: {response.text}"
        body = response.json()
        stream = await api.get(body["stream_url"], headers=headers)
        assert stream.status_code == 200
        seen.append(body["session_id"])

    assert len(set(seen)) == 5, "each request must be its own session"


async def test_playback_beyond_retention_is_refused_with_the_reason(
    api, login, traffic_camera
):
    """The committee's retention is the restriction, and it says so.

    Not a 403: the footage is not being withheld, it no longer exists. Telling
    an operator "denied" would send them to argue with the wrong people.
    """
    headers = await login("traffic.operator")
    retention = traffic_camera["technical_summary"]["retention_days"]
    assert retention, "fixture camera should declare a retention period"

    response = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={
            "password": password_for_headers(headers),
            "camera_id": traffic_camera["camera_id"],
            **_window(hours_ago=(retention + 5) * 24),
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_PLAYBACK_WINDOW"
    assert "retains" in detail["message"]


async def test_playback_requires_both_ends_of_the_window(api, login, traffic_camera):
    headers = await login("traffic.operator")
    response = await api.post(
        "/api/v1/video-sessions",
        headers=headers,
        json={
            "password": password_for_headers(headers),
            "camera_id": traffic_camera["camera_id"],
            "mode": "playback",
            "reason": "Missing the end of the window",
        },
    )
    assert response.status_code == 422
