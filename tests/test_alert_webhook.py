"""The outbound alert channel: signed, off by default, and never able to fail an ingest."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "central-api"))

from app.services import alert_webhook  # noqa: E402


def _alert(category="stolen", exact=True):
    return SimpleNamespace(alert_id="al-1", category=category, watch_plate="GJ01AB1234",
                           seen_plate="GJ01AB1234" if exact else "GJ01A81234", exact=exact,
                           distance=0.0 if exact else 0.35, camera_id="CAM-1",
                           timestamp_utc=datetime(2026, 9, 20, tzinfo=timezone.utc))


def test_the_body_carries_the_priority_an_operator_acts_on():
    rows = json.loads(alert_webhook.build_body([_alert(), _alert("suspect"), _alert(exact=False)]))["alerts"]
    assert [r["priority"] for r in rows] == ["critical", "high", "review"]


def test_the_signature_is_an_hmac_of_the_exact_body():
    body = alert_webhook.build_body([_alert()])
    expected = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert alert_webhook.sign(body, "s3cret") == expected


@pytest.mark.asyncio
async def test_delivery_posts_the_signed_body_and_a_dead_receiver_costs_nothing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sig"], seen["body"] = request.headers["X-Vigentra-Signature"], request.content
        return httpx.Response(204)

    body = alert_webhook.build_body([_alert()])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await alert_webhook.deliver("https://dispatch.example/hook", "k", body, client=client)
    assert seen["body"] == body and seen["sig"] == alert_webhook.sign(body, "k")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        assert await alert_webhook.deliver("https://dispatch.example/hook", "k", body, client=client) is False


def test_no_url_means_no_task():
    alert_webhook.notify(SimpleNamespace(alert_webhook_url="", alert_webhook_secret=""), [_alert()])
    assert not alert_webhook._tasks
