"""One dataset for both models: real vehicle crops with a known plate in them.

The grid footage gives us real vehicles, real plate positions and real
degradation - and not one plate whose text is known, because every plate in
it is below what a person can read. Rendering text we do know onto the real
plate position, and then degrading the whole crop the way the camera would,
gives an image that carries both labels at once:

  * a box, for the detector, at the real plate's real place and size;
  * the characters, for the reader, cut from that same box.

The detector and the reader are therefore trained on the same images, the
same tiers and the same worst cases - a plate the detector learns to find at
9 px is the plate the reader learns to give up on, and the 30 px plate the
reader learns to decode is one the detector has been taught to box.

Hard first. Tier shares are 60% severe+extreme, and the stage lists put
those tiers before the rest so training meets the smears before the easy
plates (which need no teaching; the pretrained weights already find them).

Output (dataset/v4_uniform):
    images/train/*.jpg  labels/train/*.txt   detector, synthetic + real train
    images/val, images/test                  the REAL splits, untouched
    plates/{train,val}/*.jpg + plates.csv    reader crops with text
    stage_hard.{txt,yaml}  stage_all.{txt,yaml}   curriculum lists
    manifest.json                            provenance and counts

    python tools/build_uniform_dataset.py --dataset dataset/v2 --out dataset/v4_uniform --target 12000
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(HERE))

from anpr import degrade as dg                                   # noqa: E402
from synthesize_hard_cases import (TIERS, PLATE_PX, TIER_SHARE, NIGHT_FRACTION,  # noqa: E402
                                   night, jitter_window, load_sources, Source)
from synthesize_plates import render_plate                       # noqa: E402

#: Below this final plate width the characters are, by any honest measure,
#: not in the pixels. The flag is recorded per crop for the per-width
#: evaluation; it does NOT change the label. Every crop carries its plate's
#: text, at every size - the reader is trained on the worst cases with the
#: truth attached, and the evaluation per width band says where it can and
#: cannot deliver. The detector keeps every box: finding a 7 px plate is
#: still a finding.
READABLE_MIN_PX = 24
#: Super-resolution pairs (degraded crop at its information size -> the same
#: region from the sharp composite at SR_SCALE times that size), one framing
#: per composite, for tools/train_plate_sr.py.
SR_SCALE = 4
SR_MAX_LR_W = 96
#: The H.264 pass is a subprocess per image and dominates build time; a third
#: of the crops carry it, which is enough for the reader and detector to have
#: met macroblocking at every tier.
CODEC_SHARE = 0.33
#: Reader-only extras: plates degraded on their own, without a vehicle
#: behind them. The composites alone are too few for a text model (one
#: plate per crop), so the same renderer and the same tiers supply more
#: reads at the widths the reader is actually asked to decode.
#:
#: Widths per tier follow the plates the fixed pipeline actually boxed on
#: cameras 6 and 7 (p5 19 px, p25 39, p50 58, p75 75, p90 112) and stop at
#: 130 px: nothing wider is a case that needs teaching. The floor of 16 px
#: is below what can be read; those rows are labelled EMPTY like the
#: composites, so the reader learns silence for a smear here too.
BOOST_PX = {"extreme": (16, 30), "severe": (24, 45), "moderate": (35, 70), "mild": (60, 130)}
#: Reader crops cut per plate row from each composite, each framed a little
#: differently, the way successive detector boxes on one track are.
FRAMINGS_PER_PLATE = 3
#: Replay mode (--reader-only): the composites are regenerated from the same
#: seeds, the stored images and labels are left alone, and only the reader
#: crops, SR pairs and plates.csv are rewritten.
REPLAY = False
SR_SPLITS = ("train", "val_synth")
#: Working width the plate is composited at, before degradation. Sharp text
#: at 140-220 px is what a plate looks like before the camera happens to it.
COMPOSITE_W = (140, 220)


@dataclass
class Emitted:
    image: str
    tier: str
    plate_px: float
    night: bool
    glare: bool
    text: str
    stacked: bool
    fmt: str
    style: str
    source: str
    reader_crops: list[dict]


def _quad(box, rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """The plate corners: the box, expanded a little and pushed off-square."""
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    ex, ey = bw * rng.uniform(0.0, 0.06), bh * rng.uniform(0.0, 0.10)
    x1, y1, x2, y2 = x1 - ex, y1 - ey, x2 + ex, y2 + ey
    jx, jy = bw * 0.06, bh * 0.12
    pts = np.array([
        [x1 + rng.uniform(-jx, jx), y1 + rng.uniform(-jy, jy)],
        [x2 + rng.uniform(-jx, jx), y1 + rng.uniform(-jy, jy)],
        [x2 + rng.uniform(-jx, jx), y2 + rng.uniform(-jy, jy)],
        [x1 + rng.uniform(-jx, jx), y2 + rng.uniform(-jy, jy)],
    ], np.float32)
    pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
    return pts


def composite(crop: np.ndarray, box, plate: np.ndarray, rng: np.random.Generator):
    """Warp the plate onto the box; returns the new crop and the plate box."""
    h, w = crop.shape[:2]
    quad = _quad(box, rng, w, h)
    ph, pw = plate.shape[:2]
    src = np.array([[0, 0], [pw - 1, 0], [pw - 1, ph - 1], [0, ph - 1]], np.float32)
    M = cv2.getPerspectiveTransform(src, quad)
    warped = cv2.warpPerspective(plate, M, (w, h), flags=cv2.INTER_AREA,
                                 borderMode=cv2.BORDER_TRANSPARENT)
    mask = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), M, (w, h),
                               flags=cv2.INTER_LINEAR)
    mask = cv2.GaussianBlur(mask, (0, 0), 1.2).astype(np.float32) / 255.0
    # Match the plate's brightness to the bumper it sits on, most of the time;
    # a plate lit differently from its car is real too.
    if rng.random() < 0.7:
        region = crop[int(quad[:, 1].min()):int(quad[:, 1].max()) + 1,
                      int(quad[:, 0].min()):int(quad[:, 0].max()) + 1]
        if region.size:
            gain = float(np.clip(region.mean() / max(1.0, warped[mask > 0.5].mean()), 0.55, 1.35))
            warped = np.clip(warped.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    out = (crop.astype(np.float32) * (1 - mask[..., None])
           + warped.astype(np.float32) * mask[..., None]).astype(np.uint8)
    nb = (float(quad[:, 0].min()), float(quad[:, 1].min()),
          float(quad[:, 0].max()), float(quad[:, 1].max()))
    return out, nb, M


#: Headlight glare: how often it is applied to night crops, and to day crops
#: (sun on a reflective plate, or daytime running lights).
GLARE_NIGHT_SHARE = 0.55
GLARE_DAY_SHARE = 0.10


def headlight_glare(img: np.ndarray, box, rng: np.random.Generator) -> np.ndarray:
    """Bloom from the lamps either side of the plate, and the veil it casts.

    On this footage a plate at night is rarely dark: it sits between two
    headlights that saturate the sensor, and the camera's own bloom spills
    across it. Three effects, applied before the camera model so they are
    blurred and compressed along with everything else:

      * two soft, near-white discs at plate height, one each side, sized to
        the plate (a car's lamps are about a plate-width out from the plate);
      * a veil over the plate region: additive light that lifts the blacks
        and flattens contrast, which is what makes glare plates unreadable
        long before they are clipped;
      * sometimes, clipping: the plate's bright field pushed to 255 so the
        characters survive only as a faint negative.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    pw, ph = max(4.0, x2 - x1), max(2.0, y2 - y1)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    glow = np.zeros((h, w), np.float32)
    lamp_dx = pw * rng.uniform(0.9, 1.8)
    lamp_r = pw * rng.uniform(0.35, 0.9)
    lamp_dy = ph * rng.uniform(-1.5, 0.8)
    for side in (-1, 1):
        if rng.random() < 0.85:                       # one lamp can be out of shot / off
            lx, ly = cx + side * lamp_dx, cy + lamp_dy
            cv2.circle(glow, (int(lx), int(ly)), int(lamp_r), 1.0, -1)
    glow = cv2.GaussianBlur(glow, (0, 0), max(1.0, lamp_r * rng.uniform(0.6, 1.4)))
    bloom = float(rng.uniform(140, 255))
    warm = np.array(rng.choice([[0.85, 0.95, 1.0], [1.0, 1.0, 1.0], [0.8, 0.9, 1.0]]), np.float32)
    out = img.astype(np.float32) + glow[..., None] * bloom * warm[None, None, :]

    # Veil over the plate and its surroundings.
    veil = np.zeros((h, w), np.float32)
    ex, ey = int(pw * rng.uniform(0.9, 2.0)), int(ph * rng.uniform(1.5, 4.0))
    cv2.ellipse(veil, (int(cx), int(cy)), (max(2, ex), max(2, ey)), 0, 0, 360, 1.0, -1)
    veil = cv2.GaussianBlur(veil, (0, 0), max(1.0, pw * 0.5))
    lift = float(rng.uniform(25, 110))
    out = out + veil[..., None] * lift
    # Contrast loss under the veil: pull towards the local bright level.
    flat = float(rng.uniform(0.0, 0.45))
    out = out * (1 - veil[..., None] * flat) + veil[..., None] * flat * 235.0

    if rng.random() < 0.25:                           # clipping
        region = out[int(max(0, y1)):int(min(h, y2)) + 1, int(max(0, x1)):int(min(w, x2)) + 1]
        region += float(rng.uniform(40, 120))
    return np.clip(out, 0, 255).astype(np.uint8)


def _pick_tier(rng: np.random.Generator) -> str:
    names = list(TIER_SHARE)
    p = np.array([TIER_SHARE[n] for n in names]); p /= p.sum()
    return names[int(rng.choice(len(names), p=p))]


def _yolo_lines(boxes, w: int, h: int) -> list[str]:
    out = []
    for x1, y1, x2, y2 in boxes:
        cx, cy = (x1 + x2) / 2 / w, (y1 + y2) / 2 / h
        bw, bh = (x2 - x1) / w, (y2 - y1) / h
        if bw <= 0 or bh <= 0:
            continue
        out.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return out


def make_one(src: Source, index: int, seed: int, out_root: Path, split: str) -> Emitted | None:
    rng = np.random.default_rng(seed)
    crop = cv2.imread(str(src.image))
    if crop is None or not src.boxes:
        return None
    # Which real plate is replaced. Others keep their real (unreadable) content.
    target_i = int(rng.integers(len(src.boxes)))
    box = src.boxes[target_i]
    bw = max(2.0, box[2] - box[0])
    # Upscale the crop so the plate can be composited sharp, then let the
    # tier's distance factor take it back down. Capped so an 8 px real plate
    # does not become a 12x blow-up of nothing.
    k = float(np.clip(rng.uniform(*COMPOSITE_W) / bw, 1.0, 10.0))
    if k > 1.0:
        crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    boxes = [(b[0] * k, b[1] * k, b[2] * k, b[3] * k) for b in src.boxes]
    box = boxes[target_i]
    h, w = crop.shape[:2]

    tier = _pick_tier(rng)
    plate = render_plate(rng)
    crop, new_box, M = composite(crop, box, plate.image, rng)
    boxes[target_i] = new_box

    # Distance: land the composited plate at the tier's target width.
    target_px = float(rng.uniform(*PLATE_PX[tier]))
    cfg = TIERS[tier]
    scale = float(np.clip(target_px / max(1.0, new_box[2] - new_box[0]), 0.03, 0.95))
    tcfg = dg.DegradeConfig(**{**cfg.__dict__, "scale": (scale, scale),
                               "codec": bool(cfg.codec and rng.random() < CODEC_SHARE)})
    is_night = rng.random() < NIGHT_FRACTION
    if is_night:
        crop = night(crop, rng)
    glare = rng.random() < (GLARE_NIGHT_SHARE if is_night else GLARE_DAY_SHARE)
    clean = crop.copy()                     # sharp composite: the SR target
    if glare:
        crop = headlight_glare(crop, new_box, rng)
    degraded = dg.degrade(crop, tcfg, seed=int(rng.integers(2**31 - 1)))

    # Reframe. jitter_window returns (image, boxes) shifted by the window.
    win = jitter_window(degraded, boxes, rng)
    if win is None:
        return None
    img, wboxes = win
    ih, iw = img.shape[:2]
    if target_i >= len(wboxes):
        return None
    pb = wboxes[target_i]
    plate_px = float(pb[2] - pb[0]) * scale       # information actually left
    stem = f"{split}_{index:06d}"
    img_path = out_root / "images" / split / f"{stem}.jpg"
    # Window origin, so the clean composite can be cut on the same grid.
    ox = int(round(boxes[target_i][0] - pb[0]))
    oy = int(round(boxes[target_i][1] - pb[1]))
    if not REPLAY:
        cv2.imwrite(str(img_path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        lines = _yolo_lines(wboxes, iw, ih)
        (out_root / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
    elif not img_path.exists():
        return None

    # Reader crops from the SAME vehicle image: the plate box with the
    # detector's kind of looseness - several framings, because a detector
    # never boxes the same plate the same way twice - one set per row for
    # stacked plates.
    reader = []
    x1, y1, x2, y2 = pb
    pw_, ph_ = x2 - x1, y2 - y1
    readable = plate_px >= READABLE_MIN_PX
    rows = plate.rows if plate.stacked else [plate.text]
    for r_i, row_text in enumerate(rows):
      for f_i in range(FRAMINGS_PER_PLATE):
        if plate.stacked:
            ry1 = y1 + ph_ * (0.0 if r_i == 0 else 0.5)
            ry2 = y1 + ph_ * (0.5 if r_i == 0 else 1.0)
        else:
            ry1, ry2 = y1, y2
        jx, jy = pw_ * rng.uniform(-0.04, 0.14), (ry2 - ry1) * rng.uniform(-0.06, 0.25)
        sx, sy = pw_ * rng.uniform(-0.05, 0.05), (ry2 - ry1) * rng.uniform(-0.08, 0.08)
        cx1 = int(max(0, x1 - jx + sx)); cx2 = int(min(iw, x2 + jx + sx))
        cy1 = int(max(0, ry1 - jy + sy)); cy2 = int(min(ih, ry2 + jy + sy))
        if cx2 - cx1 < 4 or cy2 - cy1 < 3:
            continue
        patch = img[cy1:cy2, cx1:cx2]
        p_path = out_root / "plates" / split / f"{stem}_r{r_i}_f{f_i}.jpg"
        cv2.imwrite(str(p_path), patch, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if f_i == 0 and split in SR_SPLITS:
            # LR: the degraded crop at its information size. HR: the same
            # region of the sharp composite, SR_SCALE times larger.
            lr_w = int(np.clip(round((cx2 - cx1) * scale), 8, SR_MAX_LR_W))
            lr_h = max(4, int(round((cy2 - cy1) * lr_w / max(1, cx2 - cx1))))
            lr = cv2.resize(patch, (lr_w, lr_h), interpolation=cv2.INTER_AREA)
            hy1, hy2 = min(max(0, cy1 + oy), clean.shape[0]), min(max(0, cy2 + oy), clean.shape[0])
            hx1, hx2 = min(max(0, cx1 + ox), clean.shape[1]), min(max(0, cx2 + ox), clean.shape[1])
            if hy2 - hy1 >= 3 and hx2 - hx1 >= 4:
                hr = cv2.resize(clean[hy1:hy2, hx1:hx2], (lr_w * SR_SCALE, lr_h * SR_SCALE),
                                interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(out_root / "sr" / split / f"{stem}_r{r_i}_lr.png"), lr)
                cv2.imwrite(str(out_root / "sr" / split / f"{stem}_r{r_i}_hr.jpg"), hr,
                            [cv2.IMWRITE_JPEG_QUALITY, 95])
        reader.append({
            "plate_image": f"plates/{split}/{stem}_r{r_i}_f{f_i}.jpg",
            "text": row_text,
            "sr_pair": f"sr/{split}/{stem}_r{r_i}" if (f_i == 0 and split in SR_SPLITS) else "",
            "full_text": plate.text, "row": r_i, "stacked": int(plate.stacked),
            "readable": int(readable), "tier": tier, "plate_px": round(plate_px, 1),
            "crop_w": cx2 - cx1, "crop_h": cy2 - cy1, "night": int(is_night),
            "glare": int(glare),
            "fmt": plate.fmt, "style": plate.style, "split": split,
            "image": f"images/{split}/{stem}.jpg",
        })
    return Emitted(f"images/{split}/{stem}.jpg", tier, plate_px, is_night, glare, plate.text,
                   plate.stacked, plate.fmt, plate.style, str(src.image), reader)


def make_boost(index: int, seed: int, out_root: Path, split: str) -> list[dict]:
    """Plate-only reader samples: render, frame loosely, degrade, cut rows."""
    rng = np.random.default_rng(seed)
    plate = render_plate(rng)
    ph, pw = plate.image.shape[:2]
    # A bumper-coloured margin the detector's looseness would include: a
    # grey or a muted car colour, never a saturated one.
    mx, my = int(pw * rng.uniform(0.02, 0.14)), int(ph * rng.uniform(0.04, 0.25))
    base = int(rng.integers(15, 210))
    tint = rng.integers(-18, 19, size=3)
    bg = tuple(int(np.clip(base + t, 0, 255)) for t in tint)
    canvas = np.full((ph + 2 * my, pw + 2 * mx, 3), bg, np.uint8)
    canvas[my:my + ph, mx:mx + pw] = plate.image
    tier = _pick_tier(rng)
    target_px = float(rng.uniform(*BOOST_PX[tier]))
    readable = target_px >= READABLE_MIN_PX
    cfg = TIERS[tier]
    scale = float(np.clip(target_px / pw, 0.03, 0.95))
    tcfg = dg.DegradeConfig(**{**cfg.__dict__, "scale": (scale, scale),
                               "codec": bool(cfg.codec and rng.random() < CODEC_SHARE * 0.5)})
    if rng.random() < NIGHT_FRACTION:
        canvas = night(canvas, rng)
    img = dg.degrade(canvas, tcfg, seed=int(rng.integers(2**31 - 1)))
    # Store at the information's own size, not the render size.
    small_w = max(8, int(round(img.shape[1] * scale * 2)))
    img = cv2.resize(img, (small_w, max(6, int(round(img.shape[0] * scale * 2)))),
                     interpolation=cv2.INTER_AREA)
    ih, iw = img.shape[:2]
    rows = plate.rows if plate.stacked else [plate.text]
    out = []
    for r_i, row_text in enumerate(rows):
        if plate.stacked:
            y1, y2 = (0, ih // 2) if r_i == 0 else (ih // 2, ih)
        else:
            y1, y2 = 0, ih
        patch = img[y1:y2]
        if patch.shape[0] < 3 or patch.shape[1] < 4:
            continue
        stem = f"{split}_{index:06d}_r{r_i}"
        cv2.imwrite(str(out_root / "plates" / split / f"{stem}.jpg"), patch,
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        out.append({
            "plate_image": f"plates/{split}/{stem}.jpg", "text": row_text if readable else "",
            "full_text": plate.text, "row": r_i, "stacked": int(plate.stacked),
            "readable": int(readable), "tier": tier, "plate_px": round(target_px, 1),
            "crop_w": patch.shape[1], "crop_h": patch.shape[0], "night": 0, "glare": 0,
            "fmt": plate.fmt, "style": plate.style, "split": split, "image": "",
        })
    return out


def _boost_safely(args):
    try:
        return make_boost(*args)
    except Exception as exc:                             # noqa: BLE001
        return f"skip: {type(exc).__name__}: {exc}"


def _emit_safely(args):
    try:
        return make_one(*args)
    except Exception as exc:                             # noqa: BLE001
        return f"skip: {type(exc).__name__}: {exc}"


def build(dataset: Path, out_root: Path, target: int, val_target: int, seed: int,
          workers: int, boost: int = 0) -> dict:
    started = time.time()
    real = {"train": 0, "val": 0, "test": 0}
    if REPLAY:
        if not (out_root / "images" / "train").exists():
            raise SystemExit(f"{out_root} has no images to replay against")
        for split in real:
            real[split] = sum(1 for _ in (dataset / "images" / split).glob("*.jpg"))
        # plates/ is overwritten in place, never removed: a reader may be
        # training on it while the replay runs, and the replay writes the
        # same files with the same content plus the ones the earlier build
        # left out.
        for split in ("train", "val_synth"):
            shutil.rmtree(out_root / "sr" / split, ignore_errors=True)
    else:
        if out_root.exists():
            shutil.rmtree(out_root)
        for split in ("train", "val", "test"):
            (out_root / "images" / split).mkdir(parents=True)
            (out_root / "labels" / split).mkdir(parents=True)
        # Real images: train alongside the synthetic; val/test untouched.
        for split in ("train", "val", "test"):
            for img in sorted((dataset / "images" / split).glob("*.jpg")):
                shutil.copy(img, out_root / "images" / split / img.name)
                lbl = dataset / "labels" / split / (img.stem + ".txt")
                if lbl.exists():
                    shutil.copy(lbl, out_root / "labels" / split / lbl.name)
                real[split] += 1
    for split in ("train", "val_synth"):
        (out_root / "plates" / split).mkdir(parents=True, exist_ok=True)
        (out_root / "sr" / split).mkdir(parents=True, exist_ok=True)

    train_sources = [s for s in load_sources(dataset) if not s.negative and s.boxes]
    # Reader validation composites on the REAL VAL crops so its backgrounds
    # are never ones the reader trained on.
    val_sources = _sources_for_split(dataset, "val")
    rng = np.random.default_rng(seed)
    jobs = []
    for i in range(target):
        s = train_sources[int(rng.integers(len(train_sources)))]
        jobs.append((s, i, int(rng.integers(2**31 - 1)), out_root, "train"))
    for i in range(val_target):
        s = val_sources[int(rng.integers(len(val_sources)))]
        jobs.append((s, i, int(rng.integers(2**31 - 1)), out_root, "val_synth"))
    (out_root / "images" / "val_synth").mkdir(parents=True, exist_ok=True)
    (out_root / "labels" / "val_synth").mkdir(parents=True, exist_ok=True)

    emitted: list[Emitted] = []
    skipped = 0
    if workers > 1:
        from multiprocessing.pool import ThreadPool
        with ThreadPool(workers) as pool:
            for k, res in enumerate(pool.imap_unordered(_emit_safely, jobs, chunksize=8)):
                if isinstance(res, Emitted):
                    emitted.append(res)
                else:
                    skipped += 1
                if (k + 1) % 500 == 0:
                    print(f"  {k + 1}/{len(jobs)}  {time.time() - started:.0f}s", flush=True)
    else:
        for k, job in enumerate(jobs):
            res = _emit_safely(job)
            if isinstance(res, Emitted):
                emitted.append(res)
            else:
                skipped += 1
            if (k + 1) % 500 == 0:
                print(f"  {k + 1}/{len(jobs)}  {time.time() - started:.0f}s", flush=True)

    # Reader-only extras from the same renderer and tiers.
    (out_root / "plates" / "train_boost").mkdir(parents=True, exist_ok=True)
    (out_root / "plates" / "val_boost").mkdir(parents=True, exist_ok=True)
    boost_jobs = [(i, int(rng.integers(2**31 - 1)), out_root, "train_boost") for i in range(boost)]
    boost_jobs += [(i, int(rng.integers(2**31 - 1)), out_root, "val_boost")
                   for i in range(max(200, boost // 40) if boost else 0)]
    boost_rows: list[dict] = []
    if workers > 1:
        from multiprocessing.pool import ThreadPool
        with ThreadPool(workers) as pool:
            for k, res in enumerate(pool.imap_unordered(_boost_safely, boost_jobs, chunksize=32)):
                if isinstance(res, list):
                    boost_rows.extend(res)
                else:
                    skipped += 1
                if (k + 1) % 2000 == 0:
                    print(f"  boost {k + 1}/{len(boost_jobs)}  {time.time() - started:.0f}s", flush=True)
    else:
        for job in boost_jobs:
            res = _boost_safely(job)
            if isinstance(res, list):
                boost_rows.extend(res)
            else:
                skipped += 1

    # Reader table: every crop, every size, its text attached.
    rows = [r for e in emitted for r in e.reader_crops]
    for r in rows:
        r["source"] = "composite"
    for r in boost_rows:
        r["source"] = "plate_only"
        r.setdefault("sr_pair", "")
    rows.extend(boost_rows)
    with open(out_root / "plates.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader(); wr.writerows(rows)

    # Detector stage lists: hard tiers first, then everything (+ real train).
    train_e = [e for e in emitted if e.image.startswith("images/train/")]
    sr_pairs = sum(1 for r in rows if r.get("sr_pair"))
    real_train = [f"images/train/{p.name}" for p in sorted((dataset / "images" / "train").glob("*.jpg"))]
    hard = [e.image for e in train_e if e.tier in ("severe", "extreme")]
    everything = [e.image for e in train_e] + real_train
    for name, items in (() if REPLAY else (("stage_hard", hard + real_train), ("stage_all", everything))):
        (out_root / f"{name}.txt").write_text("\n".join(str(out_root / p) for p in items) + "\n")
        (out_root / f"{name}.yaml").write_text(
            f"# Vigentra uniform dataset - {name}\n"
            f"path: {out_root.as_posix()}\ntrain: {name}.txt\nval: images/val\ntest: images/test\n"
            f"nc: 1\nnames: ['plate']\n")
    if not REPLAY:
      (out_root / "data.yaml").write_text(
        f"# Vigentra uniform dataset (detector + reader)\n"
        f"path: {out_root.as_posix()}\ntrain: stage_all.txt\nval: images/val\ntest: images/test\n"
        f"nc: 1\nnames: ['plate']\n")

    tiers = {}
    for t in TIERS:
        sel = [e for e in train_e if e.tier == t]
        tiers[t] = {"images": len(sel),
                    "plate_px_median": round(float(np.median([e.plate_px for e in sel])), 1) if sel else None}
    manifest = {
        "version": "v4_uniform",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": str(dataset), "seed": seed,
        "real_images": real, "synthetic_train_images": len(train_e),
        "synthetic_val_images": len(emitted) - len(train_e),
        "reader_crops": len(rows), "reader_empty_labels": 0,
        "reader_crops_below_readable_px": sum(1 for r in rows if not r["readable"]),
        "sr_pairs": sr_pairs, "sr_scale": SR_SCALE, "replayed": REPLAY,
        "reader_composite_crops": sum(1 for r in rows if r["source"] == "composite"),
        "reader_plate_only_crops": sum(1 for r in rows if r["source"] == "plate_only"),
        "skipped": skipped, "tiers": tiers,
        "readable_min_px": READABLE_MIN_PX,
        "night_images": sum(1 for e in train_e if e.night),
        "glare_images": sum(1 for e in train_e if e.glare),
        "stages": ["stage_hard", "stage_all"],
        "labels": "plate text is synthetic and exact; boxes come from the real "
                  "VLM-adjudicated positions and are NOT human verified",
        "elapsed_s": round(time.time() - started),
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def _sources_for_split(dataset: Path, split: str) -> list[Source]:
    out = []
    for img in sorted((dataset / "images" / split).glob("*.jpg")):
        lbl = dataset / "labels" / split / (img.stem + ".txt")
        if not lbl.exists():
            continue
        im = cv2.imread(str(img))
        if im is None:
            continue
        h, w = im.shape[:2]
        boxes = []
        for line in lbl.read_text().splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            _, cx, cy, bw, bh = map(float, parts)
            boxes.append(((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h))
        if boxes:
            out.append(Source(img, boxes, False))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="dataset/v2")
    ap.add_argument("--out", default="dataset/v4_uniform")
    ap.add_argument("--target", type=int, default=12000)
    ap.add_argument("--val-target", type=int, default=800)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--reader-only", action="store_true",
                    help="Replay the same seeds against an existing build: leave "
                         "images/labels alone, rewrite plates/, sr/ and plates.csv.")
    ap.add_argument("--boost", type=int, default=0,
                    help="Reader-only plate renders (no vehicle) added to plates.csv. "
                         "Off by default: every image in the set is a vehicle.")
    args = ap.parse_args()
    global REPLAY
    REPLAY = bool(args.reader_only)
    m = build(Path(args.dataset), Path(args.out), args.target, args.val_target, args.seed,
              args.workers, args.boost)
    print("UNIFORM DATASET", m["version"])
    print(f"synthetic train {m['synthetic_train_images']}  synthetic val {m['synthetic_val_images']}  "
          f"reader crops {m['reader_crops']} (composite {m['reader_composite_crops']}, "
          f"plate-only {m['reader_plate_only_crops']}, below {READABLE_MIN_PX}px "
          f"{m['reader_crops_below_readable_px']})  sr pairs {m['sr_pairs']}  skipped {m['skipped']}")
    for t, info in m["tiers"].items():
        print(f"  {t:<9} {info['images']:>6}  plate px median {info['plate_px_median']}")
    print(f"elapsed {m['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
