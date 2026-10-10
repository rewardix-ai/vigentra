import numpy as np

from app.anpr_engine import AnprEngine


class _Pipe:
    def __init__(self):
        self.last_vehicles, self.last_plates = [], []


def _engine():
    e = AnprEngine.__new__(AnprEngine)   # no models: only the proof bookkeeping is exercised
    e._pipeline, e._proof = _Pipe(), {}
    return e


def test_the_widest_surest_plate_view_is_kept_and_taken_once():
    e = _engine()
    frame = np.full((720, 1280, 3), 90, np.uint8)
    for w, conf in ((40, 0.9), (80, 0.9), (60, 0.95)):
        e._pipeline.last_vehicles = [(7, [400, 300, 700, 560], "car", 0.9)]
        e._pipeline.last_plates = [(7, [500, 480, 500 + w, 500], "cnn", conf)]
        e._keep_proof(frame)
    assert e._proof["7"][0] == 80 * 0.9          # the 80 px view won
    jpeg = e._take_proof([7, 12])
    assert jpeg[:2] == b"\xff\xd8" and "7" not in e._proof
    assert e._take_proof([7]) is None            # taken once


def test_no_plate_box_no_photo():
    e = _engine()
    e._pipeline.last_vehicles = [(3, [10, 10, 200, 200], "car", 0.9)]
    e._keep_proof(np.zeros((480, 640, 3), np.uint8))
    assert e._proof == {}
