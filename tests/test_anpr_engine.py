"""The consensus ANPR engine, as the edge worker uses it.

These tests run against a fake pipeline rather than real weights, for the same
reason `test_grid_capture.py` runs against a fake capture: the behaviour that
matters here is what the worker does with the engine's output, and a rule only
checked when a 2 GB model is installed is not really checked at all.

`test_yolo_on_cctv.py` covers real inference, and skips without the extras.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

EDGE = Path(__file__).resolve().parent.parent / "services" / "edge-worker" / "app"

#: Both the worker and the central API use the package name `app`, and the
#: worker's modules import each other relatively. Synthesising a parent package
#: with a `__path__` gives them a real package to be relative to, without
#: putting either `app` on sys.path where they would shadow each other.
PACKAGE = "vigentra_edge"


def _load_edge(module_name: str):
    if PACKAGE not in sys.modules:
        parent = types.ModuleType(PACKAGE)
        parent.__path__ = [str(EDGE)]
        sys.modules[PACKAGE] = parent

    qualified = f"{PACKAGE}.{module_name}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(qualified, EDGE / f"{module_name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def engine_module():
    return _load_edge("anpr_engine")


# ---------------------------------------------------------------------------
# Fakes shaped like the vendored engine's output
# ---------------------------------------------------------------------------

@dataclass
class FakeFrame:
    """Stands in for `anpr.sources.frame_source.Frame`."""

    image: object = None
    pts_ms: float = 0.0
    frame_idx: int = 0
    camera_id: str = "cam01"
    discontinuity: bool = False


def record(**over) -> dict:
    """One finalised track, shaped as `ANPRPipeline.records` holds them."""
    rec = {
        "track_id": "cam01_s0_t7",
        "camera_id": "cam01",
        "status": "CONFIRMED",
        "plate": "GJ01AB1234",
        "confidence": 0.82,
        "frames_fused": 11,
        "n_plate_hits": 14,
        "plate_class": "standard",
        "bbox": [40.0, 90.0, 96.0, 110.0],
        "vehicle_box": [10.0, 20.0, 110.0, 140.0],
        "first_seen_pts_ms": 1000.0,
        "last_seen_pts_ms": 1234500.0,
        "best_frame": 12,
        "last_frame": 14,
        "reason": "",
        "quality": {},
    }
    rec.update(over)
    return rec


class FakeVehicles:
    def __init__(self) -> None:
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1


class FakePipeline:
    """Stands in for `anpr.pipeline.ANPRPipeline`.

    The shape that matters: `process_frame` returns nothing, vehicles for the
    frame just seen hang off `last_vehicles`, and a track's verdict only
    reaches `records` when `flush()` closes its bank.
    """

    def __init__(self, records=(), vehicles=()):
        self._on_flush = list(records)
        self.records: list = []
        self.last_vehicles = list(vehicles)
        self.vehicles = FakeVehicles()
        self.frames: list = []
        self.flushes = 0

    def process_frame(self, frame):
        self.frames.append(frame)

    def flush(self):
        self.flushes += 1
        # Closing the banks is what publishes a verdict.
        self.records.extend(self._on_flush)
        self._on_flush = []


def build(engine_module, records=(), vehicles=()):
    """An AnprEngine wrapping a fake pipeline, without touching real weights."""
    engine = engine_module.AnprEngine.__new__(engine_module.AnprEngine)
    engine.camera_id = "cam01"
    engine._pipeline = FakePipeline(records, vehicles)
    engine._Frame = FakeFrame
    engine._emitted = set()
    engine._pending = []
    engine._frames = 0
    engine._started = 0.0
    return engine


# ---------------------------------------------------------------------------
# What the engine emits
# ---------------------------------------------------------------------------

def test_a_confirmed_plate_is_emitted_once_per_track(engine_module):
    """A vehicle read forty times is one sighting, not forty."""
    engine = build(engine_module, records=[record()])

    for _ in range(4):
        _, plates = engine.process(None, captured_at=1.0)
        assert plates == [], "a plate settles per track, never per frame"

    settled = engine.finish()
    assert [p.text for p in settled] == ["GJ01AB1234"]
    assert settled[0].observations == 11
    assert settled[0].track_id == 7, "the tracker's own id, out of 'cam01_s0_t7'"


def test_a_settled_plate_is_not_re_emitted_on_the_next_pass(engine_module):
    """Records stay on the pipeline between passes; the sighting must not repeat."""
    engine = build(engine_module, records=[record()])
    assert len(engine.finish()) == 1
    assert engine.finish() == []


def test_an_unconfirmed_reading_is_withheld(engine_module):
    """Too few agreeing frames is not a reading, it is a guess."""
    engine = build(engine_module, records=[record(status="CANDIDATE")])
    assert engine.finish() == []


def test_a_low_scoring_vote_is_withheld(engine_module):
    """A low fused score means the frames disagreed - the worst case."""
    engine = build(engine_module, records=[record(confidence=0.30)])
    assert engine.finish() == []


def test_a_track_with_no_plate_is_not_a_sighting(engine_module):
    engine = build(engine_module, records=[record(status="UNREADABLE", plate=None)])
    assert engine.finish() == []


def test_capture_time_is_threaded_through_as_pts_milliseconds(engine_module):
    """PTS, never arrival time - route reconstruction depends on this number.

    The worker counts in seconds and the engine's Frame carries milliseconds.
    """
    engine = build(engine_module)
    engine.process(None, captured_at=987.25)
    assert [f.pts_ms for f in engine._pipeline.frames] == [987250.0]


def test_a_sighting_carries_the_pts_not_the_wall_clock(engine_module):
    engine = build(engine_module, records=[record(last_seen_pts_ms=4242000.0)])
    assert engine.finish()[0].captured_at == 4242.0


def test_a_discontinuity_resets_the_tracker_and_keeps_the_reading(engine_module):
    """The sandbox feeds loop, and at the loop point the scene cuts.

    Track ids must not survive the cut - carrying them splices two different
    vehicles into one plate history. The plate that had already settled must
    survive it, though: the reading was good before the scene changed.
    """
    engine = build(engine_module, records=[record()])
    engine.process(None, captured_at=1.0)
    assert engine._pipeline.vehicles.resets == 0

    _, at_cut = engine.process(None, captured_at=0.5, discontinuity=True)
    assert engine._pipeline.vehicles.resets == 1

    settled = at_cut + engine.finish()     # handed over at the cut itself, and only once
    assert [p.text for p in settled] == ["GJ01AB1234"], "a cut must not eat a reading"


def test_vehicles_outside_the_canonical_vocabulary_are_dropped(engine_module):
    """A traffic light must never arrive as a vehicle because it was in frame."""
    engine = build(
        engine_module,
        vehicles=[
            ("cam01_s0_t7", [10.0, 20.0, 110.0, 140.0], "car", 0.9),
            ("cam01_s0_t8", [0.0, 0.0, 5.0, 5.0], "traffic light", 0.8),
            ("cam01_s0_t9", [1.0, 2.0, 3.0, 4.0], "motorcycle", 0.7),
        ],
    )
    detections, _ = engine.process(None, captured_at=1.0)
    assert [d.class_name for d in detections] == ["car", "motorcycle"]
    assert [d.class_id for d in detections] == [2, 3], "COCO ids, recovered from the name"
    assert [d.extra["track_id"] for d in detections] == [7, 9], "ints, parsed from the engine's keys"


def test_the_vehicle_and_plate_boxes_come_from_the_track_record(engine_module):
    engine = build(engine_module, records=[record()])
    sighting = engine.finish()[0]
    assert sighting.vehicle_bbox == [10.0, 20.0, 110.0, 140.0]
    assert sighting.plate_bbox == [40.0, 90.0, 96.0, 110.0]


def test_build_engine_returns_none_when_anpr_is_off(engine_module, monkeypatch):
    """ANPR failing is never a reason to stop counting vehicles."""
    monkeypatch.delenv("ANPR_ENABLE", raising=False)
    assert engine_module.build_engine() is None


def test_build_engine_returns_none_when_the_extras_are_missing(
    engine_module, monkeypatch
):
    monkeypatch.setenv("ANPR_ENABLE", "true")
    monkeypatch.setitem(sys.modules, "anpr.pipeline", None)

    real_import = __import__

    def fail_on_anpr(name, *args, **kwargs):
        if name.startswith("anpr"):
            raise ImportError("no anpr package in this environment")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fail_on_anpr)
    assert engine_module.build_engine() is None


# ---------------------------------------------------------------------------
# The ingest payload the worker builds from a sighting
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def worker_module():
    return _load_edge("worker")


def test_a_sighting_payload_carries_the_vote_count(worker_module, engine_module):
    """The central API records how many frames agreed; an operator is entitled
    to know whether a plate is a guess or a reading."""
    sighting = engine_module.PlateSighting(
        track_id=7,
        text="GJ01AB1234",
        confidence=0.79,
        observations=11,
        confirmed=True,
        state="GJ",
        plate_format="standard",
        plate_bbox=[40.0, 90.0, 96.0, 110.0],
        vehicle_bbox=[10.0, 20.0, 110.0, 140.0],
        captured_at=1234.5,
        frame_index=12,
        method="clahe+sr",
    )
    payload = worker_module._sighting_payload(
        sighting,
        camera_id="VIGENTRA-TRAFFIC-AHM-0001",
        timestamp_iso="2026-09-01T10:00:00Z",
        source_mode="authorized_edge",
        reader_version="vigentra-anpr-consensus/plate_detector+paddle",
        provenance={"frame_index": 12},
    )

    assert payload["plate_text"] == "GJ01AB1234"
    assert payload["plate_confidence"] == 0.79
    assert payload["provenance"]["plate_observations"] == 11
    assert payload["provenance"]["plate_confirmed"] is True
    assert payload["provenance"]["captured_at_pts"] == 1234.5
    assert payload["is_demo_data"] is False
    assert payload["bbox_xyxy"] == [10.0, 20.0, 110.0, 140.0]


def test_replaying_one_pass_collapses_to_one_row(worker_module, engine_module):
    """The detection ID is derived from the track and the text, not the frame."""
    def make(**overrides):
        base = dict(
            track_id=7, text="GJ01AB1234", confidence=0.79,
            observations=11, confirmed=True, state="GJ", plate_format="standard",
            plate_bbox=[1.0, 2.0, 3.0, 4.0], vehicle_bbox=[0.0, 0.0, 9.0, 9.0],
            captured_at=1234.5, frame_index=12,
        )
        base.update(overrides)
        return engine_module.PlateSighting(**base)

    def payload_for(sighting, timestamp="2026-09-01T10:00:00Z"):
        return worker_module._sighting_payload(
            sighting,
            camera_id="CAM-1",
            timestamp_iso=timestamp,
            source_mode="mock",
            reader_version="r/1",
            provenance={},
        )["detection_id"]

    # Same pass submitted twice - even on a later wall clock - is one row.
    assert payload_for(make()) == payload_for(make(), "2026-09-01T10:05:00Z")
    # A revised reading for the same track is deliberately a NEW row, so the
    # revision is visible rather than overwriting the first answer.
    assert payload_for(make()) != payload_for(make(text="GJ01AB1284"))
    # A different vehicle is a different row.
    assert payload_for(make()) != payload_for(make(track_id=8))


class ClosingPipeline(FakePipeline):
    """A track that closes during a pass: its vehicle left, the buffer ran out, the bank closed."""

    def __init__(self, record_at_frame, record):
        super().__init__()
        self._at, self._record = record_at_frame, record

    def process_frame(self, frame):
        super().process_frame(frame)
        if len(self.frames) == self._at:
            self.records.append(self._record)


def test_a_plate_is_handed_over_on_the_frame_its_track_closes(engine_module):
    """Not at the end of the pass: the watchlist alert it can raise must not wait for 1,500 frames."""
    engine = build(engine_module)
    engine._pipeline = ClosingPipeline(3, record())
    seen = [engine.process(FakeFrame(), captured_at=float(i))[1] for i in range(5)]
    assert [len(s) for s in seen] == [0, 0, 1, 0, 0]
    assert engine.finish() == []          # and the end of the pass does not send it again
