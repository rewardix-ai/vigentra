"""The live-wall frame service decodes only cameras that are being watched.

The guide's "open only the cameras you are processing" is a load rule the grid
enforces by blocking accounts under load. These pin the demand gate without
opening any real capture (the decoder threads are never started).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WALL = ROOT / "services" / "edge-worker" / "tools" / "snapshot_wall.py"


@pytest.fixture(scope="module")
def wall():
    spec = importlib.util.spec_from_file_location("snapshot_wall_under_test", WALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _estate(wall, cams):
    # Build the state object without starting its decoder threads.
    return wall.Estate(cams, pool=4, focus=cams[:2], width=512, hold_s=6.0, max_fps=12.0)


def test_nothing_is_decoded_until_a_camera_is_requested(wall):
    e = _estate(wall, ["cam01", "cam02", "cam03"])
    assert e._active_targets() == []          # nobody watching -> no target
    assert e.is_demanded("cam01") is False


def test_a_requested_camera_becomes_an_active_target(wall):
    e = _estate(wall, ["cam01", "cam02", "cam03"])
    e.snapshot("cam02")                        # a /snap request records demand
    assert e.is_demanded("cam02") is True
    assert e._active_targets() == ["cam02"]    # and only that one


def test_demand_expires_so_a_scrolled_away_camera_is_dropped(wall, monkeypatch):
    e = _estate(wall, ["cam01", "cam02"])
    e.note_demand("cam01")
    assert e.is_demanded("cam01") is True
    # Jump past the demand window; the camera is no longer a target.
    real = wall.time.monotonic
    monkeypatch.setattr(wall.time, "monotonic", lambda: real() + wall.DEMAND_TTL + 1)
    assert e.is_demanded("cam01") is False
    assert e._active_targets() == []
