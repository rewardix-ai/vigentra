"""SAHI-style sliced inference (spec 6.2.2). Config-driven tile size/overlap;
merges tile detections back into parent coordinates with class-agnostic NMS."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Tile:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0


def make_tiles(w: int, h: int, tile: int = 512, overlap: float = 0.2) -> list[Tile]:
    if w <= tile and h <= tile:
        return [Tile(0, 0, w, h)]
    step = max(1, int(tile * (1.0 - overlap)))
    xs = list(range(0, max(w - tile, 0) + 1, step))
    ys = list(range(0, max(h - tile, 0) + 1, step))
    if xs[-1] + tile < w:
        xs.append(w - tile)
    if ys[-1] + tile < h:
        ys.append(h - tile)
    out = []
    for y in ys:
        for x in xs:
            out.append(Tile(x, y, min(x + tile, w), min(y + tile, h)))
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float = 0.5) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


def merge_tile_dets(dets: list[tuple[np.ndarray, np.ndarray, Tile]], iou_thr: float = 0.5):
    """dets: list of (boxes[N,4] in tile coords, scores[N], tile). Returns boxes, scores in parent coords."""
    all_b, all_s = [], []
    for b, s, t in dets:
        if len(b) == 0:
            continue
        b = b.copy()
        b[:, [0, 2]] += t.x0
        b[:, [1, 3]] += t.y0
        all_b.append(b)
        all_s.append(s)
    if not all_b:
        return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
    B = np.concatenate(all_b, 0)
    S = np.concatenate(all_s, 0)
    k = nms(B, S, iou_thr)
    return B[k], S[k]
