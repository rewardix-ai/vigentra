"""Render Indian number plates with known text.

The renderer behind tools/build_uniform_dataset.py: a plate is a string
drawn from the grammar in anpr/plate_rules.py (so every synthetic plate is
one the pipeline would accept), laid out the way Indian plates are - one row
or two, grouped, with the IND strip, border, rivets and embossing that HSRP
plates carry - in the colours the road actually shows: white/black private,
yellow/black commercial, green/white electric, black/yellow rental.

Nothing here degrades anything. A plate leaves this module sharp and at high
resolution; the composer warps it into a real vehicle crop and the degrade
module does the rest, so distance, blur and compression are applied to the
plate and its surroundings together, the way a camera would.

    python tools/synthesize_plates.py --sheet reports/synth_plates_sheet.jpg
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))

from anpr import plate_rules as pr  # noqa: E402

FONT_DIR = Path("C:/Windows/Fonts")
#: Bold sans faces close to the HSRP letterform, plus a few the cheaper
#: painted plates use. Missing files are skipped.
FONT_FILES = ("arialbd.ttf", "arial.ttf", "ARIALNB.TTF", "bahnschrift.ttf",
              "calibrib.ttf", "consolab.ttf", "ariblk.ttf", "verdanab.ttf",
              "tahomabd.ttf", "seguisb.ttf")

#: Weights over the grammar's formats. standard_2L dominates the road; the
#: rest are kept so the reader has seen them, not so it expects them.
FORMAT_WEIGHTS = {
    "standard_2L": 0.50, "standard_1L": 0.08, "standard_3L": 0.05,
    "bharat": 0.06, "delhi_3L": 0.06, "delhi_2L": 0.05, "standard_0L": 0.07,
    "diplomatic": 0.01, "short_tail": 0.04,
}
#: The deployment's own state is over-represented so the reader's prior on
#: the first two characters matches what these cameras see.
PREFERRED_STATE_SHARE = 0.40
#: Letters the RTO series avoids (they look like digits). Kept rare, not
#: absent: some states do issue them.
RARE_SERIES_LETTERS = "IO"

STYLES = (
    # name, background, ink, weight
    ("private", (245, 245, 245), (20, 20, 20), 0.66),
    ("commercial", (40, 200, 235), (25, 25, 25), 0.20),   # yellow (BGR)
    ("electric", (70, 130, 40), (245, 245, 245), 0.06),   # green
    ("rental", (25, 25, 25), (40, 210, 240), 0.04),       # black / yellow
    ("temporary", (60, 60, 200), (245, 245, 245), 0.02),  # red / white
    ("faded", (200, 205, 210), (70, 70, 75), 0.02),
)


@dataclass
class Plate:
    image: np.ndarray          # BGR, sharp, ~110 px per row
    text: str                  # characters only, reading order
    rows: list[str]            # per-row characters (1 or 2 entries)
    row_boxes: list[tuple[int, int, int, int]]   # xyxy of each row's text
    fmt: str
    style: str
    stacked: bool


_FONTS: list[Path] | None = None


def fonts() -> list[Path]:
    global _FONTS
    if _FONTS is None:
        _FONTS = [FONT_DIR / f for f in FONT_FILES if (FONT_DIR / f).exists()]
        if not _FONTS:
            raise SystemExit(f"no usable fonts under {FONT_DIR}")
    return _FONTS


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def _letters(rng: np.random.Generator, n: int) -> str:
    pool = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    out = []
    for _ in range(n):
        c = pool[int(rng.integers(len(pool)))]
        if c in RARE_SERIES_LETTERS and rng.random() < 0.85:
            c = pool[int(rng.integers(len(pool)))]
        out.append(c)
    return "".join(out)


def _digits(rng: np.random.Generator, n: int) -> str:
    return "".join(str(int(rng.integers(10))) for _ in range(n))


def _state(rng: np.random.Generator) -> str:
    if pr.PREFERRED_STATES and rng.random() < PREFERRED_STATE_SHARE:
        return sorted(pr.PREFERRED_STATES)[int(rng.integers(len(pr.PREFERRED_STATES)))]
    codes = sorted(pr.STATE_CODES)
    return codes[int(rng.integers(len(codes)))]


def make_text(rng: np.random.Generator, fmt: str | None = None) -> tuple[str, list[str]]:
    """One legal plate: (characters, display groups)."""
    if fmt is None:
        names = list(FORMAT_WEIGHTS)
        weights = np.array([FORMAT_WEIGHTS[n] for n in names]); weights /= weights.sum()
        fmt = names[int(rng.choice(len(names), p=weights))]
    if fmt == "standard_2L":
        g = [_state(rng), _digits(rng, 2), _letters(rng, 2), _digits(rng, 4)]
    elif fmt == "standard_1L":
        g = [_state(rng), _digits(rng, 2), _letters(rng, 1), _digits(rng, 4)]
    elif fmt == "standard_3L":
        g = [_state(rng), _digits(rng, 2), _letters(rng, 3), _digits(rng, 4)]
    elif fmt == "bharat":
        g = [_digits(rng, 2), "BH", _digits(rng, 4), _letters(rng, int(rng.integers(1, 3)))]
    elif fmt == "delhi_3L":
        g = ["DL", str(int(rng.integers(1, 10))) + _letters(rng, 1), _letters(rng, 2), _digits(rng, 4)]
    elif fmt == "delhi_2L":
        g = ["DL", str(int(rng.integers(1, 10))) + _letters(rng, 1), _letters(rng, 1), _digits(rng, 4)]
    elif fmt == "standard_0L":
        g = [_state(rng), _digits(rng, 2), _digits(rng, 4)]
    elif fmt == "diplomatic":
        g = [_digits(rng, 2), ["CD", "UN", "DC"][int(rng.integers(3))], _digits(rng, 4)]
    elif fmt == "short_tail":
        g = [_state(rng), _digits(rng, 2), _letters(rng, 2), _digits(rng, 3)]
    else:
        raise ValueError(fmt)
    text = "".join(g)
    # Zero-padded standard plates are the common case: "GJ01" not "GJ1".
    return text, g


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _pick_style(rng: np.random.Generator):
    w = np.array([s[3] for s in STYLES]); w /= w.sum()
    return STYLES[int(rng.choice(len(STYLES), p=w))]


def _draw_row(draw: ImageDraw.ImageDraw, x: int, y: int, groups: list[str],
              font: ImageFont.FreeTypeFont, ink, gap_group: int, gap_char: int,
              emboss: bool) -> tuple[int, int, int, int]:
    """Draw grouped text with fixed gaps; returns the ink bbox.

    The gaps are decided by the caller so the measuring pass and the drawing
    pass lay the row out identically - a row measured with one spacing and
    drawn with another runs off the plate.
    """
    x0, cx = x, x
    top, bottom = y, y
    shadow = tuple(int(c * 0.55) for c in ink) if emboss else None
    for gi, group in enumerate(groups):
        for ch in group:
            bbox = draw.textbbox((cx, y), ch, font=font)
            if shadow is not None:
                draw.text((cx + 2, y + 2), ch, font=font, fill=shadow)
            draw.text((cx, y), ch, font=font, fill=ink)
            cx = bbox[2] + gap_char
            top, bottom = min(top, bbox[1]), max(bottom, bbox[3])
        if gi < len(groups) - 1:
            cx += gap_group
    return x0, top, cx - gap_char, bottom


def render_plate(rng: np.random.Generator, fmt: str | None = None,
                 stacked: bool | None = None) -> Plate:
    """One sharp plate. Rows are ~110 px tall so the composer always shrinks."""
    text, groups = make_text(rng, fmt)
    fmt_name = pr.normalise(text).fmt or "free"
    style = _pick_style(rng)
    name, bg, ink, _ = style
    if stacked is None:
        stacked = rng.random() < 0.30
    if stacked:
        split = 2 if len(groups) >= 3 else 1
        rows = [groups[:split], groups[split:]]
    else:
        rows = [groups]

    font_path = fonts()[int(rng.integers(len(fonts())))]
    size = int(rng.uniform(78, 100))
    font = ImageFont.truetype(str(font_path), size)
    gap_group = int(size * rng.uniform(0.25, 0.6))
    gap_char = int(size * rng.uniform(0.0, 0.16))
    emboss = rng.random() < 0.6
    badge = name in ("private", "commercial", "electric") and rng.random() < 0.55
    border = rng.random() < 0.7

    # Measure first on a scratch canvas.
    scratch = Image.new("RGB", (2400, 400))
    sd = ImageDraw.Draw(scratch)
    widths, heights, tops = [], [], []
    for row in rows:
        bx = _draw_row(sd, 10, 10, row, font, (255, 255, 255), gap_group, gap_char, False)
        widths.append(bx[2] - bx[0]); heights.append(bx[3] - bx[1]); tops.append(bx[1] - 10)
    pad_x = int(size * rng.uniform(0.25, 0.6))
    pad_y = int(size * rng.uniform(0.15, 0.35))
    row_gap = int(size * rng.uniform(0.10, 0.25)) if stacked else 0
    badge_w = int(size * 0.55) if badge else 0
    W = max(widths) + 2 * pad_x + badge_w
    H = sum(heights) + 2 * pad_y + row_gap * (len(rows) - 1)

    bg_rgb = (bg[2], bg[1], bg[0]); ink_rgb = (ink[2], ink[1], ink[0])
    img = Image.new("RGB", (W, H), bg_rgb)
    d = ImageDraw.Draw(img)
    if badge:
        d.rectangle([0, 0, badge_w, H], fill=(20, 60, 160))
        try:
            small = ImageFont.truetype(str(font_path), max(10, int(size * 0.22)))
            d.text((int(badge_w * 0.12), int(H * 0.62)), "IND", font=small, fill=(255, 255, 255))
        except Exception:                               # noqa: BLE001
            pass
    if border:
        t = max(2, int(size * 0.04))
        d.rectangle([badge_w + t, t, W - t - 1, H - t - 1], outline=ink_rgb, width=t)

    row_boxes = []
    y = pad_y
    row_texts = []
    for row, hgt, wid, top in zip(rows, heights, widths, tops):
        x = badge_w + pad_x + (max(widths) - wid) // 2
        # The font's internal leading puts the ink below the draw origin;
        # draw so the ink's top lands on y.
        bx = _draw_row(d, x, y - top, row, font, ink_rgb, gap_group, gap_char, emboss)
        row_boxes.append((max(0, bx[0] - 4), max(0, bx[1] - 4), min(W, bx[2] + 4), min(H, bx[3] + 4)))
        row_texts.append("".join(row))
        y += hgt + row_gap

    # Rivets and grime, which is what most real plates carry.
    if rng.random() < 0.6:
        r = max(2, int(size * 0.05))
        for cx in (badge_w + pad_x // 2, W - pad_x // 2):
            d.ellipse([cx - r, H // 2 - r, cx + r, H // 2 + r], fill=(90, 90, 90))
    out = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    if rng.random() < 0.35:
        blot = np.zeros_like(out)
        for _ in range(int(rng.integers(1, 4))):
            cx, cy = int(rng.integers(W)), int(rng.integers(H))
            ax = int(rng.uniform(size * 0.2, size * 0.9)); ay = int(rng.uniform(size * 0.1, size * 0.5))
            cv2.ellipse(blot, (cx, cy), (ax, ay), float(rng.uniform(0, 180)), 0, 360, (60, 60, 60), -1)
        blot = cv2.GaussianBlur(blot, (0, 0), size * 0.15)
        out = np.clip(out.astype(np.int16) - (blot.astype(np.int16) * rng.uniform(0.3, 0.8)), 0, 255).astype(np.uint8)
    return Plate(out, text, row_texts, row_boxes, fmt_name, name, stacked)


# ---------------------------------------------------------------------------
# Sheet, for eyeballing
# ---------------------------------------------------------------------------

def sheet(n: int, seed: int, out: Path) -> None:
    rng = np.random.default_rng(seed)
    tiles = []
    for _ in range(n):
        p = render_plate(rng)
        img = cv2.resize(p.image, (360, int(360 * p.image.shape[0] / p.image.shape[1])))
        canvas = np.full((190, 360, 3), 255, np.uint8)
        canvas[:min(160, img.shape[0])] = img[:160]
        cv2.putText(canvas, f"{p.text} {p.fmt} {p.style}", (2, 182),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 180), 1)
        tiles.append(canvas)
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    grid = np.full((rows * 192, cols * 362, 3), 255, np.uint8)
    for i, t in enumerate(tiles):
        r, c = divmod(i, cols)
        grid[r * 192:r * 192 + 190, c * 362:c * 362 + 360] = t
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), grid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", default="reports/synth_plates_sheet.jpg")
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    sheet(args.count, args.seed, Path(args.sheet))
    print(f"wrote {args.sheet} with {args.count} plates from {len(fonts())} fonts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
