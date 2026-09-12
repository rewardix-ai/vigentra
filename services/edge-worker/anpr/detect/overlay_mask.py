"""Overlay / UI mask (spec 6.2.1). Runs before any detector.

Two mechanisms, OR-ed together:
  1. Manual per-camera rectangles from config/roi.yaml (exclude boxes + an
     optional include ROI).
  2. Automatic static-text detection: over the first N frames compute a
     temporal variance map; regions with near-zero variance AND high
     text-likeness (dense edges + high-contrast strokes) are masked.

The mask is a uint8 HxW image: 255 = usable pixel, 0 = masked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np
import yaml


@dataclass
class ROIConfig:
    exclude: list[tuple[int, int, int, int]] = field(default_factory=list)   # x1,y1,x2,y2
    include: Optional[tuple[int, int, int, int]] = None                      # x1,y1,x2,y2
    lanes: list[tuple[int, int, int, int]] = field(default_factory=list)     # digital-zoom lane ROIs
    auto_static_text: bool = True
    auto_frames: int = 40
    var_thresh: float = 0.35         # temporal std below this = rendered (not encoded) pixel
    edge_density_thresh: float = 0.08
    dilate_px: int = 6
    max_auto_frac: float = 0.12      # drop the auto mask if it exceeds this share of the frame
    bright_min: int = 150            # persistent-white text rule: min-over-frames >= this; 0 disables
    osd_band_frac: float = 0.0       # optional FIXED top/bottom strips (fraction of height); 0 = strips follow detected OSD text
    bright_min_soft: int = 115       # ... or min >= this AND (max - min) <= bright_range_max (thin semi-transparent OSD on 480p feeds)
    bright_range_max: int = 90

    @staticmethod
    def load(path: str | Path, camera_id: str) -> "ROIConfig":
        path = Path(path)
        if not path.exists():
            return ROIConfig()
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        cams = cfg.get("cameras", {})
        c = cams.get(camera_id) or cams.get("default") or {}
        auto = cfg.get("auto", {})
        return ROIConfig(
            exclude=[tuple(b) for b in c.get("exclude", [])],
            include=tuple(c["include"]) if c.get("include") else None,
            lanes=[tuple(b) for b in c.get("lanes", [])],
            auto_static_text=bool(auto.get("enabled", True)),
            auto_frames=int(auto.get("frames", 40)),
            var_thresh=float(auto.get("var_thresh", 0.35)),
            edge_density_thresh=float(auto.get("edge_density_thresh", 0.08)),
            dilate_px=int(auto.get("dilate_px", 6)),
            max_auto_frac=float(auto.get("max_auto_frac", 0.12)),
            bright_min=int(auto.get("bright_min", 150)),
            osd_band_frac=float(auto.get("osd_band_frac", 0.0)),
            bright_min_soft=int(auto.get("bright_min_soft", 115)),
            bright_range_max=int(auto.get("bright_range_max", 90)),
        )


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class OverlayMasker:
    def __init__(self, cfg: ROIConfig, shape_hw: tuple[int, int]):
        self.cfg = cfg
        self.h, self.w = shape_hw
        self._acc: list[np.ndarray] = []
        self._auto_mask: Optional[np.ndarray] = None
        self.auto_mask_frac: float = 0.0
        self.auto_dropped: bool = False
        self.static_boxes: list[tuple] = []
        self.osd_strips: list[tuple[int, int]] = []   # (y1, y2) full-width strips hard-masked around detected OSD text
        self._manual = self._build_manual()
        self._mask = self._manual.copy()

    # ------------------------------------------------------------------
    def _build_manual(self) -> np.ndarray:
        m = np.full((self.h, self.w), 255, np.uint8)
        if self.cfg.include is not None:
            x1, y1, x2, y2 = self.cfg.include
            inc = np.zeros_like(m)
            inc[y1:y2, x1:x2] = 255
            m = inc
        for x1, y1, x2, y2 in self.cfg.exclude:
            m[max(0, y1):min(self.h, y2), max(0, x1):min(self.w, x2)] = 0
        # Optional fixed strips (config): use when a camera's OSD is known to sit in the edge
        # strips and must be excluded even before the warm-up has seen any frames. The default
        # (0) derives the full-width strips from the OSD text actually found (see _finalise).
        if self.cfg.osd_band_frac > 0:
            band = int(round(self.cfg.osd_band_frac * self.h))
            m[:band, :] = 0
            m[self.h - band:, :] = 0
        return m

    def observe(self, frame_bgr: np.ndarray) -> None:
        """Feed warm-up frames. Finalises the auto mask after cfg.auto_frames."""
        if not self.cfg.auto_static_text or self._auto_mask is not None:
            return
        # sample every 3rd frame so the warm-up window spans ~5 s at 25 fps instead of 1.6 s:
        # a headlight or a plate must stay bright for the whole window to count as OSD
        self._seen = getattr(self, "_seen", 0) + 1
        if (self._seen - 1) % 3:
            return
        g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        self._acc.append(g.astype(np.float32))
        if len(self._acc) >= self.cfg.auto_frames:
            self._finalise()

    def _finalise(self) -> None:
        stack = np.stack(self._acc, 0)
        std = stack.std(0)
        mean = stack.mean(0)
        # Browser/VMS chrome is rendered, not encoded: its temporal std is
        # essentially exactly zero, whereas even a static video background
        # carries codec flicker (std ~0.3-1.0). Measured on the sample clip:
        # header p90 std = 0.22, road p10 = 0.16 / p50 = 0.65. So the auto
        # detector only claims blobs that are almost entirely exact-static AND
        # text-like; composited captions (which inherit video flicker) must be
        # declared in roi.yaml.
        static = (std < self.cfg.var_thresh).astype(np.uint8)
        edges = cv2.Canny(mean.astype(np.uint8), 80, 160)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 9))
        density = cv2.blur((edges > 0).astype(np.float32), (31, 15))
        texty = (density > self.cfg.edge_density_thresh).astype(np.uint8)
        cand = cv2.morphologyEx(static & texty, cv2.MORPH_CLOSE, k)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
        auto = np.zeros((self.h, self.w), np.uint8)
        for i in range(1, n):
            x, y, w, h, a = stats[i]
            if w < 40 or h < 8 or a < 200:
                continue
            blob_std = std[y:y + h, x:x + w]
            if float((blob_std < self.cfg.var_thresh).mean()) < 0.9:
                continue
            auto[y:y + h, x:x + w] = 255
        # Rule 2 - persistent white text. The exact-static rule above only fires on rendered
        # (screen-recorded) chrome; on the real Sentinel feeds the burned-in timestamp and
        # camera caption are re-encoded with the video and flicker with a temporal std of
        # 3-10 grey levels (measured 2026-09-10 on cam02/11/15/16 HLS: bright text p50 std
        # 3.6-9.6 vs 0.35 threshold), so "13-06-2026", "RLVD" and a lone "4" reached the
        # plate reader on cam15/cam16. Camera OSD text is white in EVERY frame regardless of
        # what passes behind it; headlights and plates are not. Mask connected components
        # whose per-pixel minimum over the warm-up window stays bright, that are text-line
        # shaped (height <= 8 % of frame) and edge-dense. Restricted to the OSD bands below.
        if self.cfg.bright_min > 0:
            mn = stack.min(0)
            rng = stack.max(0) - mn
            # 480p feeds render the OSD thin and semi-transparent: glyph min over the window is
            # ~128 (cam06) instead of ~200, so also accept "bright-ish and stable" pixels
            pers = ((mn >= self.cfg.bright_min) | ((mn >= self.cfg.bright_min_soft) & (rng <= self.cfg.bright_range_max))).astype(np.uint8)
            mid = self._acc[len(self._acc) // 2].astype(np.uint8)
            edge_mid = cv2.Canny(mid, 80, 160) > 0
            n2, lab2, st2, _ = cv2.connectedComponentsWithStats(pers, 8)
            text = np.zeros_like(auto)
            heights = []
            glyph_boxes = []
            for i in range(1, n2):
                x, y, w, h, a = st2[i]
                if h > 0.08 * self.h or w < 6 or a < 20:
                    continue
                # glyph strokes fill 20-60 % of their box; a white plate background that sat
                # still through the warm-up (queue at a signal) is a solid blob (fill > 0.7)
                if a / float(max(w * h, 1)) > 0.65:
                    continue
                if float(edge_mid[y:y + h, x:x + w].mean()) < 0.08:
                    continue
                text[lab2 == i] = 255
                heights.append(int(h))
                glyph_boxes.append((int(y), int(y + h), int(x), int(x + w)))
            if heights:
                # the seconds digits change every frame and are never "persistent"; they sit
                # right after the static part of the timestamp, so grow each text line
                # sideways by ~2.5 glyph heights to cover them (OSD bands only, see below)
                th = int(np.median(heights))
                text = cv2.dilate(text, np.ones((3, 2 * int(2.5 * th) + 1), np.uint8))
                auto[text > 0] = 255
                # Whole-strip rule (every camera): wherever OSD text is found in the top or
                # bottom band, hard-mask the FULL-WIDTH strip spanned by that text (+ margin),
                # so nothing in the timestamp / caption line can ever be searched for plates.
                # Only the edge that actually carries text is masked: on cam06 the OSD is at
                # the top and a blanket bottom strip cost the best crops of exiting vehicles.
                # A caption / timestamp is a LONG, LOW run after the sideways dilation (w >= 5 h,
                # w >= 12 % of the frame, h <= 6 % of the frame); a static lamp or a lit sign in the
                # band is not, so it never widens into a strip (cam15/cam28 lost 20 % of the frame
                # to lamp-derived strips before this shape test).
                band0 = int(0.18 * self.h)
                # per-glyph pieces (NOT the dilated blob: on cam06 the sideways dilation merged the
                # caption with a bright parked auto below it into a 77-px blob that failed the height cap)
                pieces = [(y1, y2, x1, x2, 1) for (y1, y2, x1, x2) in glyph_boxes
                          if ((y1 + y2) / 2 < band0 or (y1 + y2) / 2 > self.h - band0)]
                # A caption is one ROW of many glyphs. The row is often broken into pieces where
                # the background under the text is bright (cam06 caption over a sunlit road), so
                # pieces on the same row are pooled before the row test: >= 6 glyphs in total,
                # spanning >= 12 % of the frame width. A lit ceiling edge or a lamp reflection
                # never has six glyphs (cam28 corridor grew three false strips before this).
                pieces.sort()
                used = [False] * len(pieces)
                for i, (y1, y2, x1, x2, g) in enumerate(pieces):
                    if used[i]:
                        continue
                    ry1, ry2, rx1, rx2, rg = y1, y2, x1, x2, g
                    used[i] = True
                    for j, (b1, b2, c1, c2, gg) in enumerate(pieces):
                        if used[j] or abs((b1 + b2) / 2 - (y1 + y2) / 2) > th:
                            continue
                        used[j] = True
                        ry1, ry2, rx1, rx2, rg = min(ry1, b1), max(ry2, b2), min(rx1, c1), max(rx2, c2), rg + gg
                    if rg >= 6 and (rx2 - rx1) >= 0.12 * self.w and (ry2 - ry1) <= 0.08 * self.h:
                        self.osd_strips.append((int(max(0, ry1 - 4)), int(min(self.h, ry2 + 4))))

        if self.cfg.dilate_px > 0:
            auto = cv2.dilate(auto, np.ones((self.cfg.dilate_px * 2 + 1,) * 2, np.uint8))
        # HEVC/H.264 skip-blocks make static scene content pixel-identical too (a parked
        # car's plate on sandbox cam06 was masked as "rendered text"). Burned-in OSD lives
        # in the top/bottom bands; never auto-mask the road in between.
        band = int(0.18 * self.h)
        auto[band:self.h - band, :] = 0
        # over-masking guard: an auto mask that eats a large share of the frame
        # is a scene-statistics failure, not an overlay; drop it and rely on roi.yaml
        frac = float((auto > 0).mean())
        self.auto_mask_frac = frac
        if frac > self.cfg.max_auto_frac:
            auto[:] = 0
            self.auto_dropped = True
        # the OSD strips are a hard rule, applied after the guard so they can never be dropped
        for y1, y2 in self.osd_strips:
            auto[y1:y2, :] = 255
        self._auto_mask = auto
        self._mask = self._manual.copy()
        self._mask[auto > 0] = 0
        self._acc.clear()

    def add_static_boxes(self, boxes_per_frame: list[list[tuple[float, float, float, float]]],
                         min_frac: float = 0.6, iou_thr: float = 0.8, pad: int = 6) -> int:
        """Overlay text is static in frame coordinates while vehicles move: any
        plate-like detection that recurs at the same position (IoU >= iou_thr)
        in >= min_frac of the warm-up frames is burned-in OSD (timestamp, camera
        caption) and is masked. Returns the number of boxes added."""
        n = len(boxes_per_frame)
        if n < 3:
            return 0
        cands: list[list] = []   # [box, count]
        for frame_boxes in boxes_per_frame:
            for b in frame_boxes:
                for c in cands:
                    if _iou(c[0], b) >= iou_thr:
                        c[1] += 1
                        break
                else:
                    cands.append([tuple(b), 1])
        added = 0
        for box, count in cands:
            # a parked car's plate is static too (cam06 in the sandbox: GJ32AG0028 sat still for
            # the whole clip and was masked): only the OSD bands qualify - top 18 % / bottom 18 %
            cy = (box[1] + box[3]) / 2
            in_osd_band = cy < 0.18 * self.h or cy > 0.82 * self.h
            if count / n >= min_frac and in_osd_band:
                x1, y1, x2, y2 = [int(v) for v in box]
                self._mask[max(0, y1 - pad):min(self.h, y2 + pad), max(0, x1 - pad):min(self.w, x2 + pad)] = 0
                self.static_boxes.append(box)
                added += 1
        return added

    @property
    def ready(self) -> bool:
        return (not self.cfg.auto_static_text) or self._auto_mask is not None

    @property
    def mask(self) -> np.ndarray:
        return self._mask

    @property
    def auto_mask(self) -> Optional[np.ndarray]:
        return self._auto_mask

    def apply(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Return a copy of the frame with masked pixels blacked out."""
        out = frame_bgr.copy()
        out[self._mask == 0] = 0
        return out

    def box_allowed(self, x1: float, y1: float, x2: float, y2: float, max_masked_frac: float = 0.15) -> bool:
        """A detection is rejected if more than max_masked_frac of it is masked."""
        xi1, yi1 = max(0, int(x1)), max(0, int(y1))
        xi2, yi2 = min(self.w, int(np.ceil(x2))), min(self.h, int(np.ceil(y2)))
        if xi2 <= xi1 or yi2 <= yi1:
            return False
        sub = self._mask[yi1:yi2, xi1:xi2]
        return float((sub == 0).mean()) <= max_masked_frac

    def bounding_rects(self) -> list[tuple[int, int, int, int]]:
        inv = (self._mask == 0).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
        return [(int(s[0]), int(s[1]), int(s[0] + s[2]), int(s[1] + s[3])) for s in stats[1:]]


def warm_up(masker: OverlayMasker, frames: Iterable[np.ndarray]) -> OverlayMasker:
    for f in frames:
        masker.observe(f)
        if masker.ready:
            break
    if not masker.ready and masker._acc:
        masker._finalise()
    return masker
