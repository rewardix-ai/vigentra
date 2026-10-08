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
        out += w.observe(frame, [bike(4, 500, 200 + 25 * i)], float(i))
    out += w.observe(frame, [], 20.0)   # gone: the track has ended
    return out


def test_a_bare_headed_rider_is_raised_once_with_a_snapshot(monkeypatch):
    out = run(monkeypatch, 0.97)
    assert len(out) == 1 and out[0]["kind"] == "NO_HELMET" and out[0]["snapshot_jpeg_b64"]


def test_below_the_threshold_nothing_is_raised(monkeypatch):
    assert not run(monkeypatch, 0.6)


def test_a_parked_two_wheeler_is_not_a_rider(monkeypatch):
    monkeypatch.setattr(helmet, "no_helmet_score", lambda crop: 0.99)
    frame = np.full((720, 1280, 3), 90, np.uint8)
    w, out = helmet.HelmetWatch("cam-h"), []
    for i in range(8):   # the same spot every frame
        out += w.observe(frame, [bike(9, 500, 300)], float(i))
    out += w.observe(frame, [], 20.0)
    assert not out


def test_one_bare_looking_frame_among_its_closest_views_is_not_a_call(monkeypatch):
    """A head turned away in one frame reads as bare; the rider's other close views show the helmet."""
    looks = iter([0.97, 0.2, 0.2])
    monkeypatch.setattr(helmet, "no_helmet_score", lambda crop: next(looks))
    frame = np.full((720, 1280, 3), 90, np.uint8)
    w, out = helmet.HelmetWatch("cam-h"), []
    for i in range(6):   # coming closer: 120, 126, ... px
        out += w.observe(frame, [bike(5, 500, 200 + 25 * i, w=120 + 6 * i)], float(i))
    out += w.observe(frame, [], 20.0)
    assert not out


def test_a_rider_seen_in_only_two_frames_is_still_judged(monkeypatch):
    """Light mode often catches a passing rider once or twice; too brief to show travel, not parked."""
    monkeypatch.setattr(helmet, "no_helmet_score", lambda crop: 0.95)
    frame = np.full((720, 1280, 3), 90, np.uint8)
    w, out = helmet.HelmetWatch("cam-h"), []
    for i in range(2):
        out += w.observe(frame, [bike(11, 500, 300, w=110)], float(i))
    out += w.observe(frame, [], 20.0)
    assert len(out) == 1
