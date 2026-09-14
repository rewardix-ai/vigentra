"""Stage B step 6: deblur. PSF from the motion estimate (angle + extent);
Wiener / Richardson-Lucy fast path. Learned path (NAFNet ONNX) optional."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def motion_psf(extent: float, angle_deg: float, size: int | None = None) -> np.ndarray:
    L = max(1, int(round(extent)))
    if L <= 1:
        return np.array([[1.0]], np.float32)
    size = size or (L | 1)
    k = np.zeros((size, size), np.float32)
    c = size // 2
    k[c, :] = 1.0
    M = cv2.getRotationMatrix2D((c, c), angle_deg, 1.0)
    k = cv2.warpAffine(k, M, (size, size))
    s = k.sum()
    return k / s if s > 0 else np.array([[1.0]], np.float32)


def wiener(gray: np.ndarray, psf: np.ndarray, K: float = 0.01) -> np.ndarray:
    img = gray.astype(np.float32) / 255.0
    h, w = img.shape
    pad = np.zeros((h, w), np.float32)
    kh, kw = psf.shape
    pad[:kh, :kw] = psf
    pad = np.roll(pad, (-(kh // 2), -(kw // 2)), axis=(0, 1))
    Pf = np.fft.fft2(pad)
    If = np.fft.fft2(img)
    H = np.conj(Pf) / (np.abs(Pf) ** 2 + K)
    out = np.real(np.fft.ifft2(If * H))
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)


def richardson_lucy(gray: np.ndarray, psf: np.ndarray, iters: int = 10) -> np.ndarray:
    img = gray.astype(np.float32) / 255.0 + 1e-3
    est = img.copy()
    psf_m = np.flip(psf)
    for _ in range(iters):
        conv = cv2.filter2D(est, -1, psf, borderType=cv2.BORDER_REFLECT)
        ratio = img / np.maximum(conv, 1e-3)
        est *= cv2.filter2D(ratio, -1, psf_m, borderType=cv2.BORDER_REFLECT)
    return np.clip(est * 255.0, 0, 255).astype(np.uint8)


class Deblurrer:
    def __init__(self, onnx_path: str | Path | None = None, method: str = "wiener"):
        self.method = method
        self.sess = None
        if onnx_path and Path(onnx_path).exists():
            import onnxruntime as ort
            from anpr.enhance.denoise import _providers
            self.sess = ort.InferenceSession(str(onnx_path), providers=_providers())

    def __call__(self, gray: np.ndarray, extent: float, angle_deg: float) -> np.ndarray:
        if self.sess is not None:
            x = gray.astype(np.float32)[None, None] / 255.0
            y = self.sess.run(None, {self.sess.get_inputs()[0].name: x})[0][0, 0]
            return np.clip(y * 255.0, 0, 255).astype(np.uint8)
        if extent < 1.5:
            return gray
        psf = motion_psf(extent, angle_deg)
        if self.method == "rl":
            return richardson_lucy(gray, psf)
        return wiener(gray, psf)
