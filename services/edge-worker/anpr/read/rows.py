"""Two-row plate handling (spec 8.2): detect row count from aspect + horizontal
projection profile, split, and return row crops top-to-bottom."""
from __future__ import annotations

import cv2
import numpy as np


def detect_rows(gray: np.ndarray, aspect_hint: float | None = None) -> int:
    h, w = gray.shape[:2]
    asp = aspect_hint if aspect_hint else w / max(h, 1)
    # Measured on 501 real single-row plates (Gamester03 holdout): the previous
    # rule flagged 47 of them as two-row and cost ~4 points of exact match.
    # Indian single-row plates are >= 2.5:1 even with a loose crop; two-row
    # plates (bikes/autos) are 1.2-2.0:1. Be conservative: aspect first, and
    # only then require BOTH a deep ink valley in the middle band AND two
    # separate ink bands above and below it.
    if asp >= 2.4:
        return 1
    if asp <= 1.6:
        return 2
    from anpr.enhance.binarize import polarity_normalise
    g = polarity_normalise(gray)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    ink = (255 - g).astype(np.float32)
    prof = ink.mean(1)
    prof = (prof - prof.min()) / (prof.max() - prof.min() + 1e-6)
    lo, hi = int(h * 0.38), int(h * 0.62)
    mid = prof[lo:hi]
    if mid.size < 3:
        return 1
    valley = float(mid.min())
    top_band = prof[int(h * 0.12): lo]
    bot_band = prof[hi: int(h * 0.88)]
    if top_band.size == 0 or bot_band.size == 0:
        return 1
    two_bands = top_band.max() > 0.6 and bot_band.max() > 0.6
    return 2 if (valley < 0.25 and two_bands) else 1


def split_rows(gray: np.ndarray, overlap: float = 0.06, cut_frac: float | None = None) -> list[np.ndarray]:
    """Split a two-row plate into (top, bottom) crops. The cut is the ink valley
    nearest the middle, and each row keeps a small overlap across the cut:
    on sandbox cam06 (GJ11EA2257) a hard cut at the valley clipped the bottom
    row's first glyph and the 'A' was lost; 6 % overlap recovers it.
    cut_frac forces the cut position (training jitter)."""
    h, w = gray.shape[:2]
    from anpr.enhance.binarize import polarity_normalise
    g = polarity_normalise(gray)
    ink = (255 - g).astype(np.float32).mean(1)
    lo, hi = int(h * 0.38), int(h * 0.62)
    if cut_frac is not None:
        cut = int(np.clip(cut_frac, 0.2, 0.8) * h)
    else:
        cut = lo + int(np.argmin(ink[lo:hi])) if hi > lo else h // 2
    ov = int(h * overlap)
    top, bot = gray[: min(h, cut + ov)], gray[max(0, cut - ov):]
    if top.shape[0] < 4 or bot.shape[0] < 4:
        cut = h // 2
        top, bot = gray[: cut + ov], gray[cut - ov:]
    return [top, bot]


def side_by_side(gray: np.ndarray, overlap: float = 0.06, cut_frac: float | None = None,
                 gap_frac: float = 0.25) -> np.ndarray:
    """Two-row plate -> ONE text line: the top row, a small plate-coloured gap, then the bottom
    row, both at the same height. A single-line CRNN then reads the whole registration in one
    pass (DL6SB / E6415 -> 'DL6SBE6415') and the plate grammar sees the full string, instead
    of each row being decoded on its own (read as 'DL658|FE6415' on the Delhi clip)."""
    top, bot = split_rows(gray, overlap, cut_frac)
    H = max(8, (top.shape[0] + bot.shape[0]) // 2)

    def fit(r: np.ndarray) -> np.ndarray:
        return cv2.resize(r, (max(1, int(round(r.shape[1] * H / max(r.shape[0], 1)))), H), interpolation=cv2.INTER_CUBIC)

    t, b = fit(top), fit(bot)
    fill = int(np.median(gray))
    gap = np.full((H, max(2, int(H * gap_frac))) + t.shape[2:], fill, dtype=gray.dtype)
    return np.hstack([t, gap, b])
