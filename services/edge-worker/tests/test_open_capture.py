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
