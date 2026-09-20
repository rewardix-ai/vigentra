"""What a pass must survive: a failed final upload, media time on a clip, and merged fragments.

Each of these was a way for a pass to lose plates or to report a vehicle more than once.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path



sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.worker import _finish_pass, _plain  # noqa: E402
from test_finish_pass import FakeAnpr  # noqa: E402


class FailingClient:
    def ingest(self, payload):
        raise RuntimeError("central-api refused")


def test_a_failed_final_upload_names_the_plates_it_lost(caplog):
    # The engine has already marked them emitted, so they are gone; the pass must say which.
    pending = [{"plate_text": "GJ01AB1234"}, {"plate_text": "GJ05CD9876"}]
    with caplog.at_level(logging.ERROR):
        _finish_pass(FailingClient(), pending, FakeAnpr(), camera_id="cam06",
                     source_mode="demo_local", base_provenance={})
    message = caplog.text
    assert "final ingest failed" in message
    assert "GJ01AB1234" in message and "GJ05CD9876" in message
    assert pending == []


def test_clip_frames_keep_their_own_time():
    # wall-clock timestamps made the 3 s fragment-merge window depend on how fast the machine replayed
    out = list(_plain(iter([(0, "f0", 0.0), (5, "f1", 0.2)])))
    assert [(i, t, d) for i, _, t, d in out] == [(0, 0.0, False), (5, 0.2, False)]


def test_synthetic_frames_still_fall_back_to_wall_clock():
    (index, frame, pts, discontinuity), = _plain(iter([(3, "frame")]))
    assert (index, frame, discontinuity) == (3, "frame", False)
    assert pts > 0


def test_one_sighting_per_merged_vehicle():
    from app.anpr_engine import AnprEngine

    engine = AnprEngine.__new__(AnprEngine)          # no models, no config: only _settle is under test
    engine._emitted = set()
    engine._pending = []
    engine.camera_id = "cam06"
    fragments = [
        {"track_id": "cam06_s0_t7", "plate": "GJ01AB1234", "status": "CONFIRMED", "confidence": 0.8,
         "merged_from": ["cam06_s0_t7", "cam06_s0_t9"], "frames_fused": 9},
        {"track_id": "cam06_s0_t9", "plate": "GJ01AB1234", "status": "CONFIRMED", "confidence": 0.8,
         "merged_from": ["cam06_s0_t7", "cam06_s0_t9"], "frames_fused": 9},
        {"track_id": "cam06_s0_t11", "plate": "GJ05CD9876", "status": "CONFIRMED", "confidence": 0.9},
    ]

    class Pipe:
        records = fragments

        def flush(self):
            pass

    engine._pipeline = Pipe()
    engine._settle()
    assert [s.text for s in engine._pending] == ["GJ01AB1234", "GJ05CD9876"]


def test_a_settled_plate_is_uploaded_at_once_and_boxes_wait_for_the_batch():
    from app import worker
    assert not worker._should_flush([], [])
    assert not worker._should_flush([{"box": 1}], [])
    assert worker._should_flush([{"box": 1}], [object()])
    assert worker._should_flush([{}] * worker.BATCH_SIZE, [])


def test_a_continuous_reader_runs_long_passes_and_pauses_only_when_a_camera_failed():
    from types import SimpleNamespace
    from app import worker
    dense = SimpleNamespace(sampling={"max_frames": 150})
    assert worker._pass_frames(dense, 25, continuous=False) == 150
    assert worker._pass_frames(dense, 25, continuous=True) == worker.CONTINUOUS_PASS_FRAMES
    assert worker._nap_seconds(120, worst=0, continuous=False) == 120
    assert worker._nap_seconds(120, worst=0, continuous=True) == 3
    assert worker._nap_seconds(120, worst=1, continuous=True) == 120   # never hammer a dead feed
