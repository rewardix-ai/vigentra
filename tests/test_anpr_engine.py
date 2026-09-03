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
class FakeBox:
    x1: float = 10.0
    y1: float = 20.0
    x2: float = 110.0
    y2: float = 140.0
    conf: float = 0.9
    cls: int = 2
    track_id: int | None = 7


@dataclass
class FakeEvent:
    track_id: int = 7
    text: str = "GJ01AB1234"
    score: float = 0.82
    confidence: float = 0.79
    valid: bool = True
    fmt: str | None = "standard"
    state: str | None = "GJ"
    confirmed: bool = True
    observations: int = 11
    box: dict = field(
        default_factory=lambda: {"x1": 40, "y1": 90, "x2": 96, "y2": 110, "conf": 0.8}
    )
    quality: dict = field(default_factory=dict)
    frame: int = 12
    timestamp: float = 1234.5
    method: str = "clahe+sr"


@dataclass
class FakeResult:
    frame_idx: int = 12
    vehicles: list = field(default_factory=lambda: [FakeBox()])
    plates: list = field(default_factory=list)
    events: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


class FakePipeline:
    """Stands in for `anpr.pipeline.AnprPipeline`."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.resets = 0

    def process_frame(self, frame, timestamp=None):
        self.calls.append(timestamp)
        return self._results.pop(0) if self._results else FakeResult(events=[])

    def reset(self):
        self.resets += 1

    def stats(self):
        return {"ocr_calls": 3}


@dataclass
class FakeDetectConfig:
    plate_model: str = "plate_detector.pt"
    vehicle_model: str = "yolov8n.pt"


@dataclass
class FakeOcrConfig:
    engines: tuple = ("paddle",)


@dataclass
class FakeRegionConfig:
    preferred_states: tuple = ("GJ",)


@dataclass
class FakeConfig:
    detect: FakeDetectConfig = field(default_factory=FakeDetectConfig)
    ocr: FakeOcrConfig = field(default_factory=FakeOcrConfig)
    region: FakeRegionConfig = field(default_factory=FakeRegionConfig)


def build(engine_module, results):
    """An AnprEngine wrapping a fake pipeline, without touching real weights."""
    engine = engine_module.AnprEngine.__new__(engine_module.AnprEngine)
    engine.min_score = 0.55
    engine.cfg = FakeConfig()
    engine._pipeline = FakePipeline(results)
    engine._emitted = {}
    engine._frames = 0
    engine._started = 0.0
    return engine


# ---------------------------------------------------------------------------
# What the engine emits
# ---------------------------------------------------------------------------

def test_a_confirmed_plate_is_emitted_once_per_track(engine_module):
    """A vehicle read forty times is one sighting, not forty."""
    results = [FakeResult(events=[FakeEvent()]) for _ in range(4)]
    engine = build(engine_module, results)

    emitted = []
    for _ in range(4):
        _, plates = engine.process(None, captured_at=1.0)
        emitted.extend(plates)

    assert len(emitted) == 1
    assert emitted[0].text == "GJ01AB1234"
    assert emitted[0].observations == 11


def test_an_unconfirmed_reading_is_withheld(engine_module):
    """Too few frames to vote on is not a reading, it is a guess."""
    engine = build(engine_module, [FakeResult(events=[FakeEvent(confirmed=False)])])
    _, plates = engine.process(None, captured_at=1.0)
    assert plates == []


def test_a_low_scoring_vote_is_withheld(engine_module):
    """A low consensus score means the frames disagreed - the worst case."""
    engine = build(engine_module, [FakeResult(events=[FakeEvent(score=0.3)])])
    _, plates = engine.process(None, captured_at=1.0)
    assert plates == []


def test_a_revised_reading_is_emitted_again(engine_module):
    """Consensus may revise a plate as more frames arrive; the later answer wins.

    Re-emitted rather than suppressed, because the central API is idempotent on
    detection id and both readings should be visible to whoever reviews the
    route. Silently keeping the first answer would hide that the reading moved.
    """
    engine = build(
        engine_module,
        [
            FakeResult(events=[FakeEvent(text="GJ01AB1Z34")]),
            FakeResult(events=[FakeEvent(text="GJ01AB1234")]),
        ],
    )
    first = engine.process(None, captured_at=1.0)[1]
    second = engine.process(None, captured_at=2.0)[1]

    assert [p.text for p in first] == ["GJ01AB1Z34"]
    assert [p.text for p in second] == ["GJ01AB1234"]


def test_capture_time_is_threaded_through_to_the_pipeline(engine_module):
    """PTS, never arrival time - route reconstruction depends on this number."""
    engine = build(engine_module, [FakeResult(events=[])])
    engine.process(None, captured_at=987.25)
    assert engine._pipeline.calls == [987.25]


def test_a_sighting_carries_the_pts_not_the_wall_clock(engine_module):
    engine = build(engine_module, [FakeResult(events=[FakeEvent(timestamp=4242.0)])])
    _, plates = engine.process(None, captured_at=4242.0)
    assert plates[0].captured_at == 4242.0


def test_a_discontinuity_resets_every_track(engine_module):
    """The sandbox feeds loop. Carrying track ids over a hard cut splices two
    different vehicles into one plate history."""
    engine = build(
        engine_module,
        [FakeResult(events=[FakeEvent()]), FakeResult(events=[FakeEvent()])],
    )
    engine.process(None, captured_at=1.0)
    assert engine._pipeline.resets == 0

    plates = engine.process(None, captured_at=0.5, discontinuity=True)[1]
    assert engine._pipeline.resets == 1
    # Same track id, same text - but the reset cleared what had been emitted,
    # so the vehicle on the far side of the cut is reported as its own sighting.
    assert [p.text for p in plates] == ["GJ01AB1234"]


def test_vehicles_outside_the_canonical_vocabulary_are_dropped(engine_module):
    """A traffic light must never arrive as a vehicle because it was in frame."""
    engine = build(
        engine_module,
        [FakeResult(vehicles=[FakeBox(cls=2), FakeBox(cls=9), FakeBox(cls=3)])],
    )
    detections, _ = engine.process(None, captured_at=1.0)
    assert [d.class_name for d in detections] == ["car", "motorcycle"]


def test_the_vehicle_carrying_the_plate_is_matched_by_track(engine_module):
    engine = build(
        engine_module,
        [FakeResult(vehicles=[FakeBox(track_id=7)], events=[FakeEvent(track_id=7)])],
    )
    _, plates = engine.process(None, captured_at=1.0)
    assert plates[0].vehicle_bbox == [10.0, 20.0, 110.0, 140.0]
    assert plates[0].plate_bbox == [40.0, 90.0, 96.0, 110.0]


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
        score=0.82,
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
    assert payload["plate_confidence"] == 0.82
    assert payload["provenance"]["plate_observations"] == 11
    assert payload["provenance"]["plate_confirmed"] is True
    assert payload["provenance"]["captured_at_pts"] == 1234.5
    assert payload["is_demo_data"] is False
    assert payload["bbox_xyxy"] == [10.0, 20.0, 110.0, 140.0]


def test_replaying_one_pass_collapses_to_one_row(worker_module, engine_module):
    """The detection ID is derived from the track and the text, not the frame."""
    def make(**overrides):
        base = dict(
            track_id=7, text="GJ01AB1234", score=0.82, confidence=0.79,
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
