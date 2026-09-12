"""Stage B step 1: perspective rectification to a canonical plate canvas.
Single-row: 4.2:1 -> 384x92. Two-row: 2:1 -> 256x128."""
from __future__ import annotations

import cv2
import numpy as np

CANON_SINGLE = (384, 92)
CANON_TWO_ROW = (256, 128)


def canonical_size(two_row: bool) -> tuple[int, int]:
    return CANON_TWO_ROW if two_row else CANON_SINGLE


def rectify(crop_bgr: np.ndarray, corners: np.ndarray | None, two_row: bool = False,
            size: tuple[int, int] | None = None, interpolation: int = cv2.INTER_CUBIC) -> np.ndarray:
    """Warp the quadrilateral `corners` (tl,tr,br,bl in crop coords) to a
    canonical canvas. If corners is None, use the crop's full extent."""
    W, H = size or canonical_size(two_row)
    h, w = crop_bgr.shape[:2]
    if h < 2 or w < 4:
        # degenerate crop (e.g. a 3 px proposer box): return a blank canvas rather than crash
        return np.zeros((H, W, 3) if crop_bgr.ndim == 3 else (H, W), np.uint8)
    if corners is None:
        src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    else:
        src = np.asarray(corners, np.float32).reshape(4, 2)
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(crop_bgr, M, (W, H), flags=interpolation, borderMode=cv2.BORDER_REPLICATE)


def deskew_residual(gray: np.ndarray, max_deg: float = 12.0) -> tuple[np.ndarray, float]:
    """Small residual rotation via Hough on strong horizontal edges."""
    edges = cv2.Canny(gray, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=max(20, gray.shape[1] // 6),
                            minLineLength=gray.shape[1] // 3, maxLineGap=8)
    if lines is None:
        return gray, 0.0
    angs = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) <= max_deg:
            angs.append(a)
    if not angs:
        return gray, 0.0
    ang = float(np.median(angs))
    h, w = gray.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE), ang
