"""A confirmation needs glyphs in the pixels.

The failure this guards: delhi_1080p track s0_t10, a truck windscreen, CONFIRMED as DL11AB3684 at
0.45 with alternates DL11AB9684 / DL01AB3684 / DL11AB3634. All 12 of its best crops are glass.
"""
from __future__ import annotations

import cv2
import numpy as np

from anpr.detect.char_evidence import glyph_count, track_glyphs


def plate_like(text: str = "DL1CQ5334", w: int = 220, h: int = 60) -> np.ndarray:
    """A light plate with dark characters, inside the 12 % / 25 % margin the bank crops with."""
    mx, my = int(0.12 * w), int(0.25 * h)
    img = np.full((h + 2 * my, w + 2 * mx, 3), 40, np.uint8)      # vehicle body around the plate
    img[my:my + h, mx:mx + w] = 235
    cv2.putText(img, text, (mx + 8, my + h - 16), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3)
    return img


def glass_like(w: int = 220, h: int = 60) -> np.ndarray:
    """A bright, smooth, plate-shaped panel: a windscreen edge, no characters anywhere."""
    mx, my = int(0.12 * w), int(0.25 * h)
    img = np.zeros((h + 2 * my, w + 2 * mx, 3), np.uint8)
    ramp = np.linspace(120, 225, img.shape[0]).astype(np.uint8)
    img[:] = ramp[:, None, None]
    cv2.line(img, (0, 10), (img.shape[1], 30), (70, 70, 70), 2)   # a wiper / body seam
    return img


def test_a_plate_shows_several_glyphs():
    assert glyph_count(plate_like()) >= 4


def test_glass_shows_none():
    assert glyph_count(glass_like()) < 2


def test_a_track_is_scored_by_its_best_crop():
    class Crop:
        def __init__(self, image):
            self.image = image

    crops = [Crop(glass_like()), Crop(glass_like()), Crop(plate_like())]
    assert track_glyphs(crops) >= 4
    assert track_glyphs([Crop(glass_like())] * 3) < 2


def test_a_crop_too_small_to_hold_glyphs_scores_zero():
    assert glyph_count(np.full((6, 14, 3), 200, np.uint8)) == 0
    assert glyph_count(None) == 0
