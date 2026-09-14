"""Per-track plate crop bank (spec 6.2.5). Every crop is stamped with
(track_id, frame_idx, pts_ms, quality) and the bank keeps the top-N by quality.
The bank is the entire input to Stage B."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from anpr.enhance.quality import CropQuality, assess


@dataclass
class PlateCrop:
    image: np.ndarray                 # BGR, native resolution, with margin
    corners: Optional[np.ndarray]     # 4x2 in crop coordinates (tl,tr,br,bl) or None
    box_frame: tuple[float, float, float, float]   # plate box in frame coordinates
    frame_idx: int
    pts_ms: float
    det_conf: float
    quality: CropQuality
    two_row: bool = False


@dataclass
class TrackBank:
    track_id: str
    camera_id: str
    vehicle_type: str = "car"
    first_pts_ms: float = 0.0
    last_pts_ms: float = 0.0
    first_frame: int = 0
    last_frame: int = 0
    vehicle_box: tuple[float, float, float, float] = (0, 0, 0, 0)
    crops: list[PlateCrop] = field(default_factory=list)
    n_frames_seen: int = 0
    n_plate_hits: int = 0
    closed: bool = False
    max_bank: int = 64
    vehicle_centres: list = field(default_factory=list)   # (cx, cy) per frame seen, capped

    def vehicle_motion_px(self) -> float:
        if len(self.vehicle_centres) < 2:
            return 0.0
        a = np.array(self.vehicle_centres)
        return float(np.linalg.norm(a.max(0) - a.min(0)))

    def add(self, crop: PlateCrop) -> None:
        self.n_plate_hits += 1
        self.crops.append(crop)
        if len(self.crops) > self.max_bank:
            self.crops.sort(key=lambda c: c.quality.quality_score, reverse=True)
            del self.crops[self.max_bank:]

    @staticmethod
    def rank_score(c: PlateCrop) -> float:
        """Image quality x detector belief. Quality alone chose a sharp bumper edge
        (retro proposal, conf 0.4) over the real 135-px plate (CNN, conf 0.79) on
        sandbox cam06; a crop the detector is unsure about must not lead the bank."""
        return c.quality.quality_score * (0.4 + 0.6 * min(max(c.det_conf, 0.0), 1.0))

    def top_k(self, k: int) -> list[PlateCrop]:
        return sorted(self.crops, key=self.rank_score, reverse=True)[:k]

    @property
    def best(self) -> Optional[PlateCrop]:
        return max(self.crops, key=self.rank_score) if self.crops else None

    def quality_stats(self) -> dict:
        if not self.crops:
            return {"n": 0}
        qs = np.array([c.quality.quality_score for c in self.crops])
        ws = np.array([c.quality.width_px for c in self.crops])
        return {"n": int(len(qs)), "q_max": float(qs.max()), "q_mean": float(qs.mean()),
                "w_max": float(ws.max()), "w_p50": float(np.median(ws))}


class CropBankStore:
    def __init__(self, camera_id: str, max_bank: int = 64):
        self.camera_id = camera_id
        self.max_bank = max_bank
        self.banks: dict[str, TrackBank] = {}

    def touch(self, track_id: str, frame_idx: int, pts_ms: float, vehicle_box, vehicle_type: str) -> TrackBank:
        b = self.banks.get(track_id)
        if b is None:
            b = TrackBank(track_id, self.camera_id, vehicle_type, pts_ms, pts_ms, frame_idx, frame_idx,
                          tuple(vehicle_box), max_bank=self.max_bank)
            self.banks[track_id] = b
        b.last_pts_ms = pts_ms
        b.last_frame = frame_idx
        b.vehicle_box = tuple(vehicle_box)
        b.n_frames_seen += 1
        if len(b.vehicle_centres) < 256:
            b.vehicle_centres.append(((vehicle_box[0] + vehicle_box[2]) / 2, (vehicle_box[1] + vehicle_box[3]) / 2))
        return b

    def add_crop(self, track_id: str, image: np.ndarray, corners, box_frame, frame_idx: int, pts_ms: float,
                 det_conf: float, two_row: bool = False) -> PlateCrop:
        q = assess(image, corners)
        crop = PlateCrop(image, corners, tuple(box_frame), frame_idx, pts_ms, det_conf, q, two_row)
        self.banks[track_id].add(crop)
        return crop

    def close_stale(self, current_frame: int, max_gap_frames: int) -> list[TrackBank]:
        out = []
        for b in self.banks.values():
            if not b.closed and current_frame - b.last_frame > max_gap_frames:
                b.closed = True
                out.append(b)
        return out

    def close_all(self) -> list[TrackBank]:
        out = [b for b in self.banks.values() if not b.closed]
        for b in out:
            b.closed = True
        return out
