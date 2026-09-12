"""Stage B step 3: robust temporal fusion of registered crops.

  weighted_median : per-pixel quality-weighted median (default; immune to a
                    single blown-out headlight frame)
  trimmed_mean    : per-pixel mean after dropping the top/bottom 20%
  shift_add_sr    : multi-frame super-resolution. Each crop is registered at
                    sub-pixel precision onto an s-times finer grid (shift-and-add),
                    then the accumulated image is deconvolved with a small
                    Gaussian PSF. This is the module that genuinely adds
                    information across frames.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from anpr.enhance.register import register, RegResult


@dataclass
class FusionResult:
    image: np.ndarray                   # uint8 gray (or BGR) fused image at native canonical size
    sr_image: np.ndarray | None         # uint8 gray at sr_scale x if shift_add_sr enabled
    n_used: int
    n_rejected: int
    reg_cc: list[float]
    weights: list[float]


def _weighted_median(stack: np.ndarray, w: np.ndarray) -> np.ndarray:
    """stack: (N,H,W) float, w: (N,) weights. Returns (H,W)."""
    n = stack.shape[0]
    order = np.argsort(stack, axis=0)
    sorted_vals = np.take_along_axis(stack, order, axis=0)
    w_b = np.broadcast_to(w[:, None, None], stack.shape)
    sorted_w = np.take_along_axis(w_b, order, axis=0)
    cum = np.cumsum(sorted_w, axis=0)
    half = cum[-1] / 2.0
    idx = (cum >= half[None]).argmax(axis=0)
    return np.take_along_axis(sorted_vals, idx[None], axis=0)[0]


def _trimmed_mean(stack: np.ndarray, trim: float = 0.2) -> np.ndarray:
    n = stack.shape[0]
    k = int(n * trim)
    s = np.sort(stack, axis=0)
    if n - 2 * k <= 0:
        return s.mean(0)
    return s[k:n - k].mean(0)


def fuse(crops: list[np.ndarray], weights: list[float] | None = None, method: str = "weighted_median",
         register_method: str = "ecc", min_cc: float = 0.25, sr_scale: int = 0,
         bloom_reject: float = 0.35) -> FusionResult:
    """crops: rectified crops (same size, BGR or gray). crops[0] should be the highest-quality one."""
    assert crops, "empty crop list"
    ref = crops[0]
    gray = [c if c.ndim == 2 else cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) for c in crops]
    w = np.asarray(weights if weights is not None else [1.0] * len(crops), np.float32)
    regs: list[RegResult] = [RegResult(gray[0], 1.0, True, "ref", 0.0)]
    for g in gray[1:]:
        regs.append(register(gray[0], g, register_method))
    kept_imgs, kept_w, ccs = [], [], []
    n_rej = 0
    for r, wi, g in zip(regs, w, gray):
        bloom = float((g >= 250).mean())
        if (not r.converged) or r.cc < min_cc or bloom > bloom_reject:
            n_rej += 1
            ccs.append(r.cc)
            continue
        kept_imgs.append(r.image.astype(np.float32))
        kept_w.append(float(wi) * max(r.cc, 0.05))
        ccs.append(r.cc)
    if not kept_imgs:  # keep at least the reference
        kept_imgs = [gray[0].astype(np.float32)]
        kept_w = [1.0]
    stack = np.stack(kept_imgs, 0)
    wv = np.asarray(kept_w, np.float32)
    wv = wv / (wv.sum() + 1e-9)
    if method == "trimmed_mean" or len(kept_imgs) < 3:
        fused = _trimmed_mean(stack) if len(kept_imgs) >= 5 else (stack * wv[:, None, None]).sum(0)
    elif method == "mean":
        fused = (stack * wv[:, None, None]).sum(0)
    else:
        fused = _weighted_median(stack, wv)
    fused_u8 = np.clip(fused, 0, 255).astype(np.uint8)
    sr = None
    if sr_scale and sr_scale > 1 and len(kept_imgs) >= 3:
        sr = shift_and_add(gray[0], gray[1:], w[1:], sr_scale, register_method, min_cc)
    return FusionResult(fused_u8, sr, len(kept_imgs), n_rej, ccs, kept_w)


def shift_and_add(ref: np.ndarray, others: list[np.ndarray], weights, scale: int = 3,
                  register_method: str = "ecc", min_cc: float = 0.25) -> np.ndarray:
    """Multi-frame SR: register each frame to the reference with sub-pixel
    accuracy, upsample the *warp* (not the pixels) onto a scale-x grid, accumulate
    with weights, normalise, then deconvolve a small Gaussian (Richardson-Lucy)."""
    h, w = ref.shape[:2]
    H, W = h * scale, w * scale
    acc = np.zeros((H, W), np.float64)
    wacc = np.zeros((H, W), np.float64)

    def splat(img: np.ndarray, M: np.ndarray, wt: float):
        # M maps ref coords -> img coords (inverse map). Scale to HR grid.
        S = np.array([[scale, 0, 0], [0, scale, 0], [0, 0, 1]], np.float64)
        Si = np.linalg.inv(S)
        M3 = np.vstack([M, [0, 0, 1]]) if M.shape[0] == 2 else M
        Mh = M3 @ Si   # HR ref pixel -> LR img pixel
        hr = cv2.warpPerspective(img.astype(np.float32), Mh.astype(np.float32), (W, H),
                                 flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
        acc[:] += wt * hr
        wacc[:] += wt

    splat(ref, np.eye(2, 3), 1.0)
    for img, wt in zip(others, weights):
        # recover the raw ECC warp so we can apply it on the HR grid
        r = _ecc_matrix(ref, img)
        if r is None or r[1] < min_cc:
            continue
        splat(img, r[0], float(wt) * max(r[1], 0.05))
    hr = acc / np.maximum(wacc, 1e-6)
    # deconvolve the interpolation blur: a few RL iterations with a Gaussian PSF
    hr = richardson_lucy(hr.astype(np.float32), sigma=0.6 * scale, iters=8)
    return np.clip(hr, 0, 255).astype(np.uint8)


def _ecc_matrix(ref: np.ndarray, mov: np.ndarray, upscale: int = 2):
    r = cv2.resize(ref.astype(np.float32) / 255.0, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    m = cv2.resize(mov.astype(np.float32) / 255.0, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        cc, warp = cv2.findTransformECC(r, m, warp, cv2.MOTION_EUCLIDEAN,
                                        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), None, 5)
    except cv2.error:
        return None
    S = np.diag([upscale, upscale, 1.0])
    Si = np.linalg.inv(S)
    W3 = np.vstack([warp, [0, 0, 1]]).astype(np.float64)
    Wn = (Si @ W3 @ S)[:2]
    return Wn, float(cc)


def richardson_lucy(img: np.ndarray, sigma: float = 1.0, iters: int = 8) -> np.ndarray:
    img = np.maximum(img.astype(np.float32), 1e-3)
    est = img.copy()
    k = int(max(3, 2 * round(3 * sigma) + 1))
    for _ in range(iters):
        conv = cv2.GaussianBlur(est, (k, k), sigma)
        ratio = img / np.maximum(conv, 1e-3)
        est *= cv2.GaussianBlur(ratio, (k, k), sigma)
    return est
