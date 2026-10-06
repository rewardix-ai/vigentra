"""Camera-health alerts: a camera that stops is told to someone, once, and closes itself."""
from __future__ import annotations

import asyncio

import pytest


async def _sweep(settings=None):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.main import app as central_app
    from app.services import health_monitor

    return await health_monitor.poll_once(get_session_factory(), central_app.state.adapters,
                                          settings or get_settings())


def _adapter(source_system: str):
    from app.main import app as central_app

    return next(a for a in central_app.state.adapters.values() if a.source_system == source_system)


@pytest.fixture(autouse=True)
def _fresh_streaks():
    from app.services import health_alerts

    health_alerts.reset()
    yield
    health_alerts.reset()


async def _alerts(api, headers, **params):
    response = await api.get("/api/v1/health-alerts", headers=headers, params=params)
    response.raise_for_status()
    return response.json()


async def test_a_camera_down_for_two_checks_raises_one_alert_and_closes_itself(api, login, traffic_camera, monkeypatch):
    adapter = _adapter(traffic_camera["source_system"])
    real = adapter.get_camera_health
    down = {"on": True}

    async def health(external_id):
        if down["on"] and external_id == traffic_camera["external_camera_id"]:
            return {"status": "offline", "detail": {"reason": "no frames"}}
        return await real(external_id)

    monkeypatch.setattr(adapter, "get_camera_health", health)
    operator = await login("traffic.operator")

    await _sweep()
    assert await _alerts(api, operator) == []                     # one failed check is a blip
    await _sweep()
    await _sweep()                                                # still down: no second alert
    alerts = await _alerts(api, operator)
    assert [a["kind"] for a in alerts] == ["CAMERA_OFFLINE"]
    assert alerts[0]["camera_id"] == traffic_camera["camera_id"] and alerts[0]["open"] is True
    count = (await api.get("/api/v1/health-alerts/open-count", headers=operator)).json()
    assert count == {"open": 1}

    down["on"] = False
    await _sweep()
    (closed,) = await _alerts(api, operator)
    assert closed["open"] is False and closed["recovered_at"]
    assert await _alerts(api, operator, open_only=True) == []

    audit = (await api.get("/api/v1/audit", headers=await login("auditor"))).json()
    actions = {row["action"] for row in (audit["items"] if isinstance(audit, dict) else audit)}
    assert {"health_alert_raised", "health_alert_recovered"} <= actions


async def test_an_unreachable_department_system_is_one_alert_not_one_per_camera(api, login, traffic_camera, monkeypatch):
    adapter = _adapter(traffic_camera["source_system"])
    real = adapter.check_source_health
    down = {"on": True}

    async def probe():
        return {"reachable": False, "error": "connection refused"} if down["on"] else await real()

    monkeypatch.setattr(adapter, "check_source_health", probe)
    admin = await login("system.admin")
    await _sweep()
    await _sweep()
    alerts = await _alerts(api, admin)
    assert [a["kind"] for a in alerts] == ["SOURCE_UNREACHABLE"]
    assert alerts[0]["source_system"] == traffic_camera["source_system"]

    down["on"] = False
    await _sweep()
    assert all(not a["open"] for a in await _alerts(api, admin))


async def test_who_may_take_an_alert_up(api, login, traffic_camera, monkeypatch):
    adapter = _adapter(traffic_camera["source_system"])
    real = adapter.get_camera_health

    async def health(external_id):
        if external_id == traffic_camera["external_camera_id"]:
            return {"status": "offline", "detail": {}}
        return await real(external_id)

    monkeypatch.setattr(adapter, "get_camera_health", health)
    await _sweep()
    await _sweep()
    auditor = await login("auditor")
    (alert,) = await _alerts(api, auditor)                        # oversight sees it...
    refused = await api.post(f"/api/v1/health-alerts/{alert['alert_id']}/acknowledge", headers=auditor, json={})
    assert refused.status_code == 403                             # ...but does not close it
    noc = await login("health.monitor")
    taken = await api.post(f"/api/v1/health-alerts/{alert['alert_id']}/acknowledge", headers=noc,
                           json={"note": "Field team sent"})
    assert taken.status_code == 200
    body = taken.json()
    assert body["acknowledged_by"] == "health.monitor" and body["open"] is True   # still down until it answers


async def test_the_signed_webhook_carries_health_alerts(api, traffic_camera, monkeypatch):
    from app.config import get_settings
    from app.services import alert_webhook

    sent = []

    async def deliver(url, secret, body, **_):
        sent.append(body)
        return True

    monkeypatch.setattr(alert_webhook, "deliver", deliver)
    adapter = _adapter(traffic_camera["source_system"])
    real = adapter.get_camera_health

    async def health(external_id):
        if external_id == traffic_camera["external_camera_id"]:
            return {"status": "offline", "detail": {}}
        return await real(external_id)

    monkeypatch.setattr(adapter, "get_camera_health", health)
    settings = get_settings().model_copy(update={"alert_webhook_url": "https://dispatch.example/hook",
                                                 "alert_webhook_secret": "s"})
    await _sweep(settings)
    await _sweep(settings)
    await asyncio.sleep(0)
    assert sent and b'"event": "camera_health_alerts"' in sent[0] and b'"state": "down"' in sent[0]
