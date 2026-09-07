"""Visual error analysis: what the model gets right, misses, and invents.

Metrics say a band's recall is 0.24. They do not say whether the misses are
plates a person can see, or 4 px smudges nothing could find - and those two
call for opposite responses. This renders the actual pixels behind each
outcome so the question can be settled by looking.

Categories, each sorted smallest-plate-first because that is where the errors
concentrate and where they are hardest to spot:

    hit_<band>       found, at that size - is the box really on the plate?
    miss_<band>      labelled and not found - was it findable at all?
    false_positive   predicted with no label under it - what is it firing on?
    tag_<tag>        the same outcomes within one difficulty class

Usage
-----
    python tools/error_analysis.py --weights runs/detect/runs/plate/C_smallobj_aug/weights/best.pt
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
for candidate in (str(HERE.parent), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from _corpus import MATCH_IOU, experiment_name, greedy_match, write_json  # noqa: E402
from eval_by_size import DEPLOYED_IMGSZ, DEPLOYED_NMS_IOU, collect  # noqa: E402

log = logging.getLogger("error_analysis")

TILE_W, TILE_H = 300, 230
CAPTION_H = 46
COLS, ROWS = 4, 3
MARGIN = 8
BG = (28, 28, 32)
FG = (232, 232, 236)
GT_COLOUR = (90, 200, 250)      # amber - where the label says the plate is
PRED_COLOUR = (86, 219, 127)    # green - what the model predicted
FP_COLOUR = (80, 80, 240)       # red


def _put(img, text, org, scale=0.42, colour=FG, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick,
                cv2.LINE_AA)


def _fit(img, w, h):
    ih, iw = img.shape[:2]
    if ih == 0 or iw == 0:
        return np.full((h, w, 3), BG, np.uint8)
    s = min(w / iw, h / ih)
    resized = cv2.resize(img, (max(1, int(iw * s)), max(1, int(ih * s))),
                         interpolation=cv2.INTER_NEAREST if s > 1 else cv2.INTER_AREA)
    canvas = np.full((h, w, 3), BG, np.uint8)
    y, x = (h - resized.shape[0]) // 2, (w - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


def build_tile(entry: dict, dataset: Path) -> np.ndarray:
    tile = np.full((TILE_H, TILE_W, 3), BG, np.uint8)
    image = cv2.imread(str(dataset / entry["image"]))
    if image is None:
        _put(tile, "IMAGE MISSING", (10, TILE_H // 2), 0.5, FP_COLOUR)
        return tile
    canvas = image.copy()
    # Label amber, prediction green. Where they coincide the box reads as both,
    # which is what a correct detection is supposed to look like.
    if entry.get("gt_bbox"):
        x1, y1, x2, y2 = (int(v) for v in entry["gt_bbox"])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), GT_COLOUR, 1)
    for box in entry.get("pred_bboxes", []):
        x1, y1, x2, y2 = (int(v) for v in box[:4])
        colour = FP_COLOUR if entry["kind"] == "false_positive" else PRED_COLOUR
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, 1)

    tile[0:TILE_H - CAPTION_H, :] = _fit(canvas, TILE_W, TILE_H - CAPTION_H)
    base = TILE_H - CAPTION_H + 14
    _put(tile, entry["headline"], (6, base), 0.42, FG)
    _put(tile, entry["detail"], (6, base + 14), 0.34, (150, 150, 158))
    _put(tile, (entry.get("tags") or "")[:44], (6, base + 27), 0.32, (120, 200, 245))
    return tile


def sheets(entries: list[dict], dataset: Path, out_dir: Path, title: str,
           limit: int) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = entries[:limit]
    per = COLS * ROWS
    index = []
    for start in range(0, len(entries), per):
        chunk = entries[start:start + per]
        width = COLS * TILE_W + (COLS + 1) * MARGIN
        height = ROWS * TILE_H + (ROWS + 1) * MARGIN + 30
        sheet = np.full((height, width, 3), (18, 18, 22), np.uint8)
        _put(sheet, f"{title}  [{start + 1}-{start + len(chunk)} of {len(entries)}]"
                    "    amber=label   green=prediction   red=false positive",
             (MARGIN, 20), 0.5, FG)
        for i, entry in enumerate(chunk):
            r, c = divmod(i, COLS)
            x = MARGIN + c * (TILE_W + MARGIN)
            y = 30 + MARGIN + r * (TILE_H + MARGIN)
            sheet[y:y + TILE_H, x:x + TILE_W] = build_tile(entry, dataset)
        name = f"{title.replace(' ', '_').replace('/', '-')}_{start // per:02d}.jpg"
        cv2.imwrite(str(out_dir / name), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        index.append({"sheet": name, "n": len(chunk)})
    return index


def classify(per_image: list[dict], match_iou: float) -> dict[str, list[dict]]:
    """Split every label and every prediction into hit / miss / false positive.

    Uses the same `greedy_match` as the metrics, so a plate the numbers count
    as found is a HIT tile here and never a MISS.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in per_image:
        gts, preds = item["gts"], item["preds"]
        claimed = greedy_match([p[:4] for p in preds], [p[4] for p in preds],
                               [g["bbox"] for g in gts], match_iou)
        by_gt = {gi: pi for pi, gi in enumerate(claimed) if gi >= 0}

        for gi, gt in enumerate(gts):
            width = gt["bbox"][2] - gt["bbox"][0]
            common = {"image": item["image"], "gt_bbox": gt["bbox"],
                      "tags": "|".join(gt["tags"]), "band": gt["size"],
                      "width_px": width}
            pi = by_gt.get(gi)
            if pi is not None:
                entry = {**common, "kind": "hit", "pred_bboxes": [preds[pi]],
                         "headline": f"HIT  {width:.0f}px  {gt['size']}",
                         "detail": f"conf {preds[pi][4]:.2f}"}
                buckets[f"hit_{gt['size']}"].append(entry)
            else:
                entry = {**common, "kind": "miss", "pred_bboxes": [],
                         "headline": f"MISS  {width:.0f}px  {gt['size']}",
                         "detail": f"{len(preds)} prediction(s) in image, "
                                   f"{len(by_gt)} matched other labels"}
                buckets[f"miss_{gt['size']}"].append(entry)
            for tag in gt["tags"]:
                buckets[f"tag_{tag}"].append(entry)

        for pi, pred in enumerate(preds):
            if claimed[pi] >= 0:
                continue
            width = pred[2] - pred[0]
            height = max(1e-6, pred[3] - pred[1])
            buckets["false_positive"].append({
                "image": item["image"], "gt_bbox": None, "kind": "false_positive",
                "pred_bboxes": [pred], "band": "", "width_px": width,
                "tags": "no label under this box",
                "headline": f"FALSE POSITIVE  {width:.0f}x{height:.0f}px",
                "detail": f"conf {pred[4]:.2f}  aspect {width / height:.1f}"})
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--dataset", default="dataset/v2")
    ap.add_argument("--split", default="test")
    ap.add_argument("--imgsz", type=int, default=DEPLOYED_IMGSZ)
    ap.add_argument("--conf", type=float, default=0.25,
                    help="A DEPLOYMENT threshold, not the AP floor: these sheets "
                         "should show what an operator would actually see.")
    ap.add_argument("--iou", type=float, default=MATCH_IOU)
    ap.add_argument("--device", default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--out", default="reports/error_analysis")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dataset = Path(args.dataset)
    tag = args.tag or experiment_name(args.weights)
    out_dir = Path(args.out) / tag

    per_image = collect(dataset, args.split, args.weights, args.imgsz,
                        args.conf, args.device, DEPLOYED_NMS_IOU)
    buckets = classify(per_image, args.iou)

    written = {}
    for name, entries in sorted(buckets.items()):
        if not entries:
            continue
        entries.sort(key=lambda e: e["width_px"])
        written[name] = {"count": len(entries),
                         "sheets": sheets(entries, dataset, out_dir, name,
                                          args.limit)}
    write_json(out_dir / "index.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weights": args.weights, "tag": tag, "split": args.split,
        "settings": {"imgsz": args.imgsz, "conf": args.conf,
                     "match_iou": args.iou},
        "categories": written,
    })

    print("=" * 64)
    print(f"ERROR ANALYSIS {tag}   conf={args.conf}   imgsz={args.imgsz}")
    print("=" * 64)
    for name, info in sorted(written.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {name:<28}{info['count']:>6}  ({len(info['sheets'])} sheet/s)")
    print(f"\nWritten: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
