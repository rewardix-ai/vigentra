"""Cross-unit video access requests.

Metadata now federates with no approval step: a unit fills the installation
form, its own validation passes, and the camera appears in the central
registry for everyone in scope to see. Footage did not become free in the
process — it moved behind this flow instead.

The guarantee under test: seeing a camera in the registry gives you nothing
but the right to *ask*. The unit that owns the camera decides, the answer is
time-boxed, and either side can end it.
"""
from __future__ import annotations

import pytest

from conftest import open_video_session

pytestmark = pytest.mark.asyncio


LIVE = {"mode": "live", "reason": "Cross-unit incident follow-up"}
REASON = "Chain-snatching follow-up on Vasna approach, FIR 214/2026"


async def open_session(api, headers, camera_id: str, **overrides):
    return await open_video_session(api, headers, camera_id, **{**LIVE, **overrides})


async def ask(api, headers, camera_id: str, **overrides) -> dict:
    response = await api.post(
        "/api/v1/video-access-requests",
        headers=headers,
        json={"camera_id": camera_id, "reason": REASON, **overrides},
    )
    response.raise_for_status()
    return response.json()


async def decide(api, headers, grant_id: str, verdict: str, **body):
    return await api.post(
        f"/api/v1/video-access-requests/{grant_id}/{verdict}",
        headers=headers,
        json=body,
    )


# ---------------------------------------------------------------------------
# The registry is visible; the footage is not
# ---------------------------------------------------------------------------

