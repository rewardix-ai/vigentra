"""E-challan: a person confirms the offence and types the plate; the owner is looked up and an SMS goes.

The boundaries: only traffic enforcement issues challans, only offences can be challaned, the plate
must be a valid registration, one challan per incident, the full mobile number never leaves the
service, and every lookup and issue is audited. Owners here are the demo stand-in for VAHAN.
"""
import pytest

from tests.test_incidents import _submit


async def _incident(api, login, camera_id, kind="NO_HELMET"):
    edge = await login("traffic.ai")
    r = await _submit(api, edge, camera_id, kind=kind, severity="LOW")
    assert r.status_code == 200, r.text
    operator = await login("dept.admin")
    listed = (await api.get("/api/v1/incidents", headers=operator)).json()
    return next(i["incident_id"] for i in listed if i["kind"] == kind and i["camera_id"] == camera_id)


@pytest.mark.asyncio
async def test_confirming_with_a_plate_looks_up_the_owner_and_issues_a_challan(api, login, traffic_camera):
    inc = await _incident(api, login, traffic_camera["camera_id"])
    op = await login("dept.admin")
    look = await api.post(f"/api/v1/incidents/{inc}/challan/lookup", headers=op, json={"plate": "gj 01 ab 1234"})
    assert look.status_code == 200, look.text
    body = look.json()
    assert body["plate"] == "GJ01AB1234" and body["demo"] and body["fine_rupees"] == 1000
    assert body["mobile_masked"].startswith("+91 XXXXX") and "194D" in body["section"]

    issued = await api.post(f"/api/v1/incidents/{inc}/challan", headers=op, json={"plate": "GJ01AB1234"})
    assert issued.status_code == 201, issued.text
    c = issued.json()
    assert c["challan_no"].startswith("VG-") and c["sms_status"] == "SIMULATED"
    assert c["challan_no"] in c["sms_text"] and "GJ01AB1234" in c["sms_text"]
    assert c["owner_name"] == body["owner_name"]   # the same owner every time

    again = await api.post(f"/api/v1/incidents/{inc}/challan", headers=op, json={"plate": "GJ01AB1234"})
    assert again.status_code == 409
    listed = (await api.get("/api/v1/incidents", headers=op)).json()
    assert next(i for i in listed if i["incident_id"] == inc)["status"] == "CONFIRMED"
    audit = (await api.get("/api/v1/audit", headers=await login("auditor"), params={"limit": 50})).json()
    actions = {a["action"] for a in (audit["items"] if isinstance(audit, dict) else audit)}
    assert {"challan_owner_lookup", "challan_issued"} <= actions


@pytest.mark.asyncio
async def test_a_bad_plate_a_non_offence_and_municipal_accounts_are_refused(api, login, traffic_camera):
    inc = await _incident(api, login, traffic_camera["camera_id"])
    op = await login("dept.admin")
    bad = await api.post(f"/api/v1/incidents/{inc}/challan/lookup", headers=op, json={"plate": "HELLO123"})
    assert bad.status_code == 422
    crowd = await _incident(api, login, traffic_camera["camera_id"], kind="CROWD_GATHERING")
    r = await api.post(f"/api/v1/incidents/{crowd}/challan/lookup", headers=op, json={"plate": "GJ01AB1234"})
    assert r.status_code == 422
    muni = await login("municipal.operator")
    r = await api.post(f"/api/v1/incidents/{inc}/challan/lookup", headers=muni, json={"plate": "GJ01AB1234"})
    assert r.status_code == 403
