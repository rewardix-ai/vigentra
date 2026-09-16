"""Does a crop actually show a row of glyphs? The evidence a confirmation should need.

A windscreen edge on a truck is plate-shaped (0.30 of the vehicle box, aspect 5.8) and sharp, so
the geometry prior, the quality score and the width knee all pass it. The reader then produces
DL11AB3684 at 0.45 with alternates DL11AB9684 / DL01AB3684 / DL11AB3634 - one-character variants of
an invention - and the string vote confirms it, because the crops did agree. They agreed on nothing.

What a registration has and a pane of glass does not is a row of glyphs, counted here:

  - the bank crops the plate box with a 12 % / 25 % margin, so cut back to the plate before
    thresholding; otherwise the vehicle body around it is one blob that every character touching
    the plate edge merges into, and the count comes out at 1 for real plates too;
  - normalise to 48 px tall, so a blob means the same thing on a 40 px and a 200 px plate;
  - CLAHE, then a LOCAL threshold - a global one separates plate from body, not glyph from plate;
  - count components of glyph proportions, in both polarities (dark on white, light on yellow).

Measured over the 182 tracks of the before_det baseline, every one graded by eye: taking a track's
best count over its 8 top crops, plate tracks score p10 3 / median 8 / p90 11 and junk tracks
p10 0 / median 1 / p90 4. Of that run's 16 confirmations the 15 correct ones score 6-12 and the
single hallucination scores 1.

The threshold was chosen on that data, so it is a floor against inventions, not a plate classifier:
at 2 it blocks 56 % of junk tracks and 2.5 % of plate tracks, and none of them were confirmations.

It is NOT a junk filter, and the measurement that says so is worth keeping. On an independent set -
the 323 boxes of data/det/grid from 7 cameras, all graded by eye - the junk scores a median of 10
glyphs against the plates' 7, because that junk is mostly advertising: the night_cam05 hoarding
"75750 83008" returns 10 glyphs, correctly, since it is a row of ten digits. Text painted on a
sign is exactly as glyph-rich as text stamped on a plate.

So the two guards cover different halves and neither covers both:
    width knee (anpr/detect/plate.py)  - a box that spans its vehicle: hoardings, light bars
    glyph floor (here)                 - a box with no glyphs at all: glass, body panels, blur
"""
from __future__ import annotations

import cv2
import numpy as np

# anpr/pipeline.py banks the plate box with mx = 0.12 w, my = 0.25 h of margin on each side, so the
# plate occupies the central 1/1.24 of the width and 1/1.5 of the height.
MARGIN_X, MARGIN_Y = (1 - 1 / 1.24) / 2, (1 - 1 / 1.5) / 2
NORM_H = 48


def glyph_count(bgr: np.ndarray) -> int:
    """How many glyph-shaped components the crop holds. 0 for glass, 6-12 for a legible plate."""
    if bgr is None or bgr.size == 0:
        return 0
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    h, w = g.shape[:2]
    if h < 12 or w < 24:
        return 0
    g = g[int(MARGIN_Y * h):int((1 - MARGIN_Y) * h), int(MARGIN_X * w):int((1 - MARGIN_X) * w)]
    if g.shape[0] < 6 or g.shape[1] < 12:
        return 0
    h, w = g.shape[:2]
    g = cv2.resize(g, (max(16, int(w * NORM_H / h)), NORM_H), interpolation=cv2.INTER_AREA)
    g = cv2.createCLAHE(2.0, (8, 8)).apply(g)
    H, W = g.shape
    block = max(9, (H // 3) | 1)
    best = 0
    for polarity in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
        bw = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, polarity, block, 8)
        # the plate's own border runs into the characters and merges a whole row into one blob:
        # a clear two-row GJ18X6705 counted 3 against a windscreen's 1. Eroding breaks the thin
        # joins without eating the strokes, and takes the same plate to 10.
        bw = cv2.erode(bw, np.ones((2, 2), np.uint8), iterations=1)
        _, _, stats, _ = cv2.connectedComponentsWithStats(bw, 8)
        n = 0
        for s in stats[1:]:
            bwid, bh = s[cv2.CC_STAT_WIDTH], s[cv2.CC_STAT_HEIGHT]
            if s[cv2.CC_STAT_TOP] <= 0 and bh >= 0.95 * H:
                continue                       # a full-height band is the plate frame, not a glyph
            # 0.22, not 0.30: on a two-row plate each character is only about a quarter of the
            # crop's height, and a 0.30 floor excluded every one of them.
            if (0.22 * H <= bh <= 0.95 * H and 0.03 * W <= bwid <= 0.25 * W
                    and 0.8 <= bh / max(bwid, 1) <= 6.0
                    and s[cv2.CC_STAT_AREA] >= 0.12 * bwid * bh):
                n += 1
        best = max(best, n)
    return best


def track_glyphs(crops, k: int = 8) -> int:
    """The best glyph count among a track's k best crops. One legible crop is enough."""
    return max((glyph_count(c.image) for c in crops[:k]), default=0)
