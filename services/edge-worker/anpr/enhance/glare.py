"""Stage B step 4: glare / bloom suppression. Works in LAB on L only.
Multi-scale retinex (MSR) local tone-map, then CLAHE with a small tile grid on
the character band. Global histogram equalisation is deliberately absent."""
from __future__ import annotations

import cv2
import numpy as np


def msr(L: np.ndarray, sigmas=(5, 15, 40)) -> np.ndarray:
    x = L.astype(np.float32) + 1.0
    out = np.zeros_like(x)
    for s in sigmas:
        blur = cv2.GaussianBlur(x, (0, 0), s)
        out += np.log(x) - np.log(blur + 1.0)
    out /= len(sigmas)
    lo, hi = np.percentile(out, 1), np.percentile(out, 99)
    out = (out - lo) / max(hi - lo, 1e-6)
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def suppress_glare(img: np.ndarray, clip: float = 2.0, grid=(4, 2), retinex: bool = True,
                   band=(0.12, 0.88)) -> np.ndarray:
    """img: gray or BGR uint8. Returns gray uint8."""
    if img.ndim == 3:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        L = lab[:, :, 0]
    else:
        L = img
    if retinex:
        L = msr(L)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=grid)
    h = L.shape[0]
    y0, y1 = int(h * band[0]), int(h * band[1])
    out = L.copy()
    out[y0:y1] = clahe.apply(L[y0:y1])
    return out
