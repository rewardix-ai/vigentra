"""A plate the ROI pass finds must attach to the vehicle it was found on.

Measured on cam06/cam07 before this held: 18 of 20 detected plates were
orphaned. Two mechanisms, each pinned here:

  * the ROI pass searched a vehicle box expanded by 4%, but attachment tested
    containment against the bare box at a 0.55 floor, so a bumper plate flush
    with the searched edge scored ~0.52 and was thrown away - precisely when
    the plate was small for its vehicle;
  * the tracker withholds an id until it has matched on consecutive frames,
    and a plate on an id-less vehicle had nowhere to attach at all.

These tests drive Detector.process with stubbed detectors so no weights are
needed and the geometry is exact.
"""
from anpr.config import DetectConfig
from anpr.detect import (
    Box, Detector, ORPHAN_ID_BASE, VEHICLE_FALLBACK_ID_BASE,
)

import numpy as np


class _Stub(Detector):
    """Detector with the models replaced by canned answers."""

    def __init__(self, vehicles, full_frame, roi):
        self.cfg = DetectConfig(roi_pass=True, prior_pass=False)
        self.device = "cpu"
        self.vehicle_model = object()      # non-None so ready/track paths run
        self.plate_model = object()
        self._precision = {}
        from anpr.detect import IouTracker
        self.orphan_tracker = IouTracker()
        self.vehicle_tracker = IouTracker(max_age=60,
                                          id_base=VEHICLE_FALLBACK_ID_BASE)
        self._vehicles = vehicles
        self._full = full_frame
        self._roi = roi

    def track_vehicles(self, frame):
        return [Box(*v.__dict__.values()) if False else
                Box(v.x1, v.y1, v.x2, v.y2, v.conf, v.cls, v.track_id)
                for v in self._vehicles]

    def _detect_plates(self, image):
        return list(self._full)

    def _detect_plates_batch(self, images, imgsz=None):
        # One canned answer per submitted crop, in crop coordinates.
        return [list(self._roi.get(i, [])) for i in range(len(images))]


FRAME = np.zeros((1080, 1920, 3), np.uint8)


def _plate_in_crop_margin():
    """A 300x240 vehicle whose 40x20 plate sits in the bottom 4% margin.

    Containment against the bare box is 1 - 0.04*240/20 = 0.52: below the
    0.55 floor, so the old code orphaned it although the ROI pass found it
    inside the region it searched.
    """
    vehicle = Box(500, 400, 800, 640, 0.9, 2, track_id=7)
    # crop = vehicle expanded 4%: y from 390.4 to 649.6; plate flush with bottom
    plate_full = Box(630, 629.6, 670, 649.6, 0.8)
    return vehicle, plate_full


def test_roi_plate_attaches_to_the_vehicle_it_was_found_on():
    vehicle, plate = _plate_in_crop_margin()
    # ROI answer is in crop coordinates: crop origin is the expanded box's int().
    crop_x1, crop_y1 = int(vehicle.x1 - 12), int(vehicle.y1 - 9.6)
    scale = 640 / max(312, 259)     # roi_min_size upscale the detector applies
    roi_box = Box((plate.x1 - crop_x1) * scale, (plate.y1 - crop_y1) * scale,
                  (plate.x2 - crop_x1) * scale, (plate.y2 - crop_y1) * scale, 0.8)
    det = _Stub([vehicle], full_frame=[], roi={0: [roi_box]})
    vehicles, detections = det.process(FRAME, 0)
    assert len(detections) == 1
    assert detections[0].vehicle is not None, "ROI plate was orphaned"
    assert detections[0].track_id == 7
    assert detections[0].source == "roi"


def test_full_frame_plate_in_the_margin_still_attaches():
    vehicle, plate = _plate_in_crop_margin()
    det = _Stub([vehicle], full_frame=[plate], roi={})
    _vehicles, detections = det.process(FRAME, 0)
    assert detections[0].vehicle is not None, (
        "containment must be tested against the same expanded region the "
        "ROI pass searches, not the bare vehicle box")
    assert detections[0].track_id == 7


def test_a_plate_far_from_any_vehicle_is_still_an_orphan():
    vehicle = Box(500, 400, 800, 640, 0.9, 2, track_id=7)
    stray = Box(50, 50, 90, 70, 0.6)
    det = _Stub([vehicle], full_frame=[stray], roi={})
    _vehicles, detections = det.process(FRAME, 0)
    assert detections[0].vehicle is None
    assert detections[0].track_id >= ORPHAN_ID_BASE


def test_idless_vehicle_gets_a_stable_fallback_id():
    # The tracker returned no id (first sighting, sparse frames).
    vehicle = Box(500, 400, 800, 640, 0.9, 2, track_id=None)
    plate = Box(630, 600, 670, 620, 0.8)
    det = _Stub([vehicle], full_frame=[plate], roi={})
    vehicles, detections = det.process(FRAME, 0)
    assert vehicles[0].track_id is not None
    assert vehicles[0].track_id >= VEHICLE_FALLBACK_ID_BASE
    assert detections[0].vehicle is not None, "plate on id-less vehicle orphaned"
    assert detections[0].track_id == vehicles[0].track_id
    # Same vehicle a frame later, still id-less from the tracker: same id.
    det._vehicles = [Box(505, 402, 805, 642, 0.9, 2, track_id=None)]
    vehicles2, _ = det.process(FRAME, 1)
    assert vehicles2[0].track_id == vehicles[0].track_id


def test_fallback_ids_never_collide_with_orphan_ids():
    assert VEHICLE_FALLBACK_ID_BASE > ORPHAN_ID_BASE
