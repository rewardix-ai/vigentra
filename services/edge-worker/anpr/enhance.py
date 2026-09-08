"""Plate-crop quality assessment and restoration.

The pipeline never trusts a single rendering of a plate.  Instead it measures
what is actually wrong with a crop (too dark, blown out by headlights, out of
focus, too few pixels, skewed) and then generates a small, *targeted* set of
restored variants.  Every variant is OCR'd and the readings are voted on, so
a plate only has to be legible in one of them.

Generating all variants unconditionally would be wasteful, so variant choice
is driven by the measured defects and capped by ``EnhanceConfig.max_variants``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import MODELS_DIR, EnhanceConfig

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Quality assessment
# --------------------------------------------------------------------------

@dataclass
class Quality:
    """What is wrong with this crop, on comparable 0..1-ish scales."""
    width: int
    height: int
    luma: float          #: mean brightness, 0..255
    contrast: float      #: std-dev of luma
    sharpness: float     #: variance of Laplacian - higher is sharper
    glare: float         #: fraction of specular / blown-out pixels
    dark: float          #: fraction of near-black pixels
    score: float = 0.0   #: single 0..1 "how usable is this crop" figure

    @property
    def is_dark(self) -> bool:
        return self.luma < 85.0

    @property
    def is_glared(self) -> bool:
        return self.glare > 0.035

    @property
    def is_blurred(self) -> bool:
        return self.sharpness < 90.0

    def as_dict(self) -> dict:
        return {
            "width": self.width, "height": self.height,
            "luma": round(self.luma, 1), "contrast": round(self.contrast, 1),
            "sharpness": round(self.sharpness, 1), "glare": round(self.glare, 4),
            "dark": round(self.dark, 4), "score": round(self.score, 3),
        }


#: Value at which an 8-bit sensor is effectively clipped.
CLIP_LEVEL = 250
#: Local std-dev below which a patch carries no recoverable detail.
FLAT_STD = 6.0


def _glare_ratio(bgr: np.ndarray) -> float:
    """Fraction of the crop destroyed by specular blowout.

    Brightness alone is a bad test: an Indian plate is a white background by
    design, so "bright and desaturated" describes a *correctly exposed* plate
    and would report every clean crop as glared.  Real glare has two
    signatures instead - the sensor is **clipped**, and the affected patch is
    **flat**, having lost the character strokes entirely.
    """
    v = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32)
    clipped = v >= CLIP_LEVEL
    if not clipped.any():
        return 0.0
    # Local standard deviation via the E[x^2] - E[x]^2 identity.
    mean = cv2.boxFilter(v, -1, (9, 9), normalize=True)
    mean_sq = cv2.boxFilter(v * v, -1, (9, 9), normalize=True)
    std = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))
    return float((clipped & (std < FLAT_STD)).mean())


def assess(bgr: np.ndarray) -> Quality:
    """Measure the defects of a plate crop."""
    if bgr is None or bgr.size == 0:
        return Quality(0, 0, 0, 0, 0, 0, 1.0, 0.0)

    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    luma = float(gray.mean())
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    glare = _glare_ratio(bgr)
    dark = float((gray < 40).mean())

    # Blend into one usability figure.  Resolution dominates: no amount of
    # contrast rescues a 30 px-wide plate.
    res_term = np.clip(w / 180.0, 0.0, 1.0)
    sharp_term = np.clip(sharpness / 250.0, 0.0, 1.0)
    contrast_term = np.clip(contrast / 60.0, 0.0, 1.0)
    expo_term = 1.0 - abs(luma - 128.0) / 128.0
    penalty = 1.0 - np.clip(glare * 4.0, 0.0, 0.6)
    score = float(
        (0.36 * res_term + 0.30 * sharp_term + 0.20 * contrast_term
         + 0.14 * expo_term) * penalty
    )

    return Quality(w, h, luma, contrast, sharpness, glare, dark, score)


# --------------------------------------------------------------------------
# Geometry - perspective rectification
# --------------------------------------------------------------------------

def rectify(bgr: np.ndarray, *, max_skew: float = 30.0) -> np.ndarray:
    """Flatten a plate photographed off-axis.

    CCTV almost never looks square-on at a plate, and OCR recognisers are
    trained on upright text, so this is one of the cheapest large wins in the
    pipeline.  Falls back to the original crop whenever the geometry it finds
    looks implausible - a bad warp is much worse than no warp.
    """
    if bgr is None or bgr.size == 0:
        return bgr
    h, w = bgr.shape[:2]
    if w < 24 or h < 12:
        return bgr

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 60, 60)
    edges = cv2.Canny(gray, 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return _deskew_by_text(bgr, max_skew)

    frame_area = float(w * h)
    best_quad = None
    best_area = 0.0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 0.25 * frame_area or area <= best_area:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            best_quad, best_area = approx.reshape(4, 2).astype(np.float32), area

    if best_quad is None:
        return _deskew_by_text(bgr, max_skew)

    src = _order_corners(best_quad)
    tw = int(max(np.linalg.norm(src[1] - src[0]), np.linalg.norm(src[2] - src[3])))
    th = int(max(np.linalg.norm(src[3] - src[0]), np.linalg.norm(src[2] - src[1])))
    if tw < 24 or th < 12:
        return bgr
    # Indian plates are ~4.5:1 (single row) or ~2:1 (two rows).  Anything
    # outside that band means we latched onto the wrong contour.
    if not (1.3 <= tw / max(th, 1) <= 7.0):
        return _deskew_by_text(bgr, max_skew)

    dst = np.array([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], np.float32)
    M = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(bgr, M, (tw, th), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)],   # top-left  has the smallest x+y
        pts[np.argmin(d)],   # top-right has the smallest y-x
        pts[np.argmax(s)],   # bottom-right
        pts[np.argmax(d)],   # bottom-left
    ], dtype=np.float32)


def _deskew_by_text(bgr: np.ndarray, max_skew: float) -> np.ndarray:
    """Rotation-only fallback: straighten using the dominant text angle."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(thr)
    if coords is None or len(coords) < 32:
        return bgr
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    if abs(angle) < 1.0 or abs(angle) > max_skew:
        return bgr
    h, w = bgr.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(bgr, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


# --------------------------------------------------------------------------
# Restoration primitives
# --------------------------------------------------------------------------

def _clahe(bgr: np.ndarray, clip: float = 2.5, grid: int = 8) -> np.ndarray:
    """Local contrast equalisation on the L channel only, so colour holds."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=clip, tileGridSize=(grid, grid)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def _unsharp(bgr: np.ndarray, amount: float = 1.1, radius: float = 1.4) -> np.ndarray:
    blur = cv2.GaussianBlur(bgr, (0, 0), radius)
    return cv2.addWeighted(bgr, 1.0 + amount, blur, -amount, 0)


def _auto_gamma(bgr: np.ndarray, target: float = 128.0) -> np.ndarray:
    """Pull the mean brightness toward *target* without clipping highlights."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    if mean < 1.0:
        return bgr
    gamma = float(np.log(target / 255.0) / np.log(max(mean, 1.0) / 255.0))
    gamma = float(np.clip(gamma, 0.35, 3.0))
    lut = np.array([((i / 255.0) ** (1.0 / gamma)) * 255 for i in range(256)],
                   dtype=np.uint8)
    return cv2.LUT(bgr, lut)


def suppress_glare(bgr: np.ndarray) -> np.ndarray:
    """Repair headlight / retro-reflective blowout.

    Indian HSRP plates are retro-reflective, so a headlight or the sun turns
    whole characters into flat white.  Those pixels carry no information, so
    we inpaint them from their surroundings and then re-normalise the
    illumination, which recovers the character strokes at the blowout edge.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Only inpaint genuinely clipped pixels.  A lower threshold would eat the
    # white background of every correctly-exposed plate.
    mask = ((hsv[:, :, 2] >= CLIP_LEVEL) & (hsv[:, :, 1] < 48)).astype(np.uint8) * 255
    if mask.mean() > 2.0:                       # something to repair
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
        bgr = cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA)

    # Flatten the remaining illumination gradient (a large-kernel division is
    # a cheap single-scale retinex) so one bright corner stops dominating.
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) + 1.0
    illum = cv2.GaussianBlur(gray, (0, 0), max(bgr.shape[1] / 12.0, 3.0))
    flat = np.clip((gray / illum) * 128.0, 0, 255).astype(np.uint8)
    flat = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(flat)
    return cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)


def brighten_lowlight(bgr: np.ndarray) -> np.ndarray:
    """Night / IR / dusk branch: lift exposure, then kill the amplified noise.

    Order matters.  Denoising before the gamma lift removes detail that was
    never visible; lifting first and denoising after keeps the strokes.
    """
    out = _auto_gamma(bgr, target=140.0)
    out = cv2.fastNlMeansDenoisingColored(out, None, 6, 6, 7, 21)
    out = _clahe(out, clip=3.0, grid=8)
    return _unsharp(out, amount=0.9, radius=1.2)


def binarise(bgr: np.ndarray) -> np.ndarray:
    """High-contrast two-tone rendering; rescues washed-out, flat crops."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 5, 50, 50)
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 25, 11)
    # Plates are dark-on-light or light-on-dark; normalise to dark-on-light
    # because that is what the recognisers were trained on.
    if thr.mean() < 127:
        thr = cv2.bitwise_not(thr)
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return cv2.cvtColor(thr, cv2.COLOR_GRAY2BGR)


