"""Generic events: a list refresh pulls every department system it may read."""
from __future__ import annotations


async def test_refreshing_events_from_both_departments_answers(api, admin_headers):
    """Smoke test for the refresh path, which pulls every department at once.

    It does NOT reproduce the race fixed in routers/events.py (two sources
    upserting on one AsyncSession concurrently): with the seeded mock data the
    writes never overlap, so this passes against the old code too.
    """
    response = await api.get(
        "/api/v1/events", headers=admin_headers, params={"refresh": "true", "since_hours": 24}
    )
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)
