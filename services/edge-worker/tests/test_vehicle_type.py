"""A track's vehicle type: a confident classifier call replaces the detector's class, a weak one does not."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app import vehicle_type
from app.vehicle_type import VehicleTypes

FRAME = np.zeros((720, 1280, 3), np.uint8)


def det(tid, w, cls="truck"):
    return SimpleNamespace(bbox_xyxy=[100, 100, 100 + w, 100 + w], class_name=cls, extra={"track_id": tid})


def test_an_auto_the_detector_calls_a_truck_is_posted_as_an_auto_rickshaw(monkeypatch):
    monkeypatch.setattr(vehicle_type, "_classify", lambda crop: ("auto", 0.95))
    vt = VehicleTypes()
    d = det(1, 120)
    vt.refine(FRAME, [d], 0.0)
    assert d.class_name == "auto-rickshaw" and d.extra["detector_class"] == "truck"
    later = det(1, 125)          # the next frame's detection of the same track keeps the type, no new look
    vt.refine(FRAME, [later], 0.2)
    assert later.class_name == "auto-rickshaw" and vt.tracks[1]["looks"] == 1


def test_a_weak_or_unclear_call_keeps_the_detector_class(monkeypatch):
    for answer in (("scooter", 0.5), ("unclear", 0.99)):
        monkeypatch.setattr(vehicle_type, "_classify", lambda crop, a=answer: a)
        vt = VehicleTypes()
        d = det(2, 120, "motorcycle")
        vt.refine(FRAME, [d], 0.0)
        assert d.class_name == "motorcycle", answer


def test_a_reused_track_id_after_a_scene_cut_is_a_new_vehicle(monkeypatch):
    monkeypatch.setattr(vehicle_type, "_classify", lambda crop: ("bus", 0.9))
    vt = VehicleTypes()
    vt.refine(FRAME, [det(3, 120, "car")], 50.0)
    monkeypatch.setattr(vehicle_type, "_classify", lambda crop: ("car", 0.9))
    d = det(3, 120, "car")
    vt.refine(FRAME, [d], 1.0)    # pts went back: the stream looped and the tracker reused the id
    assert d.class_name == "car"
