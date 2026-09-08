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

#: Below this final plate width the characters are not in the pixels. The
#: reader still sees these crops - labelled EMPTY - so it learns to return
#: nothing for a smear instead of inventing a plate. The detector keeps the
#: box regardless: finding a 7 px plate is still a finding.
READABLE_MIN_PX = 24
#: Share of reader samples kept from below the floor.
EMPTY_SHARE_CAP = 0.06
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
#: Working width the plate is composited at, before degradation. Sharp text
#: at 140-220 px is what a plate looks like before the camera happens to it.
COMPOSITE_W = (140, 220)


@dataclass
class Emitted:
    image: str
    tier: str
    plate_px: float
    night: bool
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
    cv2.imwrite(str(img_path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    lines = _yolo_lines(wboxes, iw, ih)
    (out_root / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))

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
        reader.append({
            "plate_image": f"plates/{split}/{stem}_r{r_i}_f{f_i}.jpg",
            "text": row_text if readable else "",
            "full_text": plate.text, "row": r_i, "stacked": int(plate.stacked),
            "readable": int(readable), "tier": tier, "plate_px": round(plate_px, 1),
            "crop_w": cx2 - cx1, "crop_h": cy2 - cy1, "night": int(is_night),
            "fmt": plate.fmt, "style": plate.style, "split": split,
            "image": f"images/{split}/{stem}.jpg",
        })
    return Emitted(f"images/{split}/{stem}.jpg", tier, plate_px, is_night, plate.text,
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
            "crop_w": patch.shape[1], "crop_h": patch.shape[0], "night": 0,
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
    if out_root.exists():
        shutil.rmtree(out_root)
    for split in ("train", "val", "test"):
        (out_root / "images" / split).mkdir(parents=True)
        (out_root / "labels" / split).mkdir(parents=True)
    for split in ("train", "val"):
        (out_root / "plates" / split).mkdir(parents=True)

    # Real images: train alongside the synthetic; val/test untouched.
    real = {"train": 0, "val": 0, "test": 0}
    for split in ("train", "val", "test"):
        for img in sorted((dataset / "images" / split).glob("*.jpg")):
            shutil.copy(img, out_root / "images" / split / img.name)
            lbl = dataset / "labels" / split / (img.stem + ".txt")
            if lbl.exists():
                shutil.copy(lbl, out_root / "labels" / split / lbl.name)
            real[split] += 1

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
    (out_root / "plates" / "val_synth").mkdir(parents=True, exist_ok=True)

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

    # Reader table, with the empty-label share capped over both sources.
    rows = [r for e in emitted for r in e.reader_crops]
    for r in rows:
        r["source"] = "composite"
    for r in boost_rows:
        r["source"] = "plate_only"
    rows.extend(boost_rows)
    empties = [r for r in rows if not r["readable"]]
    keep_empty = int(EMPTY_SHARE_CAP * len(rows))
    rng.shuffle(empties)
    drop = {id(r) for r in empties[keep_empty:]}
    rows = [r for r in rows if id(r) not in drop]
    with open(out_root / "plates.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader(); wr.writerows(rows)

    # Detector stage lists: hard tiers first, then everything (+ real train).
    train_e = [e for e in emitted if e.image.startswith("images/train/")]
    real_train = [f"images/train/{p.name}" for p in sorted((dataset / "images" / "train").glob("*.jpg"))]
    hard = [e.image for e in train_e if e.tier in ("severe", "extreme")]
    everything = [e.image for e in train_e] + real_train
    for name, items in (("stage_hard", hard + real_train), ("stage_all", everything)):
        (out_root / f"{name}.txt").write_text("\n".join(str(out_root / p) for p in items) + "\n")
        (out_root / f"{name}.yaml").write_text(
            f"# Vigentra uniform dataset - {name}\n"
            f"path: {out_root.as_posix()}\ntrain: {name}.txt\nval: images/val\ntest: images/test\n"
            f"nc: 1\nnames: ['plate']\n")
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
        "reader_crops": len(rows), "reader_empty_labels": sum(1 for r in rows if not r["readable"]),
        "reader_composite_crops": sum(1 for r in rows if r["source"] == "composite"),
        "reader_plate_only_crops": sum(1 for r in rows if r["source"] == "plate_only"),
        "skipped": skipped, "tiers": tiers,
        "readable_min_px": READABLE_MIN_PX,
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
    ap.add_argument("--boost", type=int, default=0,
                    help="Reader-only plate renders (no vehicle) added to plates.csv. "
                         "Off by default: every image in the set is a vehicle.")
    args = ap.parse_args()
    m = build(Path(args.dataset), Path(args.out), args.target, args.val_target, args.seed,
              args.workers, args.boost)
    print("UNIFORM DATASET", m["version"])
    print(f"synthetic train {m['synthetic_train_images']}  synthetic val {m['synthetic_val_images']}  "
          f"reader crops {m['reader_crops']} (composite {m['reader_composite_crops']}, "
          f"plate-only {m['reader_plate_only_crops']}, empty {m['reader_empty_labels']})  skipped {m['skipped']}")
    for t, info in m["tiers"].items():
        print(f"  {t:<9} {info['images']:>6}  plate px median {info['plate_px_median']}")
    print(f"elapsed {m['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
