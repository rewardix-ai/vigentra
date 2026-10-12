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


def test_a_settled_record_keyed_cam_s_t_finds_the_photo_of_its_track_number():
    e = _engine()
    e._pipeline.last_vehicles = [(12, [400, 300, 700, 560], "car", 0.9)]
    e._pipeline.last_plates = [(12, [500, 480, 580, 500], "cnn", 0.9)]
    e._keep_proof(np.full((720, 1280, 3), 90, np.uint8))
    assert e._take_proof(["cam06_s0_t12"])[:2] == b"\xff\xd8"


def test_a_reading_whose_photo_is_not_a_vehicle_is_dropped(monkeypatch):
    from app import anpr_engine, vehicle_type
    from app.anpr_engine import PlateSighting

    e = _engine()
    e.camera_id = "GRID-cam02"
    ok, buf = __import__("cv2").imencode(".jpg", np.full((120, 200, 3), 120, np.uint8))
    s = lambda text: PlateSighting(track_id=1, text=text, confidence=0.6, observations=7, confirmed=True, state=None,
                                   plate_format=None, plate_bbox=[], vehicle_bbox=[], captured_at=0.0, frame_index=0,
                                   evidence_jpeg=buf.tobytes())
    monkeypatch.setattr(vehicle_type, "available", lambda: True)
    monkeypatch.setattr(vehicle_type, "_classify", lambda img: ("notvehicle", 0.59))
    assert e._not_a_vehicle([s("GJ01AN67")]) == []                     # the barricade banner, 12 Oct
    monkeypatch.setattr(vehicle_type, "_classify", lambda img: ("car", 0.9))
    assert len(e._not_a_vehicle([s("GJ18BR5253")])) == 1
    no_photo = s("GJ39CB0189"); no_photo.evidence_jpeg = None
    assert len(e._not_a_vehicle([no_photo])) == 1                      # no photo: kept
