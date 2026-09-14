"""Stage B step 2: sub-pixel registration of rectified crops to a reference.
ECC (euclidean / homography) with a DIS optical-flow fallback. Convergence is
logged per crop so the loop can tell registration failures from fusion ones."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("anpr.enhance.register")


@dataclass
class RegResult:
    image: np.ndarray          # warped to reference frame (same dtype as input)
    cc: float                  # ECC correlation coefficient (or flow-based proxy)
    converged: bool
    method: str
    shift_px: float            # magnitude of translation applied


def _gray32(img: np.ndarray) -> np.ndarray:
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return g.astype(np.float32) / 255.0


def register_ecc(ref: np.ndarray, mov: np.ndarray, mode: str = "euclidean", iters: int = 80,
                 eps: float = 1e-5, upscale: int = 2) -> RegResult:
    """Register `mov` onto `ref`. Runs at `upscale` resolution for sub-pixel accuracy."""
    r = _gray32(ref)
    m = _gray32(mov)
    if upscale > 1:
        r = cv2.resize(r, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        m = cv2.resize(m, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    r = cv2.GaussianBlur(r, (3, 3), 0)
    m = cv2.GaussianBlur(m, (3, 3), 0)
    motion = {"translation": cv2.MOTION_TRANSLATION, "euclidean": cv2.MOTION_EUCLIDEAN,
              "affine": cv2.MOTION_AFFINE, "homography": cv2.MOTION_HOMOGRAPHY}[mode]
    warp = np.eye(3, dtype=np.float32) if motion == cv2.MOTION_HOMOGRAPHY else np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, eps)
    try:
        cc, warp = cv2.findTransformECC(r, m, warp, motion, crit, None, 5)
        converged = True
    except cv2.error as e:
        log.debug("ECC failed: %s", str(e).splitlines()[0])
        return RegResult(mov.copy(), 0.0, False, "ecc-" + mode, 0.0)
    h, w = mov.shape[:2]
    # scale warp back to native resolution
    S = np.diag([upscale, upscale, 1.0]).astype(np.float32)
    Si = np.linalg.inv(S)
    if motion == cv2.MOTION_HOMOGRAPHY:
        Wn = Si @ warp @ S
        out = cv2.warpPerspective(mov, Wn, (w, h), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                                  borderMode=cv2.BORDER_REPLICATE)
        shift = float(np.hypot(Wn[0, 2], Wn[1, 2]))
    else:
        W3 = np.vstack([warp, [0, 0, 1]]).astype(np.float32)
        Wn = (Si @ W3 @ S)[:2]
        out = cv2.warpAffine(mov, Wn, (w, h), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_REPLICATE)
        shift = float(np.hypot(Wn[0, 2], Wn[1, 2]))
    return RegResult(out, float(cc), converged, "ecc-" + mode, shift)


def register_flow(ref: np.ndarray, mov: np.ndarray) -> RegResult:
    r = (_gray32(ref) * 255).astype(np.uint8)
    m = (_gray32(mov) * 255).astype(np.uint8)
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    flow = dis.calc(m, r, None)
    h, w = m.shape
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    map_x = xs + flow[..., 0]
    map_y = ys + flow[..., 1]
    out = cv2.remap(mov, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    og = (_gray32(out) * 255).astype(np.uint8)
    cc = float(np.corrcoef(r.ravel().astype(np.float32), og.ravel().astype(np.float32))[0, 1])
    return RegResult(out, cc if np.isfinite(cc) else 0.0, True, "dis-flow", float(np.abs(flow).mean()))


def register(ref: np.ndarray, mov: np.ndarray, method: str = "ecc") -> RegResult:
    if method == "none":
        return RegResult(mov.copy(), 1.0, True, "none", 0.0)
    if method == "flow":
        return register_flow(ref, mov)
    res = register_ecc(ref, mov, "euclidean")
    if not res.converged or res.cc < 0.3:
        res2 = register_ecc(ref, mov, "translation")
        if res2.converged and res2.cc > res.cc:
            res = res2
    return res
