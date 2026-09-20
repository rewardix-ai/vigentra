"""Send watchlist alerts to a system outside the console.

An alert that only ever appears on a screen depends on someone watching the screen. One signed
HTTPS POST per ingest batch is the smallest honest outbound channel: a control room's dispatch
system, an SMS gateway or a messaging bot sits behind it, and none of them is this platform's
business. Off unless `ALERT_WEBHOOK_URL` is set.

The body is signed (`X-Vigentra-Signature: sha256=<hex>`, HMAC-SHA256 of the raw body with
`ALERT_WEBHOOK_SECRET`) so the receiver can refuse anything this platform did not send. Delivery
never delays or fails an ingest: it runs after the commit, off the request, and a receiver that
is down costs a log line, not an alert - the alert is already in the database and the audit log.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging

import httpx

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()   # a task nobody holds may be collected before it runs


def priority_of(category: str, exact: bool) -> str:
    """Same rule as schemas.AlertOut.priority; an exact stolen or wanted read is the one to act on."""
    if not exact:
        return "review"
    return "critical" if category in ("stolen", "wanted") else "high"


def build_body(alerts) -> bytes:
    rows = [
        {
            "alert_id": a.alert_id, "priority": priority_of(a.category, a.exact),
            "category": a.category, "watch_plate": a.watch_plate, "seen_plate": a.seen_plate,
            "exact": a.exact, "distance": a.distance, "camera_id": a.camera_id,
            "timestamp_utc": a.timestamp_utc.isoformat() if a.timestamp_utc else None,
        }
        for a in alerts
    ]
    return json.dumps({"event": "watchlist_alerts", "alerts": rows}, sort_keys=True).encode()


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def deliver(url: str, secret: str, body: bytes, *, client: httpx.AsyncClient | None = None) -> bool:
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Vigentra-Signature"] = sign(body, secret)
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=5.0) as own:
                response = await own.post(url, content=body, headers=headers)
        else:
            response = await client.post(url, content=body, headers=headers)
        response.raise_for_status()
        return True
    except Exception as exc:  # a receiver that is down must never cost an ingest
        logger.warning("alert webhook not delivered (%s); the alerts are in the console and the audit log", exc)
        return False


def notify(settings, alerts) -> None:
    """Fire and forget, after the commit. No URL configured, or nothing raised: nothing happens."""
    url = getattr(settings, "alert_webhook_url", "") or ""
    if not url or not alerts:
        return
    task = asyncio.create_task(deliver(url, getattr(settings, "alert_webhook_secret", "") or "", build_body(alerts)))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
