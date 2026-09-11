"""The Integrator's Guide rules, pinned against a fake capture so they hold
whether or not the grid is reachable.

Pre-submission checklist (guide, section 4), one test per line:
  RTSP clients force TCP ................... test_rtsp_transport_is_pinned_to_tcp
  nothing depends on CAP_PROP_FPS/arrival .. test_measured_fps_is_derived_from_pts_not_declared
                                             test_frame_timing_comes_from_pts_deltas_not_a_fixed_cadence
                                             test_no_code_path_reads_cap_prop_fps
  inter-frame gaps do not stall ............ test_a_gap_is_not_treated_as_a_disconnect
  reconnect with backoff ................... test_a_real_drop_reconnects_with_backoff
  join-time decoder warnings not fatal ..... test_join_time_decode_failures_are_tolerated
  scene discontinuity (loop point) ......... test_backwards_pts_raises_discontinuity_and_resets_timing
                                             test_pts_jitter_is_not_a_loop
  consume only, pace the load .............. test_nothing_is_written_to_disk
                                             test_module_only_ever_reads
                                             test_capture_is_released_on_exit
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


def test_measured_fps_is_derived_from_pts_not_declared(fake):
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


def test_backwards_pts_raises_discontinuity_and_resets_timing(fake):
    FakeCapture.next_script = [600_000, 600_040, 80, 120]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 4)
    assert frames[2].discontinuity and frames[2].dt_ms is None
    assert not frames[3].discontinuity and frames[3].dt_ms == 40
    # Timing restarts at the loop: the rate is measured over the new pass only.
    assert cap.measured_fps == pytest.approx(1000 / 40)


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


def test_frame_timing_comes_from_pts_deltas_not_a_fixed_cadence(fake):
    # Irregular delivery: 40, 120, 20 ms. dt is what the stream said, never
    # 1000/fps.
    FakeCapture.next_script = [1000, 1040, 1160, 1180]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    frames = _frames(cap, 4)
    assert [f.dt_ms for f in frames] == [None, 40.0, 120.0, 20.0]
    assert [f.pts_ms for f in frames] == [1000, 1040, 1160, 1180]


def test_nothing_is_written_to_disk(fake):
    src = Path(grid.__file__).read_text(encoding="utf-8")
    assert "imwrite" not in src and "VideoWriter" not in src
    assert not re.search(r"open\([^)]*['\"]w", src), "the module opens nothing for writing"


def test_capture_is_released_on_exit(fake):
    FakeCapture, _ = fake
    FakeCapture.next_script = [0, 40]
    with grid.ReconnectingCapture("rtsp://h/stream/cam01") as cap:
        next(cap.frames())
    assert FakeCapture.constructed[-1].released


def test_a_feed_with_no_frames_gives_up_within_the_stall_budget(fake):
    """A dead camera must not hold the worker for ever: with a stall budget,
    frames() raises instead of reconnecting indefinitely."""
    FakeCapture, _sleeps = fake
    FakeCapture.next_script = [None] * 500
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    with pytest.raises(grid.CaptureStalled):
        next(cap.frames(stall_timeout_s=0.0))


def test_a_healthy_feed_is_unaffected_by_the_stall_budget(fake):
    FakeCapture, _sleeps = fake
    FakeCapture.next_script = [0, 40, 80]
    cap = grid.ReconnectingCapture("rtsp://h/stream/cam01")
    got = [f.pts_ms for f in cap.frames(max_frames=3, stall_timeout_s=60.0)]
    assert got == [0, 40, 80]
