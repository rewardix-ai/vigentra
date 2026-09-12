"""Stage B step 8: adaptive binarisation (Sauvola) + stroke normalisation.
Both grayscale and binarised versions are fed to the reader ensemble."""
from __future__ import annotations

import cv2
import numpy as np


def sauvola(gray: np.ndarray, window: int = 25, k: float = 0.2, R: float = 128.0) -> np.ndarray:
    g = gray.astype(np.float32)
    mean = cv2.boxFilter(g, -1, (window, window), borderType=cv2.BORDER_REFLECT)
    sq = cv2.boxFilter(g * g, -1, (window, window), borderType=cv2.BORDER_REFLECT)
    std = np.sqrt(np.maximum(sq - mean * mean, 0))
    thr = mean * (1 + k * (std / R - 1))
    return ((g > thr) * 255).astype(np.uint8)


def polarity_normalise(gray: np.ndarray) -> np.ndarray:
    """Return dark-text-on-light-ground. Plates in India can be either
    polarity (white/yellow ground vs black/green ground)."""
    h, w = gray.shape[:2]
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    centre = gray[h // 4: 3 * h // 4, w // 8: 7 * w // 8]
    if border.mean() < centre.mean():
        return 255 - gray
    return gray


def binarise(gray: np.ndarray) -> np.ndarray:
    g = polarity_normalise(gray)
    b = sauvola(g, window=max(9, (g.shape[1] // 12) | 1), k=0.25)
    # remove specks
    b = cv2.morphologyEx(b, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return b


def stroke_normalise(binary: np.ndarray, target_stroke: int = 4) -> np.ndarray:
    """Estimate mean stroke width via distance transform and dilate/erode toward target."""
    ink = (binary < 128).astype(np.uint8)
    if ink.sum() < 10:
        return binary
    dt = cv2.distanceTransform(ink, cv2.DIST_L2, 3)
    sw = float(dt[ink > 0].mean() * 2.0)
    if sw <= 0:
        return binary
    delta = int(round((target_stroke - sw) / 2.0))
    if delta == 0:
        return binary
    k = np.ones((abs(delta) * 2 + 1,) * 2, np.uint8)
    ink2 = cv2.dilate(ink, k) if delta > 0 else cv2.erode(ink, k)
    return ((1 - ink2) * 255).astype(np.uint8)
