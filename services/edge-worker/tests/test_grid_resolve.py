"""A grid camera the documented ids do not list must still resolve to RTSP.

The worker composes stream URLs from the documented pattern instead of reading
the catalogue (which would sign central-api out). The event grid grows from 30
cameras to ~50, and central-api admits the new ones from the catalogue - so the
worker must not stop at the ids it was told about.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import worker  # noqa: E402


def test_a_camera_beyond_the_documented_ids_resolves(monkeypatch):
    monkeypatch.setattr(worker, "GRID_ENABLED", True)
    monkeypatch.setattr(worker, "_GRID_CATALOGUE", None)

    listed = worker.grid_camera_for("VIGENTRA-TRAFFIC-AHM-CAM05", "GRID-cam05")
    extra = worker.grid_camera_for("VIGENTRA-TRAFFIC-XYZ-CAM45", "GRID-cam45")

    assert listed is not None and listed.rtsp_url.endswith("/stream/cam05")
    assert extra is not None and extra.rtsp_url.endswith("/stream/cam45")


def test_a_non_grid_camera_is_not_resolved(monkeypatch):
    monkeypatch.setattr(worker, "GRID_ENABLED", True)
    assert worker.grid_camera_for("VIGENTRA-TRAFFIC-AHM-0001", "TRF-0001") is None
