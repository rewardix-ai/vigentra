"""Grid stream URLs carry the registered credentials, and nothing that is
logged does."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def grid(monkeypatch):
    monkeypatch.setenv("SENTINEL_GRID_EMAIL", "alice@example.com")
    monkeypatch.setenv("SENTINEL_GRID_PASSWORD", "p@ss:word/1")
    from app import grid as module
    importlib.reload(module)
    return module


def test_credentials_are_embedded_and_percent_encoded(grid):
    url = grid.with_credentials("rtsp://103.250.160.189:8554/stream/cam04")
    assert url == "rtsp://alice%40example.com:p%40ss%3Aword%2F1@103.250.160.189:8554/stream/cam04"


def test_existing_userinfo_is_left_alone(grid):
    url = "rtsp://bob%40x.org:secret@1.2.3.4:8554/stream/cam01"
    assert grid.with_credentials(url) == url


def test_no_credentials_means_no_userinfo(monkeypatch):
    monkeypatch.delenv("SENTINEL_GRID_EMAIL", raising=False)
    monkeypatch.delenv("SENTINEL_GRID_PASSWORD", raising=False)
    from app import grid as module
    importlib.reload(module)
    assert module.with_credentials("rtsp://h:8554/stream/cam01") == "rtsp://h:8554/stream/cam01"


def test_safe_url_redacts(grid):
    url = grid.with_credentials("rtsp://103.250.160.189:8554/stream/cam04")
    assert grid.safe_url(url) == "rtsp://***:***@103.250.160.189:8554/stream/cam04"
    assert "p%40ss" not in grid.safe_url(url)
    assert grid.safe_url("https://cctv.corp8.cloud/cam04/index.m3u8") == "https://cctv.corp8.cloud/cam04/index.m3u8"


def test_capture_label_never_carries_credentials(grid):
    url = grid.with_credentials("rtsp://103.250.160.189:8554/stream/cam04")
    cap = grid.ReconnectingCapture(url)
    assert "p%40ss" not in cap.label and "alice" not in cap.label
    assert cap.url == url


def test_whep_url_is_composed_with_credentials(grid, monkeypatch):
    import json, io

    class _Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def geturl(self): return "https://grid/cameras.json"

    class _Opener:
        def open(self, request, timeout=None):
            return _Resp(json.dumps([{"id": "cam04", "name": "Four"}]).encode())

    monkeypatch.setattr(grid, "_opener", lambda base_url, timeout: _Opener())
    cams = grid.fetch_catalogue("https://grid")
    cam = cams["cam04"]
    assert cam.rtsp_url.startswith("rtsp://alice%40example.com:")
    assert cam.rtsp_url.endswith("@103.250.160.189:8554/stream/cam04")
    assert cam.whep_url.endswith("@103.250.160.189:8889/stream/cam04/whep")
    assert cam.hls_url == "https://grid/cam04/index.m3u8"


def test_fallback_catalogue_when_gateway_unreachable(grid, monkeypatch):
    def _boom(base_url, timeout=15.0):
        raise OSError("gateway down")
    monkeypatch.setattr(grid, "fetch_catalogue", _boom)
    cams, source = grid.catalogue_or_fallback("https://grid")
    assert source == "fallback"
    assert len(cams) == 30 and "cam01" in cams and "cam30" in cams
    cam = cams["cam17"]
    assert cam.rtsp_url.startswith("rtsp://alice%40example.com:")
    assert cam.rtsp_url.endswith("@103.250.160.189:8554/stream/cam17")
    assert cam.hls_url == "https://grid/cam17/index.m3u8"
    assert "alice" not in grid.safe_url(cam.rtsp_url)
