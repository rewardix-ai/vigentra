"""The integrator's guide, asserted rather than documented.

Each test here pins one rule from §3 of
https://sentinel.gujarat.gov.in/resource. They run against a fake capture, so
they hold whether or not the sandbox is reachable - which matters, because the
grid's media plane goes down independently of its catalogue and a rule that is
only checked when the feed is up is not checked at all.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GRID = ROOT / "services" / "edge-worker" / "app" / "grid.py"


@pytest.fixture(scope="module")
def grid():
    """Load the edge module by path - `app` collides with central-api's."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("edge_grid_under_test", GRID)
    module = importlib.util.module_from_spec(spec)
    sys.modules["edge_grid_under_test"] = module
    spec.loader.exec_module(module)
    return module


class ScriptExhausted(RuntimeError):
    """Raised when a scripted capture runs out of reads.

    A real capture on a permanently dead feed keeps returning False for ever,
    and `frames()` keeps retrying behind the backoff - correct in production,
    where a supervisor decides when to give up, but a test needs a definite
    end. Exhaustion is made explicit rather than silently looping.
    """


class FakeCapture:
    """Stands in for cv2.VideoCapture. Scripted reads, scripted timestamps."""

    def __init__(self, script):
        # script: list of (ok, pts_ms)
        self.script = list(script)
        self.released = 0
        self._pts = 0.0

    def read(self):
        if not self.script:
            raise ScriptExhausted
        ok, pts = self.script.pop(0)
        if ok:
            self._pts = pts
        return ok, (object() if ok else None)

    def get(self, _prop):
        return self._pts

    def release(self):
        self.released += 1


def _capture_over(grid, script, monkeypatch):
    """A ReconnectingCapture wired to a scripted FakeCapture."""
    fake = FakeCapture(script)
    cap = grid.ReconnectingCapture("rtsp://example/stream/1", label="test")

    monkeypatch.setattr(cap, "_connect", lambda: setattr(cap, "_capture", fake))
    cap._capture = fake
    # cv2 is imported inside the read loop only for CAP_PROP_POS_MSEC.
    monkeypatch.setitem(sys.modules, "cv2", type("cv2", (), {"CAP_PROP_POS_MSEC": 0})())
    return cap, fake


# --- DO force RTSP over TCP -------------------------------------------------

def test_rtsp_transport_is_pinned_to_tcp(grid, monkeypatch):
    """UDP fails across NAT and corrupts frames in ways that look like model bugs."""
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    grid._force_tcp_transport()
    assert "rtsp_transport;tcp" in os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]


def test_existing_ffmpeg_options_are_preserved(grid, monkeypatch):
    """Pinning transport must not silently discard an operator's own tuning."""
    monkeypatch.setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "stimeout;5000000")
    grid._force_tcp_transport()
    value = os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]
    assert "stimeout;5000000" in value and "rtsp_transport;tcp" in value


# --- DO drive timing from PTS, DON'T assume a constant frame rate -----------

def test_frame_timing_comes_from_pts_deltas_not_a_fixed_cadence(grid, monkeypatch):
    """Inter-frame gaps are real and must be reported as measured, not assumed."""
    cap, _ = _capture_over(
        grid, [(True, 0.0), (True, 40.0), (True, 300.0), (True, 340.0)], monkeypatch
    )
    frames = list(cap.frames(max_frames=4))

    assert [f.pts_ms for f in frames] == [0.0, 40.0, 300.0, 340.0]
    # First frame has no predecessor; the 260 ms gap is passed through intact
    # rather than smoothed into a nominal 40 ms.
    assert [f.dt_ms for f in frames] == [None, 40.0, 260.0, 40.0]


def test_a_gap_is_not_treated_as_a_disconnect(grid, monkeypatch):
    """A long but forward gap must not trigger a reconnect."""
    cap, fake = _capture_over(grid, [(True, 0.0), (True, 5000.0)], monkeypatch)
    frames = list(cap.frames(max_frames=2))
    assert frames[1].dt_ms == 5000.0
    assert not frames[1].discontinuity
    assert fake.released == 0, "a gap must not tear down the capture"


# --- DON'T trust the reported frame rate ------------------------------------

def test_measured_fps_is_derived_from_pts_not_declared(grid, monkeypatch):
    """The catalogue says 25 fps; the stream actually delivers 20."""
    script = [(True, i * 50.0) for i in range(5)]  # 50 ms spacing == 20 fps
    cap, _ = _capture_over(grid, script, monkeypatch)
    list(cap.frames(max_frames=5))
    assert cap.measured_fps == pytest.approx(20.0, rel=1e-6)


