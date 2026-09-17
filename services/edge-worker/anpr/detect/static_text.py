"""Static scene text rejection (hoardings, sign boards, shop names, phone numbers
painted on boards, road-name captions on walls).

A number plate moves with its vehicle. A sign stays at the same frame position
with the same pixels while *different* vehicle tracks pass (or a mis-detected
static "vehicle" is re-identified) in front of it, so the per-vehicle plate
detector proposes it again and again. The map remembers every plate-sized
candidate (frame box, a 96x32 thumbnail, the vehicle box, track id, frame). A new
candidate is static scene text when an earlier candidate from a DIFFERENT track
sits at the same frame position (IoU >= 0.6) with the same appearance (NCC of the
thumbnails >= 0.85) at least a second earlier. A tracker re-id of the same parked car
(near-identical vehicle box) is excluded, and so is the same plate boxed by two
overlapping vehicle boxes in the same moment; vehicles queuing at a stop line put
different plates at similar positions, but different plates have different
pixels, so the appearance test keeps them. Learned locations demote any track
already read there (retroactive check at flush).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

THUMB = (96, 32)  # w, h


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def thumbnail(crop_bgr: np.ndarray) -> np.ndarray:
    g = crop_bgr if crop_bgr.ndim == 2 else cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    t = cv2.resize(g, THUMB, interpolation=cv2.INTER_AREA).astype(np.float32)
    t -= t.mean()
    n = float(np.linalg.norm(t))
    return t / n if n > 1e-6 else t


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    return float((a * b).sum())


@dataclass
class _Entry:
    box: tuple
    thumb: np.ndarray
    vehicle_box: tuple
    track_id: int
    frame_idx: int


class StaticTextMap:
    def __init__(self, box_iou: float = 0.6, vehicle_iou_max: float = 0.7, ncc_min: float = 0.85, history: int = 800,
                 min_w: float = 20.0, min_h: float = 8.0, min_gap_frames: int = 30):
        self.min_w, self.min_h = min_w, min_h
        self.box_iou = box_iou
        self.vehicle_iou_max = vehicle_iou_max
        self.ncc_min = ncc_min
        # A sign is seen by different vehicles seconds apart. The same pixels at the same box under
        # two vehicle boxes within a second are one plate boxed twice (a car inside a bus's box, a
        # duplicate vehicle detection): delhi_1080p t221 held DL1LT1087 read exactly on 45 crops and
        # was demoted as scene text because its box also covered the neighbour's plate for one frame.
        self.min_gap_frames = min_gap_frames
        self._hist: deque[_Entry] = deque(maxlen=history)
        self.static_boxes: list[tuple] = []      # confirmed static text locations (frame coords)
        self._static_thumbs: list[np.ndarray] = []
        self.n_rejected = 0

    def _matches_known(self, box, thumb) -> bool:
        for sb, st in zip(self.static_boxes, self._static_thumbs):
            if _iou(box, sb) >= self.box_iou and ncc(thumb, st) >= self.ncc_min:
                return True
        return False

    def check(self, box, crop_bgr, vehicle_box, track_id: int, frame_idx: int) -> bool:
        """Register the candidate; return True if it is static scene text."""
        box = tuple(float(v) for v in box)
        vehicle_box = tuple(float(v) for v in vehicle_box)
        # far-field specks (a few px) all look alike once resized; only learn plate-sized text
        if (box[2] - box[0]) < self.min_w or (box[3] - box[1]) < self.min_h:
            return False
        thumb = thumbnail(crop_bgr)
        if self._matches_known(box, thumb):
            self.n_rejected += 1
            return True
        for e in self._hist:
            # same track, or the same parked/queued vehicle re-identified by the tracker (near-identical
            # vehicle box): not evidence of a sign. NOTE (measured 2026-09-10): relaxing this to a time
            # gap demoted a CORRECT confirmed read (two fragments of one bike with different box sizes)
            # and still missed a phone-number board inside a static mis-detected "vehicle"; kept strict.
            if e.track_id == track_id or _iou(e.vehicle_box, vehicle_box) >= self.vehicle_iou_max:
                continue
            if abs(frame_idx - e.frame_idx) < self.min_gap_frames:
                continue
            if _iou(e.box, box) >= self.box_iou and ncc(e.thumb, thumb) >= self.ncc_min:
                self.static_boxes.append(box)
                self._static_thumbs.append(thumb)
                self.n_rejected += 1
                return True
        self._hist.append(_Entry(box, thumb, vehicle_box, track_id, frame_idx))
        return False

    def is_static(self, box, crop_bgr) -> bool:
        """Retroactive check for tracks finalised before the location was learned."""
        if not self.static_boxes or box is None:
            return False
        return self._matches_known(tuple(float(v) for v in box), thumbnail(crop_bgr))
