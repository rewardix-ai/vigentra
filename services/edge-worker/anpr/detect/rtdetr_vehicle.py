"""RT-DETRv2 vehicle detector (IISc UVH-26 weights) behind the same interface
as anpr.detect.vehicle.VehicleTracker, with ByteTrack on top.

Runtime: the original RT-DETR code (third_party/RT-DETR/rtdetrv2_pytorch) is
imported for the model definition; weights are the Apache-2.0 UVH-26 release
(models/uvh26/UVH-26-MV-RT-DETRv2-S.pth). Labels are UVH-26 COCO category ids
(1..14); they are mapped to the pipeline's coarse vehicle types.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import cv2
import numpy as np
import yaml

from anpr.detect.vehicle import VehicleDet

ROOT = Path(__file__).resolve().parent.parent.parent
RTDETR_DIR = ROOT / "third_party" / "RT-DETR" / "rtdetrv2_pytorch"

UVH_TO_TYPE = {1: "car", 2: "car", 3: "car", 4: "car", 5: "bus", 6: "truck", 7: "auto", 8: "motorcycle",
               9: "truck", 10: "bus", 11: "bus", 12: None, 13: "car", 14: None}


class _Dets:
    """Minimal indexable detections container accepted by ultralytics BYTETracker
    (it needs .conf, .xywh, .cls, len() and boolean/index slicing)."""

    def __init__(self, conf: np.ndarray, xywh: np.ndarray, cls: np.ndarray):
        self.conf, self.xywh, self.cls = conf, xywh, cls

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, idx) -> "_Dets":
        return _Dets(self.conf[idx], self.xywh[idx], self.cls[idx])


def _load_tracker_args(cfg_path: str | Path) -> SimpleNamespace:
    with open(cfg_path, "r", encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    d.setdefault("frame_rate", 30)
    return SimpleNamespace(**d)


class RTDETRVehicleTracker:
    """Drop-in alternative to VehicleTracker (FULL profile)."""

    def __init__(self, weights: str | Path = "models/uvh26/UVH-26-MV-RT-DETRv2-S.pth",
                 config: str | Path = RTDETR_DIR / "configs" / "rtdetrv2" / "uvh26_r18vd.yml",
                 device: str = "auto", conf: float = 0.3, imgsz: int = 640,
                 tracker_cfg: str | Path = "config/bytetrack.yaml", half: bool = True, nms_iou: float = 0.6):
        self.nms_iou = nms_iou
        import torch
        if str(RTDETR_DIR) not in sys.path:
            sys.path.insert(0, str(RTDETR_DIR))
        from src.core import YAMLConfig  # noqa: E402  (third_party)
        from ultralytics.trackers.byte_tracker import BYTETracker

        self.torch = torch
        self.device = "cuda" if (device in ("auto", "0", "cuda") and torch.cuda.is_available()) else "cpu"
        cfg = YAMLConfig(str(config))
        ck = torch.load(str(weights), map_location="cpu", weights_only=False)
        state = ck["ema"]["module"] if "ema" in ck else ck["model"]
        cfg.model.load_state_dict(state)  # strict: any architecture mismatch raises here
        self.model = cfg.model.deploy().to(self.device).eval()
        self.post = cfg.postprocessor.deploy()
        self.conf = conf
        self.imgsz = imgsz
        self.half = half and self.device == "cuda"
        if self.half:
            self.model.half()
        self.tracker_cfg = tracker_cfg
        self.tracker = BYTETracker(_load_tracker_args(tracker_cfg))
        self._session = 0

    def reset(self) -> None:
        from ultralytics.trackers.byte_tracker import BYTETracker
        self._session += 1
        self.tracker = BYTETracker(_load_tracker_args(self.tracker_cfg))

    # ------------------------------------------------------------------
    def detect(self, frame_bgr: np.ndarray, mask: Optional[np.ndarray] = None):
        """Return (xyxy[N,4] float32, conf[N], uvh_class_id[N]) at native resolution."""
        img = frame_bgr
        if mask is not None:
            img = frame_bgr.copy()
            img[mask == 0] = 0
        H, W = img.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(img, (self.imgsz, self.imgsz), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        x = self.torch.from_numpy(rgb).permute(2, 0, 1).float().div_(255.0)[None].to(self.device)
        if self.half:
            x = x.half()
        sizes = self.torch.tensor([[W, H]], dtype=self.torch.int64, device=self.device)
        with self.torch.no_grad():
            labels, boxes, scores = self.post(self.model(x), sizes)
        labels = labels[0].detach().cpu().numpy().astype(int)
        boxes = boxes[0].detach().cpu().numpy().astype(np.float32)
        scores = scores[0].detach().float().cpu().numpy()
        keep = scores >= self.conf
        boxes, scores, labels = boxes[keep], scores[keep], labels[keep]
        # DETR emits several overlapping queries per object (no NMS by design); on the
        # Delhi clip that spawned 2x the tracks with partial boxes. Class-agnostic NMS.
        if len(boxes) > 1:
            from anpr.detect.tiling import nms
            k = nms(boxes, scores, self.nms_iou)
            boxes, scores, labels = boxes[k], scores[k], labels[k]
        return boxes, scores, labels

    def update(self, frame_bgr: np.ndarray, mask: Optional[np.ndarray] = None) -> list[VehicleDet]:
        boxes, scores, labels = self.detect(frame_bgr, mask)
        # drop classes without plates (bicycle, others)
        sel = np.array([UVH_TO_TYPE.get(int(c)) is not None for c in labels], bool) if len(labels) else np.zeros(0, bool)
        boxes, scores, labels = boxes[sel], scores[sel], labels[sel]
        if len(boxes) == 0:
            self.tracker.update(_Dets(np.zeros(0, np.float32), np.zeros((0, 4), np.float32), np.zeros(0, np.float32)))
            return []
        xywh = np.stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2,
                         boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]], 1).astype(np.float32)
        tracks = self.tracker.update(_Dets(scores.astype(np.float32), xywh, labels.astype(np.float32)))
        out: list[VehicleDet] = []
        for t in tracks:
            x1, y1, x2, y2, tid, sc, cls = t[:7]
            out.append(VehicleDet(f"s{self._session}_t{int(tid)}", (float(x1), float(y1), float(x2), float(y2)),
                                  float(sc), UVH_TO_TYPE.get(int(cls), "vehicle")))
        return out
