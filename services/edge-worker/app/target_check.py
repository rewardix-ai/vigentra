"""Is this vehicle the pursued one? Score a track's plate crops against one known registration.

Reading a plate open-ended needs every glyph; checking it against ONE string only needs the reader to
find that string likely. For each of the track's best crops the CRNN's per-character probabilities give
the CTC likelihood of the target, measured against the reader's own best path (0 = the target is as
likely as anything it could read). Measured on grid clips (tools/target_search_eval.py, 8 Oct): at
>= -2.0 it found 10 of 17 readable plates with 0 false matches in 487 decoy checks, including near-twins.
"""
from __future__ import annotations

import cv2
import numpy as np

from anpr.plate_grammar import ALPHABET

BLANK = len(ALPHABET)
INDEX = {c: i for i, c in enumerate(ALPHABET)}
MATCH_SCORE = -2.0
TOP_CROPS = 3


def ctc_logp(probs: np.ndarray, target: str) -> float:
    """log P(target | probs) under CTC (forward algorithm, log space)."""
    lab = [INDEX[c] for c in target if c in INDEX]
    ext = [BLANK]
    for k in lab:
        ext += [k, BLANK]
    S = len(ext)
    lp = np.log(np.clip(probs, 1e-12, 1.0))
    alpha = np.full(S, -np.inf)
    alpha[0] = lp[0, ext[0]]
    if S > 1:
        alpha[1] = lp[0, ext[1]]
    for t in range(1, probs.shape[0]):
        prev = alpha.copy()
        for s in range(S):
            a = prev[s]
            if s >= 1:
                a = np.logaddexp(a, prev[s - 1])
            if s >= 2 and ext[s] != BLANK and ext[s] != ext[s - 2]:
                a = np.logaddexp(a, prev[s - 2])
            alpha[s] = a + lp[t, ext[s]]
    return float(np.logaddexp(alpha[-1], alpha[-2]) if S > 1 else alpha[-1])


def best_path(probs: np.ndarray) -> tuple[str, float]:
    idx = probs.argmax(1)
    logp = float(np.log(np.clip(probs[np.arange(len(idx)), idx], 1e-12, 1)).sum())
    out, prev = [], None
    for i in idx:
        if i != prev and i != BLANK:
            out.append(ALPHABET[i])
        prev = i
    return "".join(out), logp


def one_row(img: np.ndarray, two_row: bool) -> np.ndarray:
    """Greyscale, with a two-row plate laid out as one row (top then bottom)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if not two_row:
        return g
    h = g.shape[0] // 2
    top, bottom = g[:h], g[h:]
    bottom = cv2.resize(bottom, (int(bottom.shape[1] * top.shape[0] / max(1, bottom.shape[0])), max(1, top.shape[0])))
    return np.hstack([top, bottom])


def score_crops(crops, targets: list[str], reader) -> dict[str, tuple[float, str]]:
    """For each target: (score, what the reader read on the best crop). Crops are the engine's PlateCrop
    objects (image, two_row, quality); the best TOP_CROPS by quality are used."""
    best = sorted(crops, key=lambda c: -c.quality.quality_score)[:TOP_CROPS]
    if not best or not targets:
        return {}
    probs = [reader(one_row(c.image, c.two_row)) for c in best]
    read = best_path(probs[0])[0]
    out = {}
    for t in targets:
        rel = sorted((ctc_logp(p, t) - best_path(p)[1] for p in probs), reverse=True)
        out[t] = (float(np.mean(rel[:2])), read)
    return out
