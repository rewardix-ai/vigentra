"""Tracker ids must survive whatever shape they arrive in.

The ANPR engine keys a track "<camera>_s<segment>_t<n>" - `cam06_s0_t132`. The
YOLO detector hands back plain ints. Both reach the same code, and
`int(d.extra["track_id"])` in the worker's incident path raised ValueError on
the engine's keys.

That failure was total, not partial: the conversion sits inside the per-camera
try block, so one unparsable id discarded the entire pass - every detection,
every plate, every incident for that camera - and logged a single line reading
`invalid literal for int() with base 10: 's0_t1'`.

The adapter's own tests missed it because they asserted a key shape that was
invented for the tests ("cam01_7") and never emitted by the engine. These tests
use the real shape, taken from a recorded pipeline run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.anpr_engine import _track_number  # noqa: E402

#: Exactly as `anpr.pipeline` writes them (reports/pipeline_cam06_*.json).
ENGINE_KEYS = ["cam06_s0_t1", "cam06_s0_t12", "cam06_s0_t132", "cam01_s0_t7"]


@pytest.mark.parametrize("key,expected", [
    ("cam06_s0_t1", 1),
    ("cam06_s0_t12", 12),
    ("cam06_s0_t132", 132),
    ("cam01_7", 7),          # the older shape
    ("7", 7),
    (7, 7),
])
def test_the_trailing_number_is_the_tracker_id(key, expected):
    assert _track_number(key) == expected


@pytest.mark.parametrize("raw", ENGINE_KEYS + ["no_digits", "", None, object()])
def test_no_track_id_can_abort_a_camera_pass(raw):
    """The regression itself: these must return a number, never raise.

    `int("s0_t1")` raised and cost the whole cycle.
    """
    assert isinstance(_track_number(raw), int)


def test_two_different_keys_do_not_collide_into_one_track():
    """Distinct vehicles must stay distinct, or their plate histories merge."""
    ids = {_track_number(k) for k in ENGINE_KEYS}
    assert len(ids) == len(ENGINE_KEYS)


def test_the_unparsable_fallback_is_stable():
    """A hashed fallback that changed between calls would split one vehicle's
    track across several ids within a single pass."""
    assert _track_number("no_digits") == _track_number("no_digits")


# ---------------------------------------------------------------------------
# The call site itself, not just the helper
# ---------------------------------------------------------------------------

class _Det:
    """A detection as the worker sees it: box, class, and the tracker's id."""

    def __init__(self, track_id, box=(1.0, 2.0, 3.0, 4.0), class_name="car"):
        self.extra = {} if track_id is _MISSING else {"track_id": track_id}
        self.bbox_xyxy = list(box)
        self.class_name = class_name


_MISSING = object()


def test_the_incident_path_survives_the_engines_track_keys():
    """The regression, at the line that actually broke.

    A helper test passes even with the bare int() restored, because the
    conversion lived inside a comprehension in process_camera(). This drives
    the real code path.
    """
    from app.worker import _incident_views

    views = _incident_views([_Det(k) for k in ENGINE_KEYS])
    assert [v.track_id for v in views] == [1, 12, 132, 7]


def test_untracked_detections_are_skipped_not_guessed_at():
    """Only tracked vehicles can be judged for motion.

    The worker filters on `d.extra.get("track_id") is not None`, so BOTH a
    detection with no id at all and one whose id is None are dropped - an
    untracked vehicle is skipped rather than given an invented id that would
    collide with a real track.
    """
    from app.worker import _incident_views

    views = _incident_views([_Det("cam06_s0_t5"), _Det(_MISSING), _Det(None)])
    assert len(views) == 1
    assert views[0].track_id == 5


def test_a_mixed_batch_of_int_and_engine_ids_does_not_raise():
    """YOLO-only cameras emit ints, ANPR cameras emit keys; both reach here."""
    from app.worker import _incident_views

    views = _incident_views([_Det(7), _Det("cam06_s0_t132"), _Det("no_digits")])
    assert len(views) == 3
    assert views[0].track_id == 7 and views[1].track_id == 132
