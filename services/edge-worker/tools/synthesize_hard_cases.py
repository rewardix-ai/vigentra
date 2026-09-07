"""Manufacture the worst cases, from real plates, with exact labels.

The estate has 634 verified plate boxes. It does not have ten thousand, and
inventing labels is forbidden. What it does have is a physically faithful model
of what its own cameras do to a plate (`anpr/degrade.py`): defocus, motion
blur, sensor noise, distance, JPEG, and a REAL H.264 encode at the bitrates the
grid actually delivers. Push every verified crop through that chain many times
with different draws and you get many genuinely different images - different
blur, different distance, different blocking - of a plate whose position is
known to the pixel. That is data, not duplication, and the label is exact
because nothing here moves the plate: every transform is label-preserving by
construction (no perspective warp, no rotation), and the only geometric op is
a crop-window jitter whose box arithmetic is trivial and asserted.

Four severity tiers, for a curriculum:

    mild      what a good camera does on a good day
    moderate  the estate's median
    severe    the estate's bad cameras: 60-150 kbps, plates at 10-25% scale
    extreme   past the readability floor - the plate is a smear, and the
              detector must still put a box on it

Real val and test images are never touched. Every synthetic image is marked
in the manifest with its source, tier and seed, so a result can always be
traced back to the real plate it came from.

Usage
-----
    python tools/synthesize_hard_cases.py --version v3_synth --target 10000
"""
from __future__ import annotations

