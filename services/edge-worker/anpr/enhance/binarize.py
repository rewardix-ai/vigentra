"""Plate polarity normalisation, used by the two-row splitter (anpr/read/rows.py)."""
from __future__ import annotations

import numpy as np


def polarity_normalise(gray: np.ndarray) -> np.ndarray:
    """Return dark-text-on-light-ground. Plates in India can be either
    polarity (white/yellow ground vs black/green ground)."""
    h, w = gray.shape[:2]
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    centre = gray[h // 4: 3 * h // 4, w // 8: 7 * w // 8]
    if border.mean() < centre.mean():
        return 255 - gray
    return gray
