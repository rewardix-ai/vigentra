"""What kind of vehicle each track is: auto-rickshaw, bus, car, motorcycle, scooter or truck.

The COCO vehicle detector knows car, motorcycle, bus and truck only. On the grid that is wrong in a
systematic way (480 grid vehicles labelled by eye, 8 Oct): its "truck" was an auto-rickshaw 45 times,
a car 19 times and a truck 6 times, and its "motorcycle" never separates scooters from motorbikes.

So each track is classified by a small YOLO11n classifier trained on 960 grid crops labelled by eye
(models/vtype_cls.pt; ANPR research repo tools/vehicle_crops.py, vtype_ds.py, vtype_eval.py), on its
widest view so far, again whenever the view grows by half (a closer look), at most MAX_LOOKS times.
Only a confident call replaces the detector's class (CONFIDENT); "unclear" and weak calls keep it.
Held out by camera (146 vehicles of a clear type): the detector's class was right for 88, classifier
where confident plus the detector otherwise for 130, and the classifier's own confident calls for 108
of 113 (v2, 8 Oct; v1, trained on half the crops, managed 117).
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("vigentra.edge.vehicle_type")

WEIGHTS = Path(os.getenv("VEHICLE_TYPE_WEIGHTS", str(Path(__file__).resolve().parent.parent / "models" / "vtype_cls.pt")))
CONFIDENT = float(os.getenv("VEHICLE_TYPE_CONFIDENT", "0.7"))
MIN_WIDTH = 60.0        # px: a narrower vehicle is a few pixels of colour
GROW = 1.5              # look again when the vehicle is this much wider than at the last look
MAX_LOOKS = 3
FORGET_S = 120.0
#: classifier class -> the class name the platform uses (central schemas.DETECTION_CLASSES)
NAMES = {"auto": "auto-rickshaw", "bus": "bus", "car": "car", "motorcycle": "motorcycle",
         "scooter": "scooter", "truck": "truck"}
VEHICLES = frozenset({"car", "motorcycle", "bus", "truck"})

_model = None
_lock = threading.Lock()


def available() -> bool:
    return WEIGHTS.exists()


def _classify(crop) -> tuple[str, float]:
    global _model
    with _lock:
        if _model is None:
            from ultralytics import YOLO
            _model = YOLO(str(WEIGHTS))
        r = _model.predict(crop, imgsz=224, verbose=False, device="cpu")[0]
        return _model.names[int(r.probs.top1)], float(r.probs.top1conf)


def two_wheeler_score(crop) -> float:
    """P(motorcycle) + P(scooter) for a vehicle crop: the helmet check asks it before judging a rider
    (cam04, 8 Oct: a pedal cargo tricycle the detector called a motorcycle scored 0.04; labelled grid
    motorbikes and scooters score >= 0.1 in 97 of 100 cases)."""
    global _model
    with _lock:
        if _model is None:
            from ultralytics import YOLO
            _model = YOLO(str(WEIGHTS))
        p = _model.predict(crop, imgsz=224, verbose=False, device="cpu")[0].probs.data
        ids = [k for k, v in _model.names.items() if v in ("motorcycle", "scooter")]
        return float(sum(p[i] for i in ids))


class VehicleTypes:
    """Per camera: each track's type, refined as the vehicle comes closer."""

    def __init__(self) -> None:
        self.tracks: dict[int, dict] = {}

    def refine(self, frame, detections, pts: float) -> None:
        """Set each vehicle detection's class_name to its track's type (the detector's class kept in
        extra['detector_class'])."""
        h, w = frame.shape[:2]
        for d in detections:
            extra = getattr(d, "extra", None)
            tid = (extra or {}).get("track_id")
            if tid is None or d.class_name not in VEHICLES:
                continue
            t = self.tracks.get(tid)
            if t is None or pts < t["last"] or pts - t["last"] > 5.0:
                # new, or the id came back after a scene cut or a long gap: another vehicle
                t = self.tracks[tid] = {"type": None, "score": 0.0, "looked_w": 0.0, "looks": 0, "last": pts}
            t["last"] = pts
            x1, y1, x2, y2 = d.bbox_xyxy
            bw, bh = x2 - x1, y2 - y1
            if bw >= MIN_WIDTH and t["looks"] < MAX_LOOKS and bw >= GROW * t["looked_w"]:
                mx, my = 0.08 * bw, 0.08 * bh   # as the training crops were cut
                crop = frame[int(max(0, y1 - my)):int(min(h, y2 + my)), int(max(0, x1 - mx)):int(min(w, x2 + mx))]
                if crop.size:
                    try:
                        name, score = _classify(crop)
                    except Exception as exc:  # pragma: no cover - a side check never stops the pass
                        logger.warning("vehicle type failed: %s", exc)
                        name, score = "unclear", 0.0
                    t["looked_w"], t["looks"] = bw, t["looks"] + 1
                    if name in NAMES and score >= CONFIDENT:
                        t["type"], t["score"] = NAMES[name], score
            if t["type"] and t["type"] != d.class_name:
                extra["detector_class"] = d.class_name
                extra["type_score"] = round(t["score"], 3)
                d.class_name = t["type"]
        for tid in [k for k, v in self.tracks.items() if pts - v.get("last", pts) > FORGET_S]:
            self.tracks.pop(tid, None)