# --------------------------------------------------------------------------
# Super-resolution
# --------------------------------------------------------------------------

class SuperResolver:
    """Upscales tiny plates.

    Uses an OpenCV ``dnn_superres`` model when its weights are on disk and
    otherwise degrades to Lanczos + unsharp, which is markedly better than
    nothing and never fails.  Keeping the fallback means the pipeline runs
    on a fresh checkout with no downloads at all.
    """

    #: filename -> (algo name, scale) for the models we know how to load
    KNOWN = {
        "ESPCN_x4.pb": ("espcn", 4),
        "FSRCNN_x4.pb": ("fsrcnn", 4),
        "EDSR_x4.pb": ("edsr", 4),
        "ESPCN_x3.pb": ("espcn", 3),
        "FSRCNN_x3.pb": ("fsrcnn", 3),
    }

    #: Weights for the plate-trained upscaler (anpr/sr.py), looked for first
    #: under "auto": it was trained on this footage's own degradation, where
    #: the photographic ESPCN weights were not.
    PLATE_SR = "plate_sr.pt"

    def __init__(self, backend: str = "auto", scale: int = 4) -> None:
        self.backend = backend
        self.scale = scale
        self._net = None
        self._plate = None
        self._name = "lanczos"
        if backend in ("off", "lanczos"):
            return
        if backend in ("auto", "plate"):
            self._try_load_plate()
        if self._plate is None and backend != "plate":
            self._try_load(backend)

    def _try_load_plate(self) -> None:
        path = MODELS_DIR / self.PLATE_SR
        if not path.exists():
            if self.backend == "plate":
                log.info("no %s in %s; using Lanczos upscaling", self.PLATE_SR, MODELS_DIR)
            return
        try:
            from .sr import PlateUpscaler
            up = PlateUpscaler(str(path), "cpu")
            if up.scale != self.scale:
                log.warning("%s is x%d, configured scale is x%d; ignoring it",
                            self.PLATE_SR, up.scale, self.scale)
                return
            self._plate, self._name = up, up.name
            log.info("super-resolution: %s (plate-trained)", self._name)
        except Exception as exc:                # noqa: BLE001 - optional
            log.warning("failed to load %s: %s", path, exc)

    def _try_load(self, backend: str) -> None:
        try:
            sr = cv2.dnn_superres.DnnSuperResImpl_create()
        except AttributeError:
            log.info("opencv built without dnn_superres; using Lanczos upscaling")
            return
        for fname, (algo, scale) in self.KNOWN.items():
            if backend not in ("auto", algo) or scale != self.scale:
                continue
            path = MODELS_DIR / fname
            if not path.exists():
                continue
            try:
                sr.readModel(str(path))
                sr.setModel(algo, scale)
                self._net, self._name = sr, f"{algo}_x{scale}"
                log.info("super-resolution: %s", self._name)
                return
            except cv2.error as exc:
                log.warning("failed to load SR model %s: %s", fname, exc)
        log.info("no SR weights in %s; using Lanczos upscaling", MODELS_DIR)

    @property
    def name(self) -> str:
        return self._name

    def upscale(self, bgr: np.ndarray) -> np.ndarray:
        if self.backend == "off":
            return bgr
        if self._plate is not None and bgr.shape[0] * bgr.shape[1] <= 160 * 640:
            try:
                return self._plate.upscale(bgr)
            except Exception as exc:            # noqa: BLE001 - fall through
                log.debug("plate SR failed (%s); falling back", exc)
        if self._net is not None:
            # These nets are cheap on small crops but blow up on large ones,
            # so only feed them genuinely small plates.
            if bgr.shape[0] * bgr.shape[1] <= 128 * 512:
                try:
                    return self._net.upsample(bgr)
                except cv2.error as exc:
                    log.debug("SR upsample failed, falling back: %s", exc)
        h, w = bgr.shape[:2]
        big = cv2.resize(bgr, (w * self.scale, h * self.scale),
                         interpolation=cv2.INTER_LANCZOS4)
        return _unsharp(big, amount=0.8, radius=1.6)


