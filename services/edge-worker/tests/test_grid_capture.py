"""The Integrator's Guide rules, pinned against a fake capture so they hold
whether or not the grid is reachable.

Pre-submission checklist (guide, section 4), one test per line:
  RTSP clients force TCP ................... test_rtsp_transport_is_pinned_to_tcp
  nothing depends on CAP_PROP_FPS/arrival .. test_frame_timing_comes_from_pts_not_fps
                                             test_no_code_path_reads_cap_prop_fps
  inter-frame gaps do not stall ............ test_a_gap_is_not_treated_as_a_disconnect
  reconnect with backoff ................... test_a_real_drop_reconnects_with_backoff
  join-time decoder warnings not fatal ..... test_join_time_decode_failures_are_tolerated
  scene discontinuity (loop point) ......... test_loop_point_raises_discontinuity
                                             test_pts_jitter_is_not_a_loop
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import grid


class FakeCapture:
    """Stands in for cv2.VideoCapture: a scripted sequence of reads.

    Each entry is either a PTS in ms (a good frame with that timestamp) or
    None (a failed read, as a decoder produces at join time or in a gap).
    """

    constructed: list["FakeCapture"] = []
    next_script: list = []

    def __init__(self, url, backend=None, params=None):
        self.url = url
        self.script = list(FakeCapture.next_script)
        self.pts = 0.0
        self.released = False
        FakeCapture.constructed.append(self)

    def read(self):
        if not self.script:
            return False, None
        item = self.script.pop(0)
        if item is None:
            return False, None
        self.pts = float(item)
        return True, object()

    def get(self, prop):
        import cv2

        if prop == cv2.CAP_PROP_POS_MSEC:
            return self.pts
        if prop == cv2.CAP_PROP_FPS:
            return 999.0  # deliberately absurd: anything using it is wrong
        return 0.0

    def release(self):
        self.released = True


@pytest.fixture
def fake(monkeypatch):
    import cv2

    FakeCapture.constructed = []
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    sleeps: list[float] = []
    monkeypatch.setattr(grid.time, "sleep", lambda s: sleeps.append(s))
    return FakeCapture, sleeps


def _frames(cap, n):
    out = []
    for frame in cap.frames():
        out.append(frame)
        if len(out) >= n:
            break
    return out


def test_rtsp_transport_is_pinned_to_tcp(monkeypatch):
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    grid._force_tcp_transport()
    assert "rtsp_transport;tcp" in grid.os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]


def test_frame_timing_comes_from_pts_not_fps(fake):
    FakeCapture.next_script = [0, 40, 100, 180]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 4)
    assert [f.dt_ms for f in frames] == [None, 40.0, 60.0, 80.0]
    # 3 intervals over 180 ms; the declared 999 fps never enters into it.
    assert cap.measured_fps == pytest.approx(3 * 1000 / 180)


def test_no_code_path_reads_cap_prop_fps():
    src = Path(grid.__file__).read_text(encoding="utf-8")
    # It may be named in prose (the docstrings explain why it is not used),
    # never read: no call passes it to a capture.
    assert "CAP_PROP_FPS)" not in src and "get(cv2.CAP_PROP_FPS" not in src


def test_a_gap_is_not_treated_as_a_disconnect(fake):
    FakeCapture, sleeps = fake
    gap = [None] * (grid.ReconnectingCapture.GRACE_FAILURES - 1)
    FakeCapture.next_script = [0, 40, *gap, 3000]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 3)
    assert len(FakeCapture.constructed) == 1, "a gap must not tear the capture down"
    assert sleeps == []
    assert frames[-1].pts_ms == 3000 and frames[-1].dt_ms == 2960 and not frames[-1].discontinuity


def test_join_time_decode_failures_are_tolerated(fake):
    FakeCapture, sleeps = fake
    FakeCapture.next_script = [*([None] * 20), 0, 40]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 2)
    assert len(frames) == 2 and len(FakeCapture.constructed) == 1


def test_a_real_drop_reconnects_with_backoff(fake):
    FakeCapture, sleeps = fake
    dead = [None] * (grid.ReconnectingCapture.GRACE_FAILURES + 1)
    scripts = iter([dead, dead, [0, 40]])
    orig_init = FakeCapture.__init__

    def init(self, *a, **k):
        FakeCapture.next_script = next(scripts, [0, 40])
        orig_init(self, *a, **k)

    FakeCapture.__init__ = init
    try:
        cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
        first = next(cap.frames())
    finally:
        FakeCapture.__init__ = orig_init
    assert first.pts_ms == 0
    assert len(FakeCapture.constructed) == 3
    assert sleeps == [2.0, 4.0], "exponential from 2 s, never a tight loop"
    assert cap.MAX_BACKOFF == 30.0


def test_loop_point_raises_discontinuity(fake):
    FakeCapture.next_script = [600_000, 600_040, 80, 120]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 4)
    assert frames[2].discontinuity and frames[2].dt_ms is None
    assert not frames[3].discontinuity and frames[3].dt_ms == 40


def test_pts_jitter_is_not_a_loop(fake):
    # cam10 on 2026-09-10: 15600 -> 11800 with the picture continuous.
    FakeCapture.next_script = [15_600, 11_800, 11_840, 17_080, 15_880]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 5)
    assert not any(f.discontinuity for f in frames)
    assert [f.dt_ms for f in frames] == [None, None, 40.0, 5240.0, None]
    assert cap.jitter_steps == 2


def test_module_only_ever_reads():
    src = Path(grid.__file__).read_text(encoding="utf-8")
    assert not re.search(r'method\s*=\s*"(POST|PUT|DELETE|PATCH)"', src)
    assert "data=urllib.parse.urlencode(form)" in src  # the one POST: sign-in
