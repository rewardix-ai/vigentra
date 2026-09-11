"""The grid adapter's sign-in gate under a flaky gateway.

The gateway keeps one session per account and the HLS stream proxy shares the
adapter's httpx client, so how the gate treats a failed sign-in decides whether
live video keeps playing. These pin the two rules a regression broke:

  * a transport error or 5xx on /auth/login must not discard a session that
    still works, and must not wedge the grid behind a 60 s backoff;
  * a genuine refusal (200 + the login page, no Set-Cookie) must be caught and
    must hold off further attempts.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


def _build(handler):
    from app.adapters import build_adapters
    from app.config import Settings

    # conftest points the two departments at their own mock adapters; force the
    # grid adapter here (only it has the sign-in gate), and one source is enough.
    settings = Settings(
        sentinel_grid_password="pw",
        sentinel_grid_email="a@b.c",
        traffic_vms_adapter="grid_adapter",
        municipal_vms_enabled=False,
    )
    client = httpx.AsyncClient(
        base_url="https://grid.test", transport=httpx.MockTransport(handler)
    )
    adapters = build_adapters(settings, clients={s.source_system: client for s in settings.sources})
    adapter = next(a for a in adapters.values() if hasattr(a, "_gate"))
    return adapter, client


async def test_a_5xx_signin_keeps_a_working_session_and_does_not_wedge_the_grid():
    from app.adapters.base import AdapterError

    phase = {"v": "ok"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            if phase["v"] == "ok":
                return httpx.Response(200, headers={"set-cookie": "sid=good; Path=/"}, text="ok")
            return httpx.Response(502, text="bad gateway")
        if req.url.path == "/cameras.json":
            ok = "sid=good" in req.headers.get("cookie", "")
            return httpx.Response(200, json=[{"id": "cam01", "name": "C1"}]) if ok else httpx.Response(403, json={})
        return httpx.Response(404)

    adapter, client = _build(handler)
    try:
        await adapter._catalogue()  # warm: obtains the good cookie
        gate = adapter._gate()
        gate._authenticated = False  # force a re-auth on the next call
        phase["v"] = "login_5xx"
        with pytest.raises(AdapterError):
            await gate.ensure(source_system=adapter.source_system, credential="pw", identity="a@b.c")
        # The cookie the still-valid session rides on must survive.
        assert any(c.name == "sid" for c in client.cookies.jar)
        # A transient 5xx is not a refusal, so it must not arm the 60 s backoff.
        assert gate._recent_failure() is None
    finally:
        await client.aclose()


async def test_a_refused_signin_is_caught_and_backs_off():
    from app.adapters.base import SourceAuthError

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/auth/login":
            return httpx.Response(200, text="<html>login</html>")  # 200, no Set-Cookie
        return httpx.Response(403, json={})

    adapter, client = _build(handler)
    try:
        gate = adapter._gate()
        with pytest.raises(SourceAuthError):
            await gate.ensure(source_system=adapter.source_system, credential="bad", identity="a@b.c")
        # A genuine refusal arms the backoff so one bad credential is not a storm.
        assert gate._recent_failure() is not None
    finally:
        await client.aclose()