def test_no_code_path_reads_cap_prop_fps(grid):
    """Using the declared rate for time-derived metrics produces wrong numbers.

    Checked with the AST rather than a substring search, so that discussing
    the rule in a comment does not fail the test that enforces it.
    """
    import ast

    tree = ast.parse(GRID.read_text(encoding="utf-8"))
    used = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert "CAP_PROP_FPS" not in used, "the declared frame rate must never be read"
    assert "CAP_PROP_POS_MSEC" in used, "timing must come from PTS"


# --- DO expect a scene discontinuity ----------------------------------------

def test_backwards_pts_raises_discontinuity_and_resets_timing(grid, monkeypatch):
    """Each feed loops; at the cut, long-lived track state must reset."""
    cap, _ = _capture_over(
        grid, [(True, 9000.0), (True, 9040.0), (True, 20.0), (True, 60.0)], monkeypatch
    )
    frames = list(cap.frames(max_frames=4))

    assert [f.discontinuity for f in frames] == [False, False, True, False]
    # No delta is offered across the cut - a tracker fed 9040 -> 20 would
    # compute an impossible velocity.
    assert frames[2].dt_ms is None
    assert frames[3].dt_ms == 40.0


# --- DON'T treat join-time decode warnings as fatal -------------------------

def test_join_time_decode_failures_are_tolerated(grid, monkeypatch):
    """Attaching mid-stream on H.265 throws until the first IDR arrives."""
    script = [(False, 0.0)] * 10 + [(True, 100.0)]
    cap, fake = _capture_over(grid, script, monkeypatch)
    frames = list(cap.frames(max_frames=1))

    assert len(frames) == 1
    assert fake.released == 0, "10 bad reads is normal on join, not a drop"


def test_a_real_drop_reconnects_with_backoff(grid, monkeypatch):
    """Past the grace window it is a genuine drop - back off, never tight-loop."""
    slept: list[float] = []
    monkeypatch.setattr(grid.time, "sleep", slept.append)

    cap, _ = _capture_over(grid, [(False, 0.0)] * 40, monkeypatch)
    reconnects: list[int] = []
    monkeypatch.setattr(cap, "_connect", lambda: reconnects.append(1))

    with pytest.raises(ScriptExhausted):
        list(cap.frames(max_frames=1))      # never yields; ends when reads run out

    assert reconnects, "must reconnect once the grace window is exceeded"
    assert slept and slept[0] == grid.ReconnectingCapture.MIN_BACKOFF
    assert all(s <= grid.ReconnectingCapture.MAX_BACKOFF for s in slept), "backoff is capped"
    assert slept == sorted(slept), "backoff grows, it does not thrash"


# --- DO pace your load ------------------------------------------------------

def test_capture_is_released_on_exit(grid, monkeypatch):
    """Each client gets its own copy of the stream; holding one idle is abuse."""
    cap, fake = _capture_over(grid, [(True, 0.0)], monkeypatch)
    with cap:
        pass
    assert fake.released == 1


# --- DON'T plan around obtaining copies of the footage ----------------------

def test_nothing_is_written_to_disk(grid):
    """Frames are decoded from a live capture, never from a downloaded file.

    AST rather than a substring search, and bare calls kept apart from method
    calls: the builtin `open(path)` is what makes a local copy, while
    `opener.open(request)` is a urllib read over the network. Matching both on
    the name alone fails an adapter that only ever streams.
    """
    import ast

    tree = ast.parse(GRID.read_text(encoding="utf-8"))
    functions: set[str] = set()
    methods: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            methods.add(fn.attr)
        elif isinstance(fn, ast.Name):
            functions.add(fn.id)

    assert "open" not in functions, "open() suggests a local copy is being made"
    for forbidden in ("NamedTemporaryFile", "urlretrieve", "mktemp"):
        assert forbidden not in functions | methods, (
            f"{forbidden}() suggests a local copy is being made"
        )


# --- DON'T publish to the gateway -------------------------------------------

def test_module_only_ever_reads(grid):
    """Consume only - the grid's own rule. No write verb may appear in a call."""
    import ast

    tree = ast.parse(GRID.read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            called.add((fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")).lower())
    for verb in ("post", "put", "delete", "patch", "announce", "record"):
        assert verb not in called, f"{verb}() would be writing to a read-only source"
