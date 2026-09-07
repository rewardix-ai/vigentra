"""Evaluate the plate detector, stratified by plate size and difficulty.

A single mAP number is useless for this problem. A detector that finds every
large plate and no small one scores well, and is exactly the detector we are
trying to replace. So every metric here is reported per size band and per
difficulty tag, and the headline number is **recall on genuinely visible tiny
plates** - the plates that are in the labels, that a reviewer confirmed are
plates, and that the detector must find.

What is measured
----------------
Recall, precision and F1 per stratum, by greedy IoU matching against the
adjudicated labels. Unmatched predictions are false positives and are grouped
by what they look like, because "the detector fires on the timestamp overlay"
is a fixable finding and "precision is 0.6" is not.

What is NOT measured, and why
-----------------------------
True recall against *all* plates present. The labels only contain plates some
proposer proposed, so a plate no model ever saw is invisible to this
evaluation and silently counts as if it did not exist. That residual biases
against the smallest plates - the class this exists to measure. The number
here is therefore an upper bound on recall, and is reported as one.

Usage
-----
    python tools/eval_plate_detector.py --version v2 --split test
    python tools/eval_plate_detector.py --version v2 --split test --imgsz 1280
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
for candidate in (str(WORKER_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import cv2  # noqa: E402

from _corpus import SizeBands, load_json, percentiles, write_json  # noqa: E402

log = logging.getLogger("eval_plate_detector")

#: IoU at which a prediction counts as having found a label. Deliberately
#: loose. At 12 px wide a one-pixel offset costs ~0.25 IoU, so the usual 0.5
#: would score a correct detection as a miss purely because the plate is small -
#: which would bake the very bias we are trying to measure into the measurement.
MATCH_IOU = 0.30


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def load_labels(root: Path, split: str) -> dict[str, list[dict]]:
    """Ground-truth boxes per image, in pixels, from metadata.csv.

    metadata.csv rather than the YOLO .txt files: the CSV carries the size
    band, the difficulty tags and the readability that the stratification
    needs, and the two are written from the same rows so they cannot disagree.
    """
    with open(root / "metadata.csv", newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == split]
    by_image: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        bbox = row["bbox"]
        if isinstance(bbox, str):
            bbox = [float(v) for v in
                    bbox.strip("[]").replace(",", " ").split()]
        by_image[row["image_file"]].append({
            "bbox": bbox,
            "size": row["plate_size_category"],
            "difficulty": [t for t in (row.get("difficulty_category") or "").split("|")
                           if t and t != "none"],
            "readability": row.get("ocr_readable", ""),
            "width_px": float(row["plate_width_px"]),
        })
    return by_image


def evaluate(root: Path, split: str, imgsz: int | None, conf: float | None) -> dict:
    from anpr import config as anpr_config
    from anpr.detect import Detector

    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    if imgsz:
        cfg.detect.plate_imgsz = imgsz
    if conf is not None:
        cfg.detect.plate_conf = conf
    detector = Detector(cfg.detect)
    if not detector.ready:
        raise SystemExit("Plate detector not loaded. Set ANPR_MODELS_DIR.")

    labels = load_labels(root, split)
    if not labels:
        raise SystemExit(f"No labelled images in split '{split}' of {root}.")

    # Per-stratum tallies. A label belongs to one size band and may carry
    # several difficulty tags, so it is counted once per stratum it is in.
    hits: Counter = Counter()
    totals: Counter = Counter()
    false_positives: list[dict] = []
    matched_predictions = 0
    total_predictions = 0
    started = time.time()

    for index, (image_file, boxes) in enumerate(sorted(labels.items())):
        image = cv2.imread(str(root / image_file))
        if image is None:
            continue
        # The plate pass alone: this evaluates the DETECTOR, not the tracker or
        # the OCR scheduler, so no vehicle pass and no ROI upscaling. The crops
        # are already vehicle crops, which is what the ROI pass produces.
        predictions = detector._detect_plates(image)
        total_predictions += len(predictions)
        taken: set[int] = set()

        for label in boxes:
            best, best_iou = None, MATCH_IOU
            for i, pred in enumerate(predictions):
                if i in taken:
                    continue
                score = iou(label["bbox"], (pred.x1, pred.y1, pred.x2, pred.y2))
                if score > best_iou:
                    best, best_iou = i, score
            found = best is not None
            if found:
                taken.add(best)
                matched_predictions += 1

            for stratum in (f"size:{label['size']}",
                            f"readability:{label['readability']}",
                            "ALL"):
                totals[stratum] += 1
                hits[stratum] += int(found)
            for tag in label["difficulty"]:
                totals[f"difficulty:{tag}"] += 1
                hits[f"difficulty:{tag}"] += int(found)

        for i, pred in enumerate(predictions):
            if i not in taken:
                width = pred.x2 - pred.x1
                height = pred.y2 - pred.y1
                false_positives.append({
                    "image_file": image_file,
                    "bbox": [round(pred.x1, 1), round(pred.y1, 1),
                             round(pred.x2, 1), round(pred.y2, 1)],
                    "conf": round(float(pred.conf), 3),
                    "width_px": round(width, 1),
                    "aspect": round(width / height, 2) if height > 0 else 0.0,
                })

        if (index + 1) % 50 == 0:
            log.info("  %d/%d images, %.0fs", index + 1, len(labels),
                     time.time() - started)

    strata = {}
    for name, total in sorted(totals.items()):
        recall = hits[name] / total if total else 0.0
        strata[name] = {"labels": total, "found": hits[name],
                        "recall": round(recall, 4)}

    precision = matched_predictions / total_predictions if total_predictions else 0.0
    recall_all = hits["ALL"] / totals["ALL"] if totals["ALL"] else 0.0
    f1 = (2 * precision * recall_all / (precision + recall_all)
          if (precision + recall_all) else 0.0)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(root),
        "split": split,
        "images": len(labels),
        "settings": {"plate_imgsz": cfg.detect.plate_imgsz,
                     "plate_conf": cfg.detect.plate_conf,
                     "match_iou": MATCH_IOU,
                     "plate_model": cfg.detect.plate_model},
        "overall": {
            "labels": totals["ALL"], "found": hits["ALL"],
            "recall_upper_bound": round(recall_all, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "predictions": total_predictions,
            "false_positives": len(false_positives),
        },
        "strata": strata,
        "false_positive_shapes": {
            "width_px": percentiles([f["width_px"] for f in false_positives]),
            "aspect": percentiles([f["aspect"] for f in false_positives]),
            "very_wide_over_8:1": sum(1 for f in false_positives if f["aspect"] > 8),
            "taller_than_wide": sum(1 for f in false_positives if f["aspect"] < 1),
        },
        "false_positives": sorted(false_positives,
                                  key=lambda f: -f["conf"])[:200],
        "CAVEAT": (
            "recall_upper_bound, not recall. The labels contain only plates "
            "some proposer proposed; a plate no model ever saw is invisible "
            "here and does not count as a miss. The residual biases against "
            "the smallest plates."),
        "elapsed_s": round(time.time() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="v2")
    parser.add_argument("--out", default="dataset")
    parser.add_argument("--split", default="test", choices=("train", "val", "test"))
    parser.add_argument("--imgsz", type=int, default=None,
                        help="Override plate_imgsz, to sweep input resolution.")
    parser.add_argument("--conf", type=float, default=None,
                        help="Override plate_conf.")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(args.out) / args.version
    result = evaluate(root, args.split, args.imgsz, args.conf)

    out = Path(args.report or (root / f"eval_{args.split}.json"))
    write_json(out, result)

    overall = result["overall"]
    print("=" * 72)
    print(f"EVAL {args.version}/{args.split}   imgsz="
          f"{result['settings']['plate_imgsz']} conf="
          f"{result['settings']['plate_conf']}   IoU>={MATCH_IOU}")
    print("=" * 72)
    print(f"labels {overall['labels']}   found {overall['found']}   "
          f"recall<={overall['recall_upper_bound']:.3f}   "
          f"precision {overall['precision']:.3f}   f1 {overall['f1']:.3f}")
    print(f"predictions {overall['predictions']}   "
          f"false positives {overall['false_positives']}")

    print("\nRecall by plate size  <-- the number that matters is the tiny end")
    for band in ("EXTREMELY_TINY", "VERY_SMALL", "SMALL", "MEDIUM", "LARGE"):
        row = result["strata"].get(f"size:{band}")
        if row:
            print(f"    {band:<16} {row['found']:>4}/{row['labels']:<4} "
                  f"recall {row['recall']:.3f}")

    print("\nRecall by difficulty")
    for name, row in sorted(result["strata"].items()):
        if name.startswith("difficulty:"):
            print(f"    {name[11:]:<24} {row['found']:>4}/{row['labels']:<4} "
                  f"recall {row['recall']:.3f}")

    print("\nRecall by readability")
    for name, row in sorted(result["strata"].items()):
        if name.startswith("readability:"):
            print(f"    {name[12:]:<24} {row['found']:>4}/{row['labels']:<4} "
                  f"recall {row['recall']:.3f}")

    fp = result["false_positive_shapes"]
    print(f"\nFalse-positive shapes: width {fp['width_px']}")
    print(f"    wider than 8:1 (banner/OSD-like): {fp['very_wide_over_8:1']}")
    print(f"    taller than wide (not a plate):   {fp['taller_than_wide']}")
    print(f"\n{result['CAVEAT']}")
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
