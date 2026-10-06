"""open_capture takes RTSP or fails loudly - it never falls to CDN HLS.

The CDN HLS endpoint needs a sign-in cookie the capture cannot carry, so an
unreachable RTSP port must raise rather than decode the gateway's login page.
"""
from __future__ import annotations

import pytest

from app import grid


def _camera(rtsp="rtsp://10.0.0.9:8554/stream/cam01", hls="https://cctv.example/cam01/index.m3u8"):
    return grid.GridCamera(
        id="cam01", name="C1", location="", live=True, codec=None, width=None,
        height=None, declared_fps=None, rtsp_url=rtsp, hls_url=hls, whep_url="",
    )


def test_reachable_rtsp_is_opened(monkeypatch):
    monkeypatch.setattr(grid, "_port_open", lambda *a, **k: True)
    opened = {}
    monkeypatch.setattr(grid, "ReconnectingCapture", lambda url, label="": opened.setdefault("url", url))
    grid.open_capture(_camera())
    assert opened["url"].startswith("rtsp://")


def test_unreachable_rtsp_raises_rather_than_decoding_hls(monkeypatch):
    monkeypatch.setattr(grid, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(grid, "ReconnectingCapture", lambda url, label="": pytest.fail(f"opened {url}"))
    with pytest.raises(RuntimeError, match="RTSP"):
        grid.open_capture(_camera())


BROKER = ("http://central-api:8000/api/v1/streams/sess_1", {"Authorization": "Bearer tok"})


def test_hls_fallback_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("SENTINEL_GRID_HLS_FALLBACK", raising=False)
    monkeypatch.setattr(grid, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(grid, "ReconnectingCapture", lambda url, **k: pytest.fail(f"opened {url}"))
    with pytest.raises(RuntimeError, match="SENTINEL_GRID_HLS_FALLBACK"):
        grid.open_capture(_camera(), fallback=BROKER)


def test_opt_in_reads_the_camera_through_the_broker(monkeypatch):
    monkeypatch.setenv("SENTINEL_GRID_HLS_FALLBACK", "1")
    monkeypatch.setattr(grid, "_port_open", lambda *a, **k: False)
    opened = {}
    monkeypatch.setattr(grid, "ReconnectingCapture", lambda url, **k: opened.update(url=url, **k))
    grid.open_capture(_camera(), fallback=BROKER)
    assert opened["url"] == BROKER[0] and opened["headers"] == BROKER[1]
    assert "tok" not in opened["label"]          # the token goes in headers, never in a log label


def test_reachable_rtsp_wins_even_with_the_fallback_on(monkeypatch):
    monkeypatch.setenv("SENTINEL_GRID_HLS_FALLBACK", "1")
    monkeypatch.setattr(grid, "_port_open", lambda *a, **k: True)
    opened = {}
    monkeypatch.setattr(grid, "ReconnectingCapture", lambda url, **k: opened.setdefault("url", url))
    grid.open_capture(_camera(), fallback=BROKER)
    assert opened["url"].startswith("rtsp://")


def test_headers_apply_to_that_one_open_only(monkeypatch):
    import sys
    import types
    seen = []
    fake = types.SimpleNamespace(CAP_FFMPEG=1900, VideoCapture=lambda url, api: seen.append(
        __import__("os").environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")) or types.SimpleNamespace(release=lambda: None))
    monkeypatch.setitem(sys.modules, "cv2", fake)
    monkeypatch.setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    cap = grid.ReconnectingCapture(BROKER[0], headers=BROKER[1])
    cap._connect()
    assert seen[0] == "rtsp_transport;tcp|headers;Authorization: Bearer tok\r\n"
    assert __import__("os").environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] == "rtsp_transport;tcp"


def test_the_worker_offers_its_brokered_session_as_the_fallback(monkeypatch):
    from app import worker
    seen = {}

    class Capture:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def frames(self, stall_timeout_s=None):
            return iter(())

    def fake_open(camera, fallback=None):
        seen["fallback"] = fallback
        return Capture()

    monkeypatch.setattr(worker.grid, "open_capture", fake_open)
    list(worker.iter_grid_frames(_camera(), 1, 1, BROKER))
    assert seen["fallback"] == BROKER
