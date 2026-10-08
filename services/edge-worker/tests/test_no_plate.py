"""A vehicle without a visible number plate is raised only when its plate should have been seen."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.no_plate import NoPlateWatch, candidate

W, H = 1280, 720


def frame(level=120):
    rng = np.random.default_rng(0)   # texture, so the view is sharp
    return np.clip(rng.normal(level, 40, (H, W, 3)), 0, 255).astype(np.uint8)


def det(tid, box, cls="car"):
    return SimpleNamespace(bbox_xyxy=list(box), class_name=cls, extra={"track_id": tid})


ASPECT = {"motorcycle": 0.7, "scooter": 0.7}   # a front view's width / height; cars, buses ~1.2


def drive(watch, tid, *, towards=True, width=360, cls="car", level=120, n=10):
    f = frame(level)
    aspect = ASPECT.get(cls, 1.2)
    for i in range(n):
        if towards:   # coming down the frame, growing: its front faces the camera
            x1, y1, wi = 400, 100 + 40 * i, width * (0.8 + 0.02 * i)
        else:         # crossing the frame side-on
            x1, y1, wi = 100 + 80 * i, 300, width
        watch.observe(f, [det(tid, (x1, y1, x1 + wi, y1 + wi / aspect), cls)], float(i))


def closed(tid):
    return [{"track_id": f"cam06_s0_t{tid}", "reason": "no_plate_detected"}]


def test_a_close_car_facing_the_camera_with_no_plate_is_raised_with_its_frame():
    w = NoPlateWatch("cam-x")
    drive(w, 1)
    out = w.closed(closed(1), W)
    assert len(out) == 1 and out[0]["kind"] == "NO_PLATE_VISIBLE"
    assert out[0]["snapshot_jpeg_b64"]


def test_side_on_small_or_dark_is_not_raised():
    for kwargs in ({"towards": False}, {"width": 150}, {"level": 15}, {"cls": "motorcycle", "width": 100},
                   {"cls": "person"}):
        w = NoPlateWatch("cam-x")
        drive(w, 2, **kwargs)
        assert not w.closed(closed(2), W), kwargs


def test_the_rule_says_why_not():
    view = {"label": "car", "max_w": 400, "useful_frames": 2, "first_c": (0, 0), "last_c": (0, 300),
            "luma": 120, "sharpness": 100.0}
    assert candidate(view, W) == (False, "too_few_frames")


def test_every_vehicle_type_close_enough_is_raised():
    for cls, width in (("motorcycle", 160), ("bus", 320), ("truck", 320), ("car", 260)):
        w = NoPlateWatch("cam-x")
        drive(w, 3, cls=cls, width=width)
        out = w.closed(closed(3), W)
        assert len(out) == 1 and out[0]["evidence"]["vehicle"] == cls, cls


def test_a_car_turning_at_a_junction_is_not_raised():
    """cam04, 8 Oct: came down the frame, then turned; its widest view was its side."""
    w = NoPlateWatch("cam-x")
    f = frame()
    for i in range(8):                       # approaching, front on
        x1, y1, wi = 400, 100 + 30 * i, 250 + 5 * i
        w.observe(f, [det(4, (x1, y1, x1 + wi, y1 + wi / 1.2))], float(i))
    for i in range(3):                       # turning across: long and low, moving sideways
        x1, wi = 450 + 60 * i, 330 + 20 * i
        w.observe(f, [det(4, (x1, 330, x1 + wi, 330 + wi / 2.0))], 8.0 + i)
    assert not w.closed(closed(4), W)
