"""Stage A[2]: plate detector, run only inside vehicle boxes at upscaled
resolution, with a retro-reflective candidate proposer as a cheap pre-filter
and geometry-prior soft scoring (spec 6.2.3). Every rejection is logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from anpr.detect.tiling import make_tiles, merge_tile_dets, nms
from anpr.plate_grammar import ASPECT_ANY, ASPECT_TWO_ROW, PLATE_H_FRAC_OF_VEHICLE

log = logging.getLogger("anpr.detect.plate")


@dataclass
class PlateDet:
    box: tuple[float, float, float, float]     # frame coords
    conf: float                                 # detector conf * geometry prior
    raw_conf: float
    geom_prior: float
    two_row: bool
    source: str                                 # 'cnn' | 'retro'
    rejections: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Retro-reflective proposer: bright, low-saturation, roughly rectangular blobs
# with high local contrast. Works on the (upscaled) vehicle crop.
# ---------------------------------------------------------------------------
def retro_proposals(vehicle_bgr: np.ndarray, min_w: int = 10, max_frac_w: float = 0.9) -> list[tuple[float, float, float, float, float]]:
    h, w = vehicle_bgr.shape[:2]
    if h < 16 or w < 16:
        return []
    hsv = cv2.cvtColor(vehicle_bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    # local brightness relative to neighbourhood
    blur = cv2.GaussianBlur(v, (0, 0), max(3, w / 40))
    rel = cv2.subtract(v, blur)
    m1 = (rel > 25) & (v > 110) & (s < 110)
    # also accept yellow plates (commercial): high V, hue ~20-35
    hue = hsv[:, :, 0]
    m2 = (v > 120) & (s > 80) & (hue >= 15) & (hue <= 40)
    m = (m1 | m2).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, a = stats[i]
        if bw < min_w or bh < 4 or bw > max_frac_w * w:
            continue
        asp = bw / max(bh, 1)
        if not (ASPECT_ANY[0] <= asp <= ASPECT_ANY[1]):
            continue
        fill = a / float(bw * bh)
        if fill < 0.45:
            continue
        # local contrast inside: characters produce texture
        roi = v[y:y + bh, x:x + bw]
        tex = float(cv2.Laplacian(roi, cv2.CV_32F).var())
        score = min(1.0, 0.4 * fill + 0.3 * min(tex / 200.0, 1.0) + 0.3 * min(asp / 4.0, 1.0))
        out.append((float(x), float(y), float(x + bw), float(y + bh), score))
    return out


def geometry_prior(box, vehicle_box, vehicle_type: str) -> tuple[float, bool, list[str]]:
    x1, y1, x2, y2 = box
    vw = max(vehicle_box[2] - vehicle_box[0], 1)
    vh = max(vehicle_box[3] - vehicle_box[1], 1)
    w, h = max(x2 - x1, 1), max(y2 - y1, 1)
    asp = w / h
    reasons = []
    p = 1.0
    two_row = asp < ASPECT_TWO_ROW[1]
    lo, hi = ASPECT_ANY
    if asp < lo:
        p *= max(0.1, asp / lo)
        reasons.append(f"aspect_low:{asp:.2f}")
    elif asp > hi:
        p *= max(0.1, hi / asp)
        reasons.append(f"aspect_high:{asp:.2f}")
    frac = h / vh
    flo, fhi = PLATE_H_FRAC_OF_VEHICLE
    if vehicle_type in ("motorcycle", "auto"):
        flo, fhi = 0.05, 0.40   # two-wheelers and auto-rickshaws: small two-row plates, larger share of the box
    if frac < flo:
        p *= max(0.2, frac / flo)
        reasons.append(f"plate_h_frac_low:{frac:.3f}")
    elif frac > fhi:
        p *= max(0.2, fhi / frac)
        reasons.append(f"plate_h_frac_high:{frac:.3f}")
    # plates sit in the lower 75% of the vehicle box for rear/front views
    cy = (y1 + y2) / 2
    rel_y = (cy - vehicle_box[1]) / vh
    if rel_y < 0.25:
        p *= 0.5
        reasons.append(f"plate_too_high:{rel_y:.2f}")
    if w > 0.9 * vw:
        p *= 0.3
        reasons.append("plate_wider_than_vehicle")
    return float(np.clip(p, 0, 1)), two_row, reasons


class PlateDetector:
    """YOLO plate detector run per vehicle crop with optional tiling.
    If weights are missing, falls back to the retro-reflective proposer alone."""

    def __init__(self, weights: Optional[str | Path] = "models/plate_det.pt", device: str = "auto",
                 imgsz: int = 640, conf: float = 0.2, upscale_min_px: int = 640, tile: int = 0,
                 overlap: float = 0.2, use_retro: bool = True, half: bool = True):
        self.model = None
        if weights and Path(weights).exists():
            from ultralytics import YOLO
            self.model = YOLO(str(weights))
        else:
            log.warning("plate detector weights not found (%s); retro proposer only", weights)
        from anpr.detect.vehicle import _resolve_device
        self.device = _resolve_device(device)
        self.imgsz = imgsz
        self.conf = conf
        self.upscale_min_px = upscale_min_px
        self.tile = tile
        self.overlap = overlap
        self.use_retro = use_retro
        self.half = half and self.device != "cpu"
        self.rejection_log: list[dict] = []

    # ------------------------------------------------------------------
    def _run_cnn(self, img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h, w = img.shape[:2]
        if self.tile and (w > self.tile or h > self.tile):
            tiles = make_tiles(w, h, self.tile, self.overlap)
            dets = []
            for t in tiles:
                sub = img[t.y0:t.y1, t.x0:t.x1]
                b, s = self._infer(sub)
                dets.append((b, s, t))
            return merge_tile_dets(dets, 0.5)
        return self._infer(img)

    def _infer(self, img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        res = self.model.predict(img, imgsz=self.imgsz, conf=self.conf, device=self.device, verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
        return res.boxes.xyxy.cpu().numpy().astype(np.float32), res.boxes.conf.cpu().numpy().astype(np.float32)

    # ------------------------------------------------------------------
    def detect_in_vehicle(self, frame_bgr: np.ndarray, vehicle_box, vehicle_type: str = "car",
                          frame_idx: int = -1, track_id: str = "") -> list[PlateDet]:
        H, W = frame_bgr.shape[:2]
        x1, y1, x2, y2 = vehicle_box
        # margin so plates at the box edge are not clipped
        mw, mh = 0.05 * (x2 - x1), 0.05 * (y2 - y1)
        cx1, cy1 = int(max(0, x1 - mw)), int(max(0, y1 - mh))
        cx2, cy2 = int(min(W, x2 + mw)), int(min(H, y2 + mh))
        if cx2 - cx1 < 8 or cy2 - cy1 < 8:
            return []
        crop = frame_bgr[cy1:cy2, cx1:cx2]
        ch, cw = crop.shape[:2]
        scale = 1.0
        long_side = max(ch, cw)
        if long_side < self.upscale_min_px:
            scale = self.upscale_min_px / long_side
            crop_up = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        else:
            crop_up = crop
        cands: list[tuple[np.ndarray, float, str]] = []
        if self.model is not None:
            b, s = self._run_cnn(crop_up)
            for bb, ss in zip(b, s):
                cands.append((bb / scale, float(ss), "cnn"))
        if self.use_retro:
            for (rx1, ry1, rx2, ry2, sc) in retro_proposals(crop_up):
                cands.append((np.array([rx1, ry1, rx2, ry2], np.float32) / scale, float(sc) * 0.6, "retro"))
        if not cands:
            return []
        out: list[PlateDet] = []
        for bb, sc, src in cands:
            fb = (cx1 + float(bb[0]), cy1 + float(bb[1]), cx1 + float(bb[2]), cy1 + float(bb[3]))
            if fb[2] - fb[0] < 8 or fb[3] - fb[1] < 3:
                continue  # narrower than one character at native resolution: not a plate
            gp, two_row, reasons = geometry_prior(fb, vehicle_box, vehicle_type)
            if reasons:
                self.rejection_log.append({"frame": frame_idx, "track": track_id, "src": src, "box": fb,
                                           "raw_conf": sc, "geom_prior": gp, "reasons": reasons})
                if len(self.rejection_log) > 5000:   # bounded: the dev box has ~1 GB of headroom
                    del self.rejection_log[:2500]
            out.append(PlateDet(fb, sc * gp, sc, gp, two_row, src, reasons))
        # class-agnostic NMS across CNN + retro candidates
        B = np.array([d.box for d in out], np.float32)
        S = np.array([d.conf for d in out], np.float32)
        keep = nms(B, S, 0.4)
        out = [out[i] for i in keep]
        # keep at most 2 plates per vehicle (front + rear can both be visible)
        return sorted(out, key=lambda d: d.conf, reverse=True)[:2]