async def test_metadata_is_visible_without_any_grant(api, login, municipal_camera):
    """A Traffic operator can read a Municipal camera's record in full detail."""
    headers = await login("traffic.operator")
    response = await api.get(
        f"/api/v1/cameras/{municipal_camera['camera_id']}", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["owning_department"] == "Municipal Corporation"
    assert body["location"]["latitude"] is not None


async def test_video_without_a_grant_says_to_ask_the_unit(api, login, municipal_camera):
    """The refusal has to be actionable, not just a wall.

    An operator told only "denied" files a ticket. An operator told the owning
    unit decides this goes and asks them.
    """
    headers = await login("traffic.operator")
    response = await open_session(api, headers, municipal_camera["camera_id"])
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["state"] == "needs_unit_approval"
    assert "Municipal Corporation" in str(detail)


# ---------------------------------------------------------------------------
# The request flow
# ---------------------------------------------------------------------------

async def test_grant_opens_video_for_the_requester_only(api, login, municipal_camera):
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    owner = await login("municipal.state")

    grant = await ask(api, requester, camera_id)
    assert grant["status"] == "requested"
    assert grant["is_active"] is False

    approved = await decide(api, owner, grant["grant_id"], "grant", note="FIR verified")
    assert approved.status_code == 200
    assert approved.json()["status"] == "granted"
    assert approved.json()["is_active"] is True

    opened = await open_session(api, requester, camera_id)
    assert opened.status_code == 201, opened.text

    # The grant is personal. A colleague in the same requesting department did
    # not ask, was not named, and gets nothing.
    colleague = await login("traffic.zone3")
    assert (await open_session(api, colleague, camera_id)).status_code == 403


async def test_denial_leaves_video_shut(api, login, municipal_camera):
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    owner = await login("municipal.state")

    grant = await ask(api, requester, camera_id)
    refused = await decide(
        api, owner, grant["grant_id"], "deny", note="No case reference supplied"
    )
    assert refused.status_code == 200
    assert refused.json()["status"] == "denied"

    assert (await open_session(api, requester, camera_id)).status_code == 403


async def test_only_the_owning_unit_may_decide(api, login, municipal_camera):
    """The requesting side approving its own request would be the whole hole."""
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    grant = await ask(api, requester, camera_id)

    # A Traffic operator holds `video:grant`, but not over Municipal cameras.
    traffic_side = await login("traffic.state")
    assert (await decide(api, traffic_side, grant["grant_id"], "grant")).status_code == 403

    # And the requester cannot self-serve: no `video:grant` permission at all.
    assert (await decide(api, requester, grant["grant_id"], "grant")).status_code == 403


async def test_a_grant_can_be_narrowed_on_the_way_through(api, login, municipal_camera):
    """Asking for live and playback does not mean receiving both."""
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    owner = await login("municipal.state")

    grant = await ask(api, requester, camera_id, modes=["live", "playback"])
    approved = await decide(
        api, owner, grant["grant_id"], "grant", modes=["playback"], days=2
    )
    assert approved.status_code == 200
    assert approved.json()["allowed_modes"] == ["playback"]

    live = await open_session(api, requester, camera_id, mode="live")
    assert live.status_code == 403, "live was not granted"


async def test_revocation_ends_a_live_grant(api, login, municipal_camera):
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    owner = await login("municipal.state")

    grant = await ask(api, requester, camera_id)
    await decide(api, owner, grant["grant_id"], "grant")
    assert (await open_session(api, requester, camera_id)).status_code == 201

    revoked = await api.delete(
        f"/api/v1/video-access-requests/{grant['grant_id']}", headers=owner
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    assert (await open_session(api, requester, camera_id)).status_code == 403


async def test_asking_for_your_own_unit_is_refused(api, login, traffic_camera):
    """Not an error to punish — a request that would mean nothing.

    Access inside the owning unit is already settled by department, city and
    zone scope. A grant on top of that would be a second, weaker answer to a
    question already decided.
    """
    headers = await login("traffic.operator")
    response = await api.post(
        "/api/v1/video-access-requests",
        headers=headers,
        json={"camera_id": traffic_camera["camera_id"], "reason": REASON},
    )
    assert response.status_code == 400
    assert "own unit" in response.json()["detail"]


async def test_duplicate_requests_are_refused(api, login, municipal_camera):
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    await ask(api, requester, camera_id)

    again = await api.post(
        "/api/v1/video-access-requests",
        headers=requester,
        json={"camera_id": camera_id, "reason": REASON},
    )
    assert again.status_code == 409


async def test_a_reason_is_mandatory(api, login, municipal_camera):
    """Every viewing of another unit's footage has to be explicable later."""
    requester = await login("traffic.operator")
    response = await api.post(
        "/api/v1/video-access-requests",
        headers=requester,
        json={"camera_id": municipal_camera["camera_id"], "reason": ""},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Visibility of the queue itself
# ---------------------------------------------------------------------------

async def test_each_side_sees_the_request_and_nobody_else_does(
    api, login, municipal_camera
):
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    owner = await login("municipal.state")
    grant = await ask(api, requester, camera_id)

    async def ids(headers) -> set[str]:
        response = await api.get("/api/v1/video-access-requests", headers=headers)
        response.raise_for_status()
        return {item["grant_id"] for item in response.json()}

    assert grant["grant_id"] in await ids(requester), "the requester must see their ask"
    assert grant["grant_id"] in await ids(owner), "the owning unit must see its queue"

    # An unrelated Traffic account is party to neither side.
    assert grant["grant_id"] not in await ids(await login("traffic.zone3"))


async def test_the_decision_is_audited(api, login, municipal_camera):
    camera_id = municipal_camera["camera_id"]
    requester = await login("traffic.operator")
    owner = await login("municipal.state")

    grant = await ask(api, requester, camera_id)
    await decide(api, owner, grant["grant_id"], "grant", note="Verified with control room")

    audit = await api.get(
        "/api/v1/audit", headers=await login("auditor"), params={"limit": 200}
    )
    audit.raise_for_status()
    entries = audit.json()

    actions = {
        entry["action"] for entry in entries
        if entry.get("resource_id") == grant["grant_id"]
    }
    assert "video_access_requested" in actions
    assert "video_access_granted" in actions


async def test_an_oversight_account_can_neither_see_nor_revoke_another_units_request(
    api, login, traffic_camera
):
    """Department scope alone let a statewide auditor read every unit's requests
    and revoke another unit's grant; ownership needs the grant permission."""
    requester = await login("municipal.operator")
    grant = await ask(api, requester, traffic_camera["camera_id"])

    auditor = await login("auditor")
    seen = (await api.get("/api/v1/video-access-requests", headers=auditor)).json()
    assert grant["grant_id"] not in {g["grant_id"] for g in seen}
    refused = await api.delete(
        f"/api/v1/video-access-requests/{grant['grant_id']}", headers=auditor
    )
    assert refused.status_code == 403

    # The owning unit still sees it, and the requester can still withdraw it.
    owner = await login("traffic.state")
    owners_view = (await api.get("/api/v1/video-access-requests", headers=owner)).json()
    assert grant["grant_id"] in {g["grant_id"] for g in owners_view}
    withdrawn = await api.delete(
        f"/api/v1/video-access-requests/{grant['grant_id']}", headers=requester
    )
    assert withdrawn.status_code == 200, withdrawn.text
