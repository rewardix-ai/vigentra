"""Per-size-band precision, recall and AP for a set of plate-detector weights.

Ultralytics reports one mAP over the whole set. That number is dominated by the
plates the model already finds, which is exactly the wrong summary here: a
detector can gain three points of mAP while its recall on tiny plates stays at
zero. So this computes the metrics separately for each size band, using the
bands derived from the footage rather than invented ones.

How a band is scored (COCO's convention, and for COCO's reason)
---------------------------------------------------------------
For band B, ground-truth boxes in B are the targets. Boxes in *other* bands are
neither targets nor background - they are **ignored**: a prediction that lands
on a MEDIUM plate while we are scoring TINY is not a false positive, it is
simply not part of this question. Counting it as a false positive would make
every band's precision depend on how many plates of other sizes happened to be
in shot.

Anything else unmatched is a false positive. AP is the all-point interpolated
area under the resulting precision-recall curve.

Matching runs at IoU >= 0.30 by default, not 0.50. At 12 px wide a one-pixel
offset costs about 0.25 IoU, so 0.50 scores a correct small-plate detection as
a miss - which would bake the very bias being measured into the measurement.
Pass --iou 0.5 to see the stricter number too.

Usage
-----
    python tools/eval_by_size.py --weights D:/ANPR/models/plate_detector.pt
    python tools/eval_by_size.py --weights runs/plate/C_smallobj_aug/weights/best.pt \
        --imgsz 960 --split test --tag C_smallobj_aug
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
for candidate in (str(WORKER_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import numpy as np  # noqa: E402

from _corpus import load_json, write_json  # noqa: E402

log = logging.getLogger("eval_by_size")

BANDS = ("EXTREMELY_TINY", "VERY_SMALL", "SMALL", "MEDIUM", "LARGE")
#: Difficulty tags reported alongside the size bands.
TAGS = ("tiny", "blur", "low_light", "overexposed", "glare", "low_contrast",
        "angled", "partial", "multi_vehicle", "low_confidence", "unreadable")
READABILITY = ("READABLE", "UNREADABLE_TOO_SMALL", "UNREADABLE_QUALITY",
               "NOT_ATTEMPTED")


def iou_matrix(preds: np.ndarray, gts: np.ndarray) -> np.ndarray:
    """IoU of every prediction against every ground-truth box."""
    if len(preds) == 0 or len(gts) == 0:
        return np.zeros((len(preds), len(gts)), dtype=np.float32)
    px1, py1, px2, py2 = preds[:, 0:1], preds[:, 1:2], preds[:, 2:3], preds[:, 3:4]
    gx1, gy1, gx2, gy2 = gts[:, 0], gts[:, 1], gts[:, 2], gts[:, 3]
    ix1 = np.maximum(px1, gx1)
    iy1 = np.maximum(py1, gy1)
    ix2 = np.minimum(px2, gx2)
    iy2 = np.minimum(py2, gy2)
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    pa = np.clip(px2 - px1, 0, None) * np.clip(py2 - py1, 0, None)
    ga = np.clip(gx2 - gx1, 0, None) * np.clip(gy2 - gy1, 0, None)
    union = pa + ga - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def average_precision(tp: np.ndarray, fp: np.ndarray, n_gt: int) -> float:
    """All-point interpolated AP from confidence-sorted TP/FP flags."""
    if n_gt == 0:
        return float("nan")
    tp_cum = np.cumsum(tp)
    fp_cum = np.cumsum(fp)
    recall = tp_cum / n_gt
    precision = tp_cum / np.maximum(tp_cum + fp_cum, 1e-9)
    # Make precision monotonically decreasing, then integrate.
    precision = np.maximum.accumulate(precision[::-1])[::-1]
    recall = np.concatenate(([0.0], recall))
    precision = np.concatenate(([precision[0] if len(precision) else 1.0], precision))
    return float(np.sum(np.diff(recall) * precision[1:]))


def score_stratum(per_image: list[dict], selector, iou_thresh: float) -> dict:
    """Precision/recall/AP for the subset of ground truth *selector* picks.

    `per_image` holds, for each image, its predictions (sorted later) and its
    ground-truth boxes with their attributes. Ground truth the selector rejects
    becomes an ignore region rather than background - see the module docstring.
    """
    records: list[tuple[float, int, int]] = []      # (conf, tp, fp)
    n_gt = 0

    for item in per_image:
        gts = item["gts"]
        target_idx = [i for i, g in enumerate(gts) if selector(g)]
        ignore_idx = [i for i, g in enumerate(gts) if not selector(g)]
        n_gt += len(target_idx)

        preds = item["preds"]
        if len(preds) == 0:
            continue
        boxes = np.array([p[:4] for p in preds], dtype=np.float32)
        confs = np.array([p[4] for p in preds], dtype=np.float32)
        order = np.argsort(-confs)

        gt_boxes = np.array([g["bbox"] for g in gts], dtype=np.float32) \
            if gts else np.zeros((0, 4), np.float32)
        ious = iou_matrix(boxes, gt_boxes)

        claimed_target: set[int] = set()
        for pi in order:
            best_t, best_iou = -1, iou_thresh
            for gi in target_idx:
                if gi in claimed_target:
                    continue
                if ious[pi, gi] >= best_iou:
                    best_t, best_iou = gi, ious[pi, gi]
            if best_t >= 0:
                claimed_target.add(best_t)
                records.append((float(confs[pi]), 1, 0))
                continue
            # Landed on a plate of a different size: not this band's business.
            if any(ious[pi, gi] >= iou_thresh for gi in ignore_idx):
                continue
            records.append((float(confs[pi]), 0, 1))

    if not records:
        return {"gt": n_gt, "detected": 0, "precision": 0.0, "recall": 0.0,
                "AP": float("nan") if n_gt == 0 else 0.0, "predictions": 0}

    records.sort(key=lambda r: -r[0])
    tp = np.array([r[1] for r in records], dtype=np.float32)
    fp = np.array([r[2] for r in records], dtype=np.float32)
    detected = int(tp.sum())
    return {
        "gt": n_gt,
        "detected": detected,
        "predictions": len(records),
        "precision": round(float(detected / max(1, len(records))), 4),
        "recall": round(float(detected / n_gt), 4) if n_gt else 0.0,
        "AP": round(average_precision(tp, fp, n_gt), 4),
    }


def collect(dataset: Path, split: str, weights: str, imgsz: int, conf: float,
            device: str, nms_iou: float) -> list[dict]:
    """Run the weights over the split and pair predictions with ground truth."""
    from ultralytics import YOLO

    with open(dataset / "metadata.csv", newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == split]
    gts_by_image: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        bbox = row["bbox"]
        if isinstance(bbox, str):
            bbox = [float(v) for v in bbox.strip("[]").replace(",", " ").split()]
        gts_by_image[row["image_file"]].append({
            "bbox": bbox,
            "size": row["plate_size_category"],
            "tags": [t for t in (row.get("difficulty_category") or "").split("|")
                     if t and t != "none"],
            "readability": row.get("ocr_readable", ""),
        })

    # Every image in the split, including the hard negatives - a model's false
    # positives on plate-free crops are exactly what we want counted.
    images = sorted((dataset / "images" / split).glob("*.jpg"))
    model = YOLO(weights)
    per_image: list[dict] = []
    started = time.time()

    for index, image_path in enumerate(images):
        rel = f"images/{split}/{image_path.name}"
        result = model.predict(str(image_path), imgsz=imgsz, conf=conf,
                               iou=nms_iou, device=device, verbose=False)[0]
        preds = []
        if result.boxes is not None and len(result.boxes):
            xyxy = result.boxes.xyxy.cpu().numpy()
            confs = result.boxes.conf.cpu().numpy()
            preds = [(*map(float, box), float(c)) for box, c in zip(xyxy, confs)]
        per_image.append({"image": rel, "preds": preds,
                          "gts": gts_by_image.get(rel, [])})
        if (index + 1) % 100 == 0:
            log.info("  %d/%d images (%.0fs)", index + 1, len(images),
                     time.time() - started)

    return per_image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weights", required=True)
    parser.add_argument("--dataset", default="dataset/v2")
    parser.add_argument("--split", default="test", choices=("train", "val", "test"))
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.001,
                        help="Detection floor for the PR curve. Low on purpose - "
                             "AP integrates the whole curve, so this is not a "
                             "deployment threshold.")
    parser.add_argument("--nms-iou", type=float, default=0.45)
    parser.add_argument("--iou", type=float, default=0.30,
                        help="Match IoU. 0.30 by default; see the docstring.")
    parser.add_argument("--device", default="0")
    parser.add_argument("--tag", default=None, help="Name for this run's report.")
    parser.add_argument("--out", default="reports/eval_by_size")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dataset = Path(args.dataset)
    tag = args.tag or Path(args.weights).stem

    log.info("evaluating %s on %s/%s at imgsz=%d", args.weights, dataset,
             args.split, args.imgsz)
    per_image = collect(dataset, args.split, args.weights, args.imgsz,
                        args.conf, args.device, args.nms_iou)

    overall = score_stratum(per_image, lambda g: True, args.iou)
    by_size = {b: score_stratum(per_image, lambda g, b=b: g["size"] == b, args.iou)
               for b in BANDS}
    by_tag = {t: score_stratum(per_image, lambda g, t=t: t in g["tags"], args.iou)
              for t in TAGS}
    by_read = {r: score_stratum(per_image, lambda g, r=r: g["readability"] == r,
                                args.iou)
               for r in READABILITY}

    total_preds = sum(len(i["preds"]) for i in per_image)
    negatives = [i for i in per_image if not i["gts"]]
    fp_on_negatives = sum(len(i["preds"]) for i in negatives)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tag": tag,
        "weights": args.weights,
        "dataset": str(dataset),
        "split": args.split,
        "settings": {"imgsz": args.imgsz, "conf_floor": args.conf,
                     "nms_iou": args.nms_iou, "match_iou": args.iou},
        "images": len(per_image),
        "overall": overall,
        "by_size": by_size,
        "by_difficulty": by_tag,
        "by_readability": by_read,
        "false_positives_on_plate_free_images": {
            "images": len(negatives),
            "predictions": fp_on_negatives,
            "per_image": round(fp_on_negatives / max(1, len(negatives)), 3),
        },
        "total_predictions": total_preds,
        "CAVEAT": ("Recall is an upper bound: the labels contain only plates "
                   "some proposer proposed, so a plate no model ever saw is "
                   "invisible here. The residual biases against small plates."),
    }
    out = Path(args.out) / f"{tag}_{args.split}_imgsz{args.imgsz}.json"
    write_json(out, report)

    print("=" * 78)
    print(f"{tag}   {args.split}   imgsz={args.imgsz}   match IoU>={args.iou}")
    print("=" * 78)
    print(f"{'stratum':<22}{'GT':>6}{'det':>6}{'recall':>9}{'prec':>8}{'AP':>8}")
    print("-" * 78)
    print(f"{'OVERALL':<22}{overall['gt']:>6}{overall['detected']:>6}"
          f"{overall['recall']:>9.3f}{overall['precision']:>8.3f}{overall['AP']:>8.3f}")
    print("-" * 78)
    for band in BANDS:
        s = by_size[band]
        if s["gt"]:
            print(f"{band:<22}{s['gt']:>6}{s['detected']:>6}"
                  f"{s['recall']:>9.3f}{s['precision']:>8.3f}{s['AP']:>8.3f}")
    print("-" * 78)
    for name in TAGS:
        s = by_tag[name]
        if s["gt"]:
            print(f"{name:<22}{s['gt']:>6}{s['detected']:>6}"
                  f"{s['recall']:>9.3f}{s['precision']:>8.3f}{s['AP']:>8.3f}")
    print("-" * 78)
    for name in READABILITY:
        s = by_read[name]
        if s["gt"]:
            print(f"{name:<22}{s['gt']:>6}{s['detected']:>6}"
                  f"{s['recall']:>9.3f}{s['precision']:>8.3f}{s['AP']:>8.3f}")
    fp = report["false_positives_on_plate_free_images"]
    print("-" * 78)
    print(f"false positives on the {fp['images']} plate-free images: "
          f"{fp['predictions']} ({fp['per_image']}/image)")
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
