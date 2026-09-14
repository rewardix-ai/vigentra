"""Stage A[3]: 4-point plate corner estimation.

Fallback path (no learned head yet): inside the detector box, find the
bright quadrilateral via adaptive threshold + contour + minAreaRect, refine
with cv2.approxPolyDP; if nothing convincing is found, return the box corners.
Returns corners ordered (tl, tr, br, bl) in the same coordinate frame as the
input box, plus a confidence in [0,1] that the quad is real.
"""
from __future__ import annotations

import cv2
import numpy as np


def order_corners(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(1)
    d = np.diff(pts, axis=1).ravel()
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmin(d)], pts[np.argmax(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def box_corners(x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)


def estimate_corners(crop_bgr: np.ndarray, margin_frac: float = 0.0) -> tuple[np.ndarray, float]:
    """crop_bgr: the detector box region (optionally with margin). Returns (corners_in_crop_coords, conf)."""
    h, w = crop_bgr.shape[:2]
    fallback = box_corners(w * margin_frac, h * margin_frac, w * (1 - margin_frac), h * (1 - margin_frac))
    if h < 6 or w < 12:
        return fallback, 0.0
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    scale = 1.0
    if w < 120:
        scale = 120.0 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    g = cv2.GaussianBlur(gray, (3, 3), 0)
    # plates are the brightest low-saturation rectangle in the box (retro-reflective)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best, best_score = None, 0.0
    area_box = float(gray.shape[0] * gray.shape[1])
    for c in cnts:
        a = cv2.contourArea(c)
        if a < 0.15 * area_box:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (rw, rh), ang = rect
        if rw < 1 or rh < 1:
            continue
        long_, short_ = max(rw, rh), min(rw, rh)
        aspect = long_ / short_
        if not (1.2 <= aspect <= 6.5):
            continue
        fill = a / (rw * rh)
        score = fill * min(a / area_box, 1.0)
        if score > best_score:
            best_score, best = score, rect
    if best is None:
        return fallback, 0.0
    pts = cv2.boxPoints(best) / scale
    pts = order_corners(pts)
    # clamp to crop
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
    conf = float(np.clip(best_score, 0, 1))
    return pts, conf
