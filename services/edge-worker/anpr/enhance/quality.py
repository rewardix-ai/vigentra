"""Crop-bank quality scoring (spec 7.1).

Each plate crop gets: sharpness (variance of Laplacian + Tenengrad), width,
skew angle, local contrast (CLAHE-normalised std of the character band),
bloom fraction, motion-blur direction/extent. Combined into quality_score.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np


@dataclass
class CropQuality:
    width_px: float
    height_px: float
    sharpness_lap: float
    tenengrad: float
    skew_deg: float
    local_contrast: float
    bloom_frac: float
    blur_extent: float
    blur_angle_deg: float
    dark_frac: float
    quality_score: float

    def as_dict(self) -> dict:
        return asdict(self)


def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def sharpness(gray: np.ndarray) -> tuple[float, float]:
    g = gray.astype(np.float32)
    lap = cv2.Laplacian(g, cv2.CV_32F).var()
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    ten = float(np.mean(gx * gx + gy * gy))
    return float(lap), ten


def local_contrast(gray: np.ndarray) -> float:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 2))
    h = gray.shape[0]
    band = gray[int(h * 0.15):int(h * 0.85), :]
    if band.size == 0:
        return 0.0
    return float(clahe.apply(band).std())


def bloom_fraction(gray: np.ndarray, thr: int = 250) -> float:
    return float((gray >= thr).mean())


def dark_fraction(gray: np.ndarray, thr: int = 20) -> float:
    return float((gray <= thr).mean())


def motion_blur_estimate(gray: np.ndarray) -> tuple[float, float]:
    """Estimate blur extent (px) and angle (deg) from the gradient-orientation
    anisotropy of the power spectrum. Cheap and robust enough for weighting."""
    g = gray.astype(np.float32)
    if min(g.shape) < 8:
        return 0.0, 0.0
    g = (g - g.mean()) / (g.std() + 1e-6)
    f = np.fft.fftshift(np.abs(np.fft.fft2(g * np.hanning(g.shape[0])[:, None] * np.hanning(g.shape[1])[None, :])))
    f = np.log1p(f)
    h, w = f.shape
    cy, cx = h // 2, w // 2
    ys, xs = np.mgrid[0:h, 0:w]
    ang = np.arctan2(ys - cy, xs - cx)
    r = np.hypot(ys - cy, xs - cx)
    valid = (r > 2) & (r < min(h, w) // 2 - 1)
    if valid.sum() < 16:
        return 0.0, 0.0
    bins = 18
    idx = ((ang[valid] + np.pi) / (2 * np.pi) * bins).astype(int) % bins
    energy = np.bincount(idx, weights=f[valid], minlength=bins)
    energy = energy / (energy.mean() + 1e-6)
    anis = float(energy.max() - energy.min())
    blur_angle = float((energy.argmax() / bins) * 180.0)
    # anisotropy -> pseudo extent in px, clipped; calibrated later on synthetic data
    extent = float(np.clip((anis - 0.3) * 6.0, 0.0, 20.0))
    return extent, blur_angle


def skew_from_corners(corners: np.ndarray | None) -> float:
    if corners is None:
        return 0.0
    c = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    top = c[1] - c[0]
    return float(abs(np.degrees(np.arctan2(top[1], top[0]))))


NORM_W = 128  # sharpness/contrast are measured on a width-normalised copy so crops of different sizes compare


def assess(crop_bgr: np.ndarray, corners: np.ndarray | None = None) -> CropQuality:
    gray = _gray(crop_bgr)
    h, w = gray.shape[:2]
    # Laplacian variance on a tiny native crop is dominated by codec/edge noise
    # (a 6 px high streak scored 1900 on the sample clip). Normalise width first:
    # upscaling a blurred crop cannot invent gradients, so a genuinely sharp
    # 90 px plate and a 20 px smear now land on comparable scales.
    if w > 4 and h > 2:
        s = NORM_W / w
        norm = cv2.resize(gray, (NORM_W, max(4, int(round(h * s)))),
                          interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    else:
        norm = gray
    lap, ten = sharpness(norm)
    lc = local_contrast(norm)
    bloom = bloom_fraction(gray)
    dark = dark_fraction(gray)
    ext, ang = motion_blur_estimate(gray)
    skew = skew_from_corners(corners)
    # --- combine ---------------------------------------------------------
    # width term saturates ~90 px (single-frame legibility), floors at 12 px
    s_w = float(np.clip((w - 12) / (90 - 12), 0, 1))
    # sharpness measured at NORM_W; scale set from synthetic clean (~600+) vs 20 px degraded (~<60) crops
    s_sharp = float(np.clip(lap / 300.0, 0, 1))
    if h < 8:
        s_w *= 0.3  # a plate thinner than 8 px cannot carry legal-height characters
    s_con = float(np.clip(lc / 45.0, 0, 1))
    s_bloom = float(np.clip(1.0 - bloom * 4.0, 0, 1))
    s_dark = float(np.clip(1.0 - dark * 2.0, 0, 1))
    s_blur = float(np.clip(1.0 - ext / 12.0, 0, 1))
    s_skew = float(np.clip(1.0 - skew / 30.0, 0, 1))
    q = (0.30 * s_w + 0.25 * s_sharp + 0.15 * s_con + 0.10 * s_bloom + 0.05 * s_dark
         + 0.10 * s_blur + 0.05 * s_skew)
    return CropQuality(w, h, lap, ten, skew, lc, bloom, ext, ang, dark, float(q))