# --------------------------------------------------------------------------
# Variant generation
# --------------------------------------------------------------------------

def _fit_height(bgr: np.ndarray, height: int) -> np.ndarray:
    """Scale to the recogniser's expected height, preserving aspect."""
    h, w = bgr.shape[:2]
    if h == 0 or w == 0:
        return bgr
    scale = height / float(h)
    # Upscaling wants Lanczos/cubic, downscaling wants area averaging.
    interp = cv2.INTER_LANCZOS4 if scale > 1.0 else cv2.INTER_AREA
    new_w = max(8, int(round(w * scale)))
    return cv2.resize(bgr, (new_w, height), interpolation=interp)


def _pad(bgr: np.ndarray, px: int = 6) -> np.ndarray:
    """Recognisers do badly when glyphs touch the border."""
    return cv2.copyMakeBorder(bgr, px, px, px, px, cv2.BORDER_REPLICATE)


def build_variants(crop: np.ndarray, cfg: EnhanceConfig,
                   sr: SuperResolver | None = None,
                   quality: Quality | None = None) -> list[tuple[str, np.ndarray]]:
    """Produce the targeted set of restored renderings for one plate crop.

    Returns a list of ``(variant_name, image)``, ordered most-promising first
    and truncated to ``cfg.max_variants``.
    """
    if crop is None or crop.size == 0:
        return []

    q = quality or assess(crop)
    base = crop

    if cfg.rectify:
        try:
            base = rectify(base)
        except cv2.error as exc:                # never let geometry kill a frame
            log.debug("rectify failed: %s", exc)
            base = crop

    # Small plates get super-resolved before anything else, so every later
    # variant works on the larger image.
    if sr is not None and q.width < cfg.sr_width_threshold and cfg.sr_backend != "off":
        try:
            base = sr.upscale(base)
        except Exception as exc:                # noqa: BLE001 - SR is optional
            log.debug("super-resolution failed: %s", exc)

    variants: list[tuple[str, np.ndarray]] = []

    def add(name: str, img: np.ndarray) -> None:
        if img is not None and img.size:
            variants.append((name, _pad(_fit_height(img, cfg.ocr_height))))

    # Ordered by expected payoff for the defects we actually measured, so the
    # max_variants truncation keeps the ones that matter.
    add("clahe", _unsharp(_clahe(base, cfg.clahe_clip, cfg.clahe_grid)))
    if q.is_dark:
        add("lowlight", brighten_lowlight(base))
    if q.is_glared:
        add("glare", suppress_glare(base))
    add("base", base)
    if q.is_blurred:
        add("sharp", _unsharp(base, amount=1.8, radius=2.0))
    if q.contrast < 45.0:
        add("binary", binarise(base))

    # The crop exactly as it left the frame, at its own resolution.
    #
    # Every other variant is fitted to `ocr_height`, which for a plate already
    # 80-100px wide is a 4x upscale, so the recogniser partly reads the
    # interpolation. The untouched pixels sometimes disagree with the fitted
    # ones, and that disagreement is useful: it is what the cross-variant vote
    # in the OCR ensemble resolves. Only padded, because a glyph touching the
    # border reads badly and padding does not resample.
    #
    # LAST on purpose. It is another opinion, not a better one - measured on
    # this estate it reads nothing on some crops and misreads others, so it must
    # never win a tie against a variant that actually read the plate.
    if crop is not None and crop.size:
        variants.append(("native", _pad(crop)))

    return variants[: max(1, cfg.max_variants)]
