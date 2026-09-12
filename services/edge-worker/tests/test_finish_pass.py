"""The end of a pass must ship the plates it just settled.

A track's verdict exists only once its bank closes, so the engine hands over
its plates from finish() *after* the last frame. For most of today the worker
flushed `pending` first and drained second, which meant every settled plate was
appended to a list that nothing ever sent. The log said "4 plates", the
registry recorded none, and nothing raised - a dropped list is silent.

Order is the invariant, so `_finish_pass` owns both steps and these tests drive
it directly. Unit-testing the drain alone would pass with the order reversed,
which is exactly how the bug survived four other test files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.anpr_engine import PlateSighting  # noqa: E402
from app.worker import _finish_pass  # noqa: E402


class FakeClient:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def ingest(self, payload):
        self.batches.append([dict(p) for p in payload])
        return {"accepted": len(payload)}


class FakeAnpr:
    name = "vigentra-anpr"
    version = "test/reader"

    def __init__(self, sightings=(), raises=False) -> None:
        self._sightings = list(sightings)
        self._raises = raises
        self.finished = 0

    def finish(self):
        self.finished += 1
        if self._raises:
            raise RuntimeError("engine fault")
        return self._sightings


def sighting(text: str, track: int = 1) -> PlateSighting:
    """A settled reading, with every field the dataclass requires."""
    return PlateSighting(
        track_id=track,
        text=text,
        score=0.82,
        confidence=0.82,
        observations=12,
        confirmed=True,
        state=None,
        plate_format="standard",
        plate_bbox=[40.0, 90.0, 96.0, 110.0],
        vehicle_bbox=[10.0, 20.0, 110.0, 140.0],
        captured_at=1234.5,
        frame_index=12,
    )


def run(client=None, pending=None, anpr=None) -> int:
    return _finish_pass(
        client,
        pending if pending is not None else [],
        anpr,
        camera_id="VIGENTRA-TRAFFIC-BHA-CAM06",
        source_mode="demo_local",
        base_provenance={},
    )


def texts_sent(client: FakeClient) -> list[str]:
    """Every plate reading in what was actually ingested.

    `plate_text` is the field _sighting_payload puts the reading in. Searching
    for any key containing "plate" also matched provenance's plate_format
    ("standard"), which is metadata, not a reading.
    """
    return [row["plate_text"] for batch in client.batches for row in batch
            if row.get("plate_text")]


def test_settled_plates_are_actually_sent():
    """The regression. Flush-before-drain leaves this list empty."""
    client, pending = FakeClient(), []
    n = run(client, pending, FakeAnpr([sighting("GJ23H1546", 1), sighting("GJ11CK1044", 2)]))

    assert n == 2
    assert client.batches, "nothing was ingested at all"
    assert sorted(texts_sent(client)) == ["GJ11CK1044", "GJ23H1546"]


def test_plates_ride_along_with_the_detections_of_the_same_pass():
    client, pending = FakeClient(), [{"class_name": "car"}]
    run(client, pending, FakeAnpr([sighting("GJ23H1546")]))

    assert len(client.batches) == 1, "detections and plates should go in one batch"
    assert len(client.batches[0]) == 2


def test_detections_still_flush_when_there_is_no_engine():
    client, pending = FakeClient(), [{"class_name": "car"}]
    assert run(client, pending, None) == 0
    assert len(client.batches) == 1


def test_an_engine_fault_does_not_cost_the_detections():
    """ANPR failing is never a reason to lose the vehicles of that pass."""
    client, pending = FakeClient(), [{"class_name": "car"}]
    assert run(client, pending, FakeAnpr(raises=True)) == 0
    assert len(client.batches) == 1


def test_nothing_to_send_means_no_call():
    client = FakeClient()
    assert run(client, [], FakeAnpr([])) == 0
    assert client.batches == []


def test_pending_is_cleared_so_a_cycle_does_not_resend():
    client, pending = FakeClient(), [{"class_name": "car"}]
    run(client, pending, FakeAnpr([sighting("GJ23H1546")]))
    assert pending == [], "a --forever worker would resend this batch every cycle"


def test_a_dry_run_still_drains_the_engine():
    """No client, but the engine must not keep the pass's tracks open."""
    anpr = FakeAnpr([sighting("GJ23H1546")])
    assert run(None, [], anpr) == 1
    assert anpr.finished == 1
