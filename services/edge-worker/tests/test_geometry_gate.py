"""The plate-geometry gate applied before OCR."""
from anpr.config import DetectConfig
from anpr.pipeline import plate_geometry_ok


def test_plate_sized_boxes_pass():
    cfg = DetectConfig()
    assert plate_geometry_ok(40, 12, 1920, cfg)       # a small plate
    assert plate_geometry_ok(300, 80, 1920, cfg)      # a near plate
    assert plate_geometry_ok(60, 40, 1920, cfg)       # a stacked two-row plate


def test_signboards_and_captions_fail():
    cfg = DetectConfig()
    assert not plate_geometry_ok(900, 120, 1920, cfg)  # a fifth of the frame and more
    assert not plate_geometry_ok(400, 30, 1920, cfg)   # a caption strip, aspect 13
    assert not plate_geometry_ok(30, 60, 1920, cfg)    # taller than wide
    assert not plate_geometry_ok(0, 0, 1920, cfg)