import argparse
import csv
import logging
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
for candidate in (str(WORKER_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from anpr import degrade as dg  # noqa: E402
from _corpus import to_yolo, write_json  # noqa: E402

log = logging.getLogger("synthesize")

#: Severity tiers. `perspective=0.0` in all of them: a warp moves the plate and
#: this generator only emits transforms whose label arithmetic is exact.
#:
#: `scale` here is a placeholder - the distance step is set PER SAMPLE from
#: the tier's target plate width (`PLATE_PX`), because "scale the crop by 0.1"
#: means nothing on its own: on a 100 px crop it makes a 6 px image, on a
#: 700 px crop a 70 px one. What the detector has to survive is a plate of N
#: pixels, and N is what the estate's feed analysis measured.
TIERS: dict[str, dg.DegradeConfig] = {
    "mild": dg.DegradeConfig(
        blur_sigma=(0.3, 1.0), motion_len=(0, 3), noise_sigma=(1.0, 6.0),
        scale=(0.9, 0.9), jpeg_quality=(45, 80), codec_bitrate=(300_000, 900_000),
        perspective=0.0, brightness=(-15.0, 15.0), contrast=(0.9, 1.1),
        occlusion_bands=(0, 0)),
    "moderate": dg.DegradeConfig(
        blur_sigma=(0.6, 1.6), motion_len=(0, 6), noise_sigma=(3.0, 10.0),
        scale=(0.5, 0.5), jpeg_quality=(25, 60), codec_bitrate=(150_000, 400_000),
        perspective=0.0, brightness=(-28.0, 28.0), contrast=(0.8, 1.2),
        occlusion_bands=(0, 1), occlusion_width=(0.04, 0.10)),
    "severe": dg.DegradeConfig(
        blur_sigma=(1.0, 2.2), motion_len=(3, 9), noise_sigma=(6.0, 14.0),
        scale=(0.3, 0.3), jpeg_quality=(18, 40), codec_bitrate=(70_000, 180_000),
        perspective=0.0, brightness=(-45.0, 30.0), contrast=(0.7, 1.25),
        occlusion_bands=(0, 2), occlusion_width=(0.05, 0.16)),
    "extreme": dg.DegradeConfig(
        blur_sigma=(1.4, 2.6), motion_len=(5, 12), noise_sigma=(8.0, 18.0),
        scale=(0.2, 0.2), jpeg_quality=(14, 32), codec_bitrate=(50_000, 130_000),
        perspective=0.0, brightness=(-55.0, 35.0), contrast=(0.65, 1.3),
        occlusion_bands=(0, 2), occlusion_width=(0.06, 0.18)),
}

#: Target plate width in pixels AFTER the distance step, per tier. Anchored on
#: the estate: its median plate is ~15 px, its readability floor 60 px. The
#: extreme tier stops at 6 px - below that a plate is genuinely a point, and a
#: box on a point teaches the detector to fire on noise, which the smoke-test
#: sheet showed plainly.
PLATE_PX = {
    "mild": (40.0, 90.0),
    "moderate": (20.0, 45.0),
    "severe": (10.0, 22.0),
    "extreme": (6.0, 12.0),
}
#: For a negative crop (no plate to anchor on), the crop scale range instead.
NEGATIVE_SCALE = {
    "mild": (0.5, 0.9), "moderate": (0.3, 0.55),
    "severe": (0.15, 0.32), "extreme": (0.10, 0.20),
}


def scale_for(tier: str, boxes, rng: np.random.Generator) -> float:
    """Distance factor that lands the plate at the tier's target width."""
    if not boxes:
        return float(rng.uniform(*NEGATIVE_SCALE[tier]))
    plate_w = max(1.0, float(np.mean([b[2] - b[0] for b in boxes])))
    target = float(rng.uniform(*PLATE_PX[tier]))
    # Never enlarge (a real camera cannot add pixels) and never go below the
    # point where the crop itself is a handful of pixels.
    return float(np.clip(target / plate_w, 0.05, 0.95))

#: Share of the synthetic budget per tier. Weighted towards the hard end,
#: because the easy end is what the real data already covers.
TIER_SHARE = {"mild": 0.15, "moderate": 0.25, "severe": 0.35, "extreme": 0.25}

#: Night simulation on top of a tier, for a fraction of samples: gamma crush
#: plus a colour cast, which is what sodium/LED street lighting does.
NIGHT_FRACTION = 0.30


@dataclass
class Source:
    image: Path
    boxes: list[tuple[float, float, float, float]]   # xyxy pixels
    negative: bool


def load_sources(dataset: Path) -> list[Source]:
    """Every TRAIN image of the real dataset with its pixel boxes."""
    boxes_by_image: dict[str, list] = {}
    with open(dataset / "metadata.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["split"] != "train":
                continue
            bbox = [float(v) for v in row["bbox"].strip("[]").replace(",", " ").split()]
            boxes_by_image.setdefault(row["image_file"], []).append(tuple(bbox))
    sources = []
    for image in sorted((dataset / "images" / "train").glob("*.jpg")):
        rel = f"images/train/{image.name}"
        boxes = boxes_by_image.get(rel, [])
        sources.append(Source(image, boxes, negative=not boxes))
    return sources


def night(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    gamma = rng.uniform(1.6, 2.6)
    table = ((np.arange(256) / 255.0) ** gamma * 255.0).astype(np.uint8)
    out = cv2.LUT(img, table)
    # Sodium (warm) or LED (cool) cast.
    cast = np.array(rng.choice([[0.75, 0.9, 1.05], [1.05, 0.95, 0.8]]), dtype=np.float32)
    return np.clip(out.astype(np.float32) * cast, 0, 255).astype(np.uint8)


def jitter_window(img: np.ndarray, boxes, rng: np.random.Generator):
    """Re-frame the crop so the plate is not always in the same place.

    The window always keeps every plate fully inside; boxes are shifted by the
    window origin and that is the whole of the arithmetic.
    """
    h, w = img.shape[:2]
    # A verified box can run a little past the crop edge - a plate partly out
    # of frame. Its label inside this image is the visible part, so clamp
    # first; otherwise no window can contain it and the guard below fires.
    boxes = [(max(0.0, b[0]), max(0.0, b[1]), min(float(w), b[2]), min(float(h), b[3]))
             for b in boxes]
    boxes = [b for b in boxes if b[2] - b[0] >= 2 and b[3] - b[1] >= 2]
    if boxes:
        bx1 = min(b[0] for b in boxes); by1 = min(b[1] for b in boxes)
        bx2 = max(b[2] for b in boxes); by2 = max(b[3] for b in boxes)
    else:
        bx1, by1, bx2, by2 = w * 0.3, h * 0.3, w * 0.7, h * 0.7
    # Window between 70% and 100% of each dimension, but never smaller than
    # the plates plus a margin - a plate spanning most of the crop simply
    # gets a window the size of the crop.
    win_w = int(min(w, max(bx2 - bx1 + 8, rng.uniform(0.7 * w, w))))
    win_h = int(min(h, max(by2 - by1 + 8, rng.uniform(0.7 * h, h))))
    # Origin range that keeps every plate inside; when the range is empty
    # (plate as wide as the window) the only valid origin is its low end.
    lo_x, hi_x = max(0.0, bx2 - win_w), min(bx1, float(w - win_w))
    lo_y, hi_y = max(0.0, by2 - win_h), min(by1, float(h - win_h))
    x0 = int(rng.uniform(lo_x, hi_x)) if hi_x > lo_x else int(lo_x)
    y0 = int(rng.uniform(lo_y, hi_y)) if hi_y > lo_y else int(lo_y)
    x0 = max(0, min(x0, w - win_w)); y0 = max(0, min(y0, h - win_h))
    crop = img[y0:y0 + win_h, x0:x0 + win_w]
    shifted = [(b[0] - x0, b[1] - y0, b[2] - x0, b[3] - y0) for b in boxes]
    for b in shifted:
        assert b[0] >= -0.5 and b[1] >= -0.5 and b[2] <= win_w + 0.5 and b[3] <= win_h + 0.5, \
            "jitter window cut a plate - arithmetic error"
    return crop, shifted


def synthesize(dataset: Path, out_root: Path, target: int, seed: int,
               variants_cap: int) -> dict:
    sources = load_sources(dataset)
    positives = [s for s in sources if not s.negative]
    negatives = [s for s in sources if s.negative]
    log.info("sources: %d positive, %d negative train images", len(positives), len(negatives))

    # Negatives get a quarter of the budget: enough to keep the detector from
    # learning "degraded crop => plate", not enough to drown the positives.
    n_pos_target = int(target * 0.75)
    n_neg_target = target - n_pos_target
    per_pos = min(variants_cap, max(1, -(-n_pos_target // max(1, len(positives)))))
    per_neg = min(variants_cap, max(1, -(-n_neg_target // max(1, len(negatives)))))

    images_dir = out_root / "images" / "train"
    labels_dir = out_root / "labels" / "train"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    tier_names = list(TIERS)
    tier_p = np.array([TIER_SHARE[t] for t in tier_names]); tier_p /= tier_p.sum()

    manifest_rows = []
    counts = Counter()
    lists: dict[str, list[str]] = {t: [] for t in tier_names}
    made = 0

    def emit(source: Source, index: int) -> None:
        nonlocal made
        img = cv2.imread(str(source.image))
        if img is None:
            counts["unreadable_source"] += 1
            return
        tier = str(rng.choice(tier_names, p=tier_p))
        crop, boxes = jitter_window(img, list(source.boxes), rng)
        scale = scale_for(tier, boxes, rng)
        cfg = replace(TIERS[tier], scale=(scale, scale))
        sample_seed = int(rng.integers(0, 2**31 - 1))
        out = dg.degrade(crop, cfg, seed=sample_seed)
        is_night = rng.random() < NIGHT_FRACTION
        if is_night:
            out = night(out, rng)
        h, w = out.shape[:2]
        stem = f"syn_{tier}_{source.image.stem}_{index:02d}"
        cv2.imwrite(str(images_dir / f"{stem}.jpg"), out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        lines = []
        for b in boxes:
            cx, cy, bw, bh = to_yolo(b, w, h)
            if bw > 0 and bh > 0:
                lines.append(f"0 {cx} {cy} {bw} {bh}")
        (labels_dir / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""),
                                                encoding="utf-8")
        rel = f"images/train/{stem}.jpg"
        lists[tier].append(str((images_dir / f"{stem}.jpg").resolve()))
        manifest_rows.append({"image": rel, "source": str(source.image.name),
                              "tier": tier, "night": is_night, "seed": sample_seed,
                              "distance_scale": round(scale, 4),
                              "plate_px_after_distance": (
                                  round(float(np.mean([b[2] - b[0] for b in boxes])) * scale, 1)
                                  if boxes else None),
                              "boxes": len(lines), "negative": source.negative})
        counts[f"tier_{tier}"] += 1
        counts["negative" if source.negative else "positive"] += 1
        made += 1
        if made % 500 == 0:
            log.info("  %d synthetic images", made)

    def emit_safely(src: Source, i: int) -> None:
        # One bad sample must not kill a run that has an hour invested. It is
        # counted, named in the manifest, and the run continues.
        try:
            emit(src, i)
        except (AssertionError, cv2.error, ValueError) as exc:
            counts["skipped_error"] += 1
            if counts["skipped_error"] <= 20:
                log.warning("skipped %s variant %d: %s", src.image.name, i, exc)

    for src in positives:
        for i in range(per_pos):
            if counts["positive"] >= n_pos_target:
                break
            emit_safely(src, i)
    for src in negatives:
        for i in range(per_neg):
            if counts["negative"] >= n_neg_target:
                break
            emit_safely(src, i)

    # Real train images are copied in too, so one directory is the whole
    # training set and val/test stay the untouched real splits.
    real_list = []
    for src in sources:
        dst = images_dir / src.image.name
        if not dst.exists():
            shutil.copy2(src.image, dst)
            label_src = dataset / "labels" / "train" / f"{src.image.stem}.txt"
            if label_src.exists():
                shutil.copy2(label_src, labels_dir / f"{src.image.stem}.txt")
        real_list.append(str(dst.resolve()))
    for split in ("val", "test"):
        for kind in ("images", "labels"):
            src_dir = dataset / kind / split
            dst_dir = out_root / kind / split
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            shutil.copytree(src_dir, dst_dir)

    # Curriculum lists: each stage is everything easier plus the next tier.
    stages = {
        "stage1_real_mild": real_list + lists["mild"],
        "stage2_plus_moderate": real_list + lists["mild"] + lists["moderate"],
        "stage3_plus_severe": real_list + lists["mild"] + lists["moderate"] + lists["severe"],
        "stage4_all": real_list + sum((lists[t] for t in tier_names), []),
    }
    for name, entries in stages.items():
        (out_root / f"{name}.txt").write_text("\n".join(entries) + "\n", encoding="utf-8")
        (out_root / f"{name}.yaml").write_text(
            f"# Curriculum stage {name}: {len(entries)} train images. Val/test are REAL.\n"
            f"path: {out_root.resolve().as_posix()}\n"
            f"train: {name}.txt\nval: images/val\ntest: images/test\n"
            f"nc: 1\nnames: ['plate']\n", encoding="utf-8")
    (out_root / "data.yaml").write_text(
        f"path: {out_root.resolve().as_posix()}\ntrain: images/train\n"
        f"val: images/val\ntest: images/test\nnc: 1\nnames: ['plate']\n", encoding="utf-8")

    manifest = {
        "version": out_root.name,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "source_dataset": str(dataset),
        "real_train_images": len(real_list),
        "synthetic_images": made,
        "per_positive_source": per_pos, "per_negative_source": per_neg,
        "counts": dict(counts),
        "tiers": {t: {"images": len(lists[t]), "config": vars(TIERS[t])} for t in tier_names},
        "night_fraction": NIGHT_FRACTION,
        "curriculum_stages": {k: len(v) for k, v in stages.items()},
        "label_provenance": (
            "Every synthetic box is the verified box of its real source crop, "
            "shifted by the crop-window origin only. No transform here moves a "
            "plate. Readability of the result is NOT a label: an extreme-tier "
            "plate is a smear and is still labelled as a plate, because it is one."),
        "PROVENANCE_WARNING": (
            "Source labels are VLM-adjudicated, not human-verified; see the "
            "source dataset's manifest. Synthetic images inherit that."),
        "samples": manifest_rows,
    }
    write_json(out_root / "manifest.json", manifest)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="dataset/v2", help="Real, verified source.")
    ap.add_argument("--version", default="v3_synth")
    ap.add_argument("--out", default="dataset")
    ap.add_argument("--target", type=int, default=10000,
                    help="Synthetic images to generate (real train images are added on top).")
    ap.add_argument("--variants-cap", type=int, default=40,
                    help="Never more than this many variants from one source.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out_root = Path(args.out) / args.version
    if out_root.exists() and args.overwrite:
        shutil.rmtree(out_root)
    if dg._ffmpeg_exe() is None:
        raise SystemExit("ffmpeg not found - the H.264 pass is the whole point; install it.")

    manifest = synthesize(Path(args.dataset), out_root, args.target, args.seed,
                          args.variants_cap)
    print("=" * 70)
    print(f"SYNTHETIC HARD CASES {manifest['version']}")
    print("=" * 70)
    print(f"real train images     {manifest['real_train_images']}")
    print(f"synthetic images      {manifest['synthetic_images']}")
    for t, info in manifest["tiers"].items():
        print(f"  {t:<10}{info['images']:>7}")
    print("curriculum stages:", manifest["curriculum_stages"])
    print(f"\nWritten: {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
