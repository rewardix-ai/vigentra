"""Cut the dashboard's logo files out of the approved master artwork.

    python scripts/brand_assets.py docs/brand/vigentra-logo-master.png

Needs numpy and opencv (the edge-worker extras). Writes, under
services/dashboard/:

  public/brand/vigentra-logo.png        full lockup, transparent, for light surfaces
  public/brand/vigentra-mark.png        the mark alone, transparent, own colours
  public/brand/vigentra-mark-light.png  the mark reversed to white, for the navy chrome
  public/brand/vigentra-wordmark.png    the wordmark alone, own colours, for light surfaces
  public/brand/vigentra-wordmark-light.png  the wordmark reversed to white, for the navy chrome
  app/icon.png                          browser tab icon: the mark on a white tile

The master is dark artwork on a near-white ground, laid out as three bands -
mark, wordmark, tagline - separated by empty rows. The bands are found rather
than hard-coded, so a re-exported master with different margins still cuts
right.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "services" / "dashboard"
PAD = 6
INK_THRESHOLD = 24


def _bands(ink: np.ndarray) -> list[tuple[int, int]]:
    rows = ink.any(axis=1)
    bands, start = [], None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(rows) - 1))
    return bands


def _crop(image: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
    h, w = image.shape[:2]
    return image[max(0, y0 - PAD):min(h, y1 + PAD + 1), max(0, x0 - PAD):min(w, x1 + PAD + 1)]


def colour_to_alpha(bgr: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Remove the ground, keeping true colours and anti-aliased edges (BGRA)."""
    c = bgr.astype(np.float64) / 255.0
    b = bg / 255.0
    alpha = np.clip(((b - c) / np.maximum(b, 1e-6)).max(axis=2), 0, 1)
    alpha[alpha < 0.03] = 0
    safe = np.where(alpha > 0, alpha, 1)[..., None]
    colour = np.clip((c - (1 - alpha[..., None]) * b) / safe, 0, 1)
    return np.dstack([colour * 255, alpha * 255]).round().astype(np.uint8)


def reversed_white(bgr: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """White artwork whose opacity follows the original's darkness: the navy
    parts go solid, the grey arc translucent, the eye's highlight clear."""
    lum = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    bg_l = float(np.mean(bg))
    dark_l = float(np.percentile(lum, 1))
    alpha = np.clip((bg_l - lum) / max(bg_l - dark_l, 1.0), 0, 1)
    alpha[alpha < 0.03] = 0
    out = np.full((*lum.shape, 4), 255, np.uint8)
    out[..., 3] = (alpha * 255).round().astype(np.uint8)
    return out


def tab_icon(mark_bgra: np.ndarray, size: int = 256, inset: int = 30, radius: int = 52) -> np.ndarray:
    """The mark centred on a white rounded tile, legible on dark tab bars."""
    tile = np.zeros((size, size, 4), np.uint8)
    mask = np.zeros((size, size), np.uint8)
    cv2.rectangle(mask, (radius, 0), (size - radius - 1, size - 1), 255, -1)
    cv2.rectangle(mask, (0, radius), (size - 1, size - radius - 1), 255, -1)
    for cx, cy in ((radius, radius), (size - radius - 1, radius), (radius, size - radius - 1), (size - radius - 1, size - radius - 1)):
        cv2.circle(mask, (cx, cy), radius, 255, -1, lineType=cv2.LINE_AA)
    tile[..., :3] = 255
    tile[..., 3] = mask
    mh, mw = mark_bgra.shape[:2]
    scale = min((size - 2 * inset) / mw, (size - 2 * inset) / mh)
    small = cv2.resize(mark_bgra, (round(mw * scale), round(mh * scale)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    y0, x0 = (size - sh) // 2, (size - sw) // 2
    a = small[..., 3:4].astype(np.float64) / 255.0
    region = tile[y0:y0 + sh, x0:x0 + sw, :3].astype(np.float64)
    tile[y0:y0 + sh, x0:x0 + sw, :3] = (small[..., :3] * a + region * (1 - a)).round().astype(np.uint8)
    return tile


def main(master: Path) -> int:
    image = cv2.imread(str(master), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read {master}")
    h, w = image.shape[:2]
    corners = np.array([image[5, 5], image[5, w - 6], image[h - 6, 5], image[h - 6, w - 6]], float)
    bg = np.median(corners, axis=0)
    ink = np.abs(image.astype(int) - bg).max(axis=2) > INK_THRESHOLD
    bands = _bands(ink)
    if len(bands) < 2:
        raise SystemExit(f"expected mark, wordmark and tagline bands; found {len(bands)}")

    ys, xs = np.where(ink)
    logo = _crop(image, ys.min(), ys.max(), xs.min(), xs.max())
    top, bottom = bands[0]
    cols = np.where(ink[top:bottom + 1].any(axis=0))[0]
    mark = _crop(image, top, bottom, cols.min(), cols.max())
    w_top, w_bottom = bands[1]
    w_cols = np.where(ink[w_top:w_bottom + 1].any(axis=0))[0]
    wordmark = _crop(image, w_top, w_bottom, w_cols.min(), w_cols.max())

    brand = OUT / "public" / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    mark_bgra = colour_to_alpha(mark, bg)
    outputs = {
        brand / "vigentra-logo.png": colour_to_alpha(logo, bg),
        brand / "vigentra-mark.png": mark_bgra,
        brand / "vigentra-mark-light.png": reversed_white(mark, bg),
        brand / "vigentra-wordmark.png": colour_to_alpha(wordmark, bg),
        brand / "vigentra-wordmark-light.png": reversed_white(wordmark, bg),
        OUT / "app" / "icon.png": tab_icon(mark_bgra),
    }
    for path, data in outputs.items():
        cv2.imwrite(str(path), data)
        print(f"{path.relative_to(ROOT)}  {data.shape[1]}x{data.shape[0]}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(Path(sys.argv[1])))
