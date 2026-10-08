"""A bare-headed rider becomes a candidate once, when the two-wheeler's track ends."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app import helmet


def bike(tid, x, y, w=120):
    return SimpleNamespace(bbox_xyxy=[x, y, x + w, y + 1.4 * w], class_name="motorcycle", extra={"track_id": tid})


def run(monkeypatch, score):
    monkeypatch.setattr(helmet, "no_helmet_score", lambda crop: score)
    frame = np.full((720, 1280, 3), 90, np.uint8)
    w, out = helmet.HelmetWatch("cam-h"), []
    for i in range(8):
        out += w.observe(frame, [bike(4, 500, 300 + 10 * i)], float(i))
    out += w.observe(frame, [], 20.0)   # gone: the track has ended
    return out


def test_a_bare_headed_rider_is_raised_once_with_a_snapshot(monkeypatch):
    out = run(monkeypatch, 0.97)
    assert len(out) == 1 and out[0]["kind"] == "NO_HELMET" and out[0]["snapshot_jpeg_b64"]


def test_below_the_threshold_nothing_is_raised(monkeypatch):
    assert not run(monkeypatch, 0.6)
