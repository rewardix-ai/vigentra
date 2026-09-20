"""Ingest and watchlist matching at volume, measured.

2,000 plate reads in worker-sized batches against a 200-entry watchlist: every row accepted, the one
watched vehicle alerted exactly once, and the throughput printed (`pytest -s`). This is the figure
docs/scalability.md quotes for one API process; it runs on SQLite in-process, so PostgreSQL behind a
real network will differ - it is a floor for the matcher and the write path, not a cluster benchmark.
"""
from __future__ import annotations

import time

from test_watchlist import add_watch, plate_detection

READS, BATCH, WATCHED = 2000, 50, 200


def _plate(i: int) -> str:
    return f"GJ{i % 38 + 1:02d}{chr(65 + i % 26)}{chr(65 + (i // 26) % 26)}{1000 + i % 9000:04d}"


async def test_two_thousand_reads_against_two_hundred_watched_plates(api, login, traffic_camera):
    watcher = await login("traffic.state")
    for i in range(WATCHED - 1):
        (await add_watch(api, watcher, _plate(100_000 + i * 7))).raise_for_status()
    (await add_watch(api, watcher, "MH12DE1433")).raise_for_status()

    edge = await login("traffic.ai")
    rows = [plate_detection(traffic_camera["camera_id"], _plate(i), detection_id=f"vol-{i}") for i in range(READS)]
    rows[1234] = plate_detection(traffic_camera["camera_id"], "MH12DE1433", detection_id="vol-1234")

    accepted, started = 0, time.perf_counter()
    for at in range(0, READS, BATCH):
        response = await api.post("/api/v1/detections/ingest", headers=edge, json={"detections": rows[at:at + BATCH]})
        response.raise_for_status()
        accepted += response.json()["accepted"]
    seconds = time.perf_counter() - started

    assert accepted == READS
    alerts = (await api.get("/api/v1/alerts", headers=watcher)).json()
    assert [a["seen_plate"] for a in alerts if a["exact"]] == ["MH12DE1433"]
    print(f"\ningest: {READS} plate reads, {WATCHED} watched plates, {seconds:.1f}s = {READS / seconds:.0f} reads/s")
