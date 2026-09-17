"""Stage A[1]/[1b]: vehicle detector + tracker.

Wraps Ultralytics YOLO11 with its built-in ByteTrack/BoT-SORT. Emits
VehicleTrack records per frame. Tracks are reset on source discontinuity
(loop seam / reconnect) so ids never teleport across the seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

COCO_VEHICLE = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


@dataclass
class VehicleDet:
    track_id: str
    box: tuple[float, float, float, float]   # x1,y1,x2,y2 in frame px
    conf: float
    cls_name: str


class VehicleTracker:
    def __init__(self, weights: str | Path = "models/yolo11s.pt", device: str = "auto", imgsz: int = 1280,
                 conf: float = 0.25, classes=(2, 3, 5, 7), tracker_cfg: str | Path = "config/bytetrack.yaml",
                 half: bool = True, agnostic_nms: bool = True, upscale: bool = True):
        self.agnostic_nms = agnostic_nms
        # False: a frame smaller than imgsz is detected at its own size instead of being enlarged to it.
        # On the 720p grid clip rec_cam07, enlarging to 1920 halved the tracked detections (27 vs 41).
        self.upscale = upscale
        from ultralytics import YOLO
        self.model = YOLO(str(weights))
        self.device = _resolve_device(device)
        self.imgsz = imgsz
        self.conf = conf
        self.classes = list(classes)
        self.tracker_cfg = str(tracker_cfg)
        self.half = half and self.device != "cpu"
        self._session = 0
        self._active = True

    def reset(self) -> None:
        """Terminate all tracks (call on discontinuity)."""
        self._session += 1
        # ultralytics keeps tracker state on the predictor; drop it
        pred = getattr(self.model, "predictor", None)
        if pred is not None and hasattr(pred, "trackers"):
            try:
                for t in pred.trackers:
                    t.reset()
            except Exception:
                pass

    def update(self, frame_bgr: np.ndarray, mask: Optional[np.ndarray] = None) -> list[VehicleDet]:
        img = frame_bgr
        if mask is not None:
            img = frame_bgr.copy()
            img[mask == 0] = 0
        # Frames larger than the inference size are shrunk here with INTER_AREA instead of inside
        # ultralytics' letterbox (INTER_LINEAR). On Apple-silicon OpenCV 5 that linear resize runs
        # through KleidiCV, which read past the end of a 4K frame buffer and segfaulted the run
        # (EXC_BAD_ACCESS in kleidicv_resize_generic_stripe_u8, 2026-09-11); the letterbox then
        # has nothing left to resize. Boxes are scaled back to frame pixels below.
        scale = 1.0
        if max(img.shape[:2]) > self.imgsz:
            scale = self.imgsz / max(img.shape[:2])
            img = cv2.resize(img, (max(1, round(img.shape[1] * scale)), max(1, round(img.shape[0] * scale))),
                             interpolation=cv2.INTER_AREA)
        # class-agnostic NMS: one vehicle, one box. Per-class NMS kept a car AND a truck box on
        # the same van (Delhi clip), so its plate was banked by two tracks that each fused half
        # the frames and neither reached the confirm floor.
        imgsz = self.imgsz
        if not self.upscale:
            imgsz = min(imgsz, -(-max(img.shape[:2]) // 32) * 32)
        res = self.model.track(img, persist=True, imgsz=imgsz, conf=self.conf, classes=self.classes,
                               tracker=self.tracker_cfg, device=self.device, verbose=False,
                               agnostic_nms=self.agnostic_nms)[0]
        out: list[VehicleDet] = []
        if res.boxes is None or res.boxes.id is None:
            return out
        xyxy = res.boxes.xyxy.cpu().numpy() / scale
        ids = res.boxes.id.cpu().numpy().astype(int)
        confs = res.boxes.conf.cpu().numpy()
        clss = res.boxes.cls.cpu().numpy().astype(int)
        for b, i, c, k in zip(xyxy, ids, confs, clss):
            out.append(VehicleDet(f"s{self._session}_t{int(i)}", tuple(float(v) for v in b), float(c),
                                  COCO_VEHICLE.get(int(k), "vehicle")))
        return out


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "0" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"
