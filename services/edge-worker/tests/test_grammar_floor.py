"""A reading that names a district or state which does not exist is not a plate.

The engine scores every settled reading against the Indian plate grammar and
records it as `grammar_prior`. A low prior means the string parses as *some*
format but names something that cannot exist: "SS" is not a state, Delhi has no
district 20 or 22. Those readings are indistinguishable from real ones on a
screen, which makes them the single output this system must never produce.

Measured on live cam01/cam06 footage on 2026-09-12: GJ11CK1044 1.000,
GJ32AG2883 1.000, GJ23H1546 0.500, DL4GC1009 0.245 - against SS77D45 0.004,
DL205 0.006, MP117 0.021, GJ0998 0.030, DL225356 0.032, HP777890 0.105.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.anpr_engine as engine  # noqa: E402


def record(plate: str, prior: float, status: str = "CONFIRMED", conf: float = 0.9) -> dict:
    return {
        "track_id": "cam01_s0_t1",
        "plate": plate,
        "status": status,
        "confidence": conf,
        "grammar_prior": prior,
        "frames_fused": 12,
        "bbox": [1.0, 2.0, 3.0, 4.0],
        "vehicle_box": [0.0, 0.0, 9.0, 9.0],
        "last_seen_pts_ms": 1000.0,
        "best_frame": 3,
    }


class Pipeline:
    def __init__(self, records):
        self.records = list(records)

    def flush(self):
        pass


def drain(records):
    eng = engine.AnprEngine.__new__(engine.AnprEngine)
    eng.camera_id = "cam01"
    eng.min_score = 0.55
    eng._pipeline = Pipeline(records)
    eng._emitted = set()
    eng._pending = []
    return [s.text for s in eng.finish()]


@pytest.mark.parametrize("plate,prior", [
    ("GJ11CK1044", 1.000), ("GJ32AG2883", 1.000),
    ("GJ23H1546", 0.500), ("DL4GC1009", 0.245),
])
def test_real_registrations_survive(plate, prior):
    assert drain([record(plate, prior)]) == [plate]


@pytest.mark.parametrize("plate,prior", [
    ("SS77D45", 0.004), ("DL205", 0.006), ("MP117", 0.021),
    ("GJ0998", 0.030), ("DL225356", 0.032), ("HP777890", 0.105),
])
def test_impossible_registrations_are_dropped(plate, prior):
    """These parse as a format but name a state or district that cannot exist."""
    assert drain([record(plate, prior)]) == []


def test_a_record_without_a_prior_is_not_dropped():
    """Older engines do not set grammar_prior; absence must not mean zero."""
    rec = record("GJ11CK1044", 0.0)
    del rec["grammar_prior"]
    assert drain([rec]) == ["GJ11CK1044"]


def test_the_floor_applies_to_confirmed_readings_too():
    """A confirm cannot launder an impossible registration."""
    assert drain([record("SS77D45", 0.004, status="CONFIRMED", conf=0.99)]) == []


def test_a_dropped_reading_says_so_in_the_log(caplog):
    """Silence is the failure mode this floor could otherwise introduce.

    A pass that reads four plates and emits none must not look identical in the
    log to a pass that read nothing - that ambiguity is what made "0 plates"
    take hours to diagnose.
    """
    import logging

    with caplog.at_level(logging.INFO):
        assert drain([record("SS77D45", 0.004)]) == []

    assert any("SS77D45" in r.getMessage() for r in caplog.records), (
        "a discarded reading left no trace in the log"
    )
