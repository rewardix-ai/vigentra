"""Statistics and contact sheets for a built dataset.

An automatically generated dataset that nobody has looked at is a liability:
every systematic labelling error in it becomes a systematic error in the model,
and the numbers alone will not show you a box drawn around a headlight. This
produces the two things needed to actually check it -

**Numbers.** Size and difficulty distributions per split, the balance between
easy and hard, and the leakage check that proves no frame's near-duplicates
straddle train and val/test.

**Pictures.** Contact sheets showing, for every sample: the crop the model will
see, the plate box drawn on it, the plate itself blown up, its pixel
dimensions, its difficulty tags, the detector confidence and the OCR result if
there is one. Sorted worst-first, because the small end is where the errors
are and a sheet of easy plates proves nothing.

Usage
-----
    python tools/dataset_report.py --version v2
    python tools/dataset_report.py --version v2 --sheets --tag tiny
    python tools/dataset_report.py --version v2 --sheets --limit 400
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from _corpus import load_json, percentiles, write_json  # noqa: E402

log = logging.getLogger("dataset_report")

# --- contact sheet geometry ------------------------------------------------
TILE_W, TILE_H = 260, 210       # one sample's cell
PLATE_STRIP_H = 64              # the blown-up plate, along the bottom
CAPTION_H = 52
COLS, ROWS = 5, 4               # 20 samples per sheet
MARGIN = 8
BG = (28, 28, 32)
FG = (232, 232, 236)
DIM = (150, 150, 158)
BOX = (86, 219, 127)


def _put(canvas, text, org, scale=0.40, color=FG, thickness=1):
    cv2.putText(canvas, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thickness, cv2.LINE_AA)


def _fit(image, width, height):
    """Letterbox into a fixed cell without distorting the aspect ratio."""
    h, w = image.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((height, width, 3), np.uint8)
    scale = min(width / w, height / h)
    resized = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_NEAREST if scale > 1
                         else cv2.INTER_AREA)
    canvas = np.full((height, width, 3), BG, np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


def build_tile(row: dict, dataset_root: Path) -> np.ndarray:
    """One sample: context image with the box, the plate blown up, the facts."""
    tile = np.full((TILE_H, TILE_W, 3), BG, np.uint8)
    image_path = dataset_root / row["image_file"]
    image = cv2.imread(str(image_path))
    if image is None:
        _put(tile, "IMAGE MISSING", (10, TILE_H // 2), 0.5, (80, 80, 240))
        _put(tile, row.get("image_id", "")[:34], (10, TILE_H // 2 + 18), 0.34, DIM)
        return tile

    x1, y1, x2, y2 = [float(v) for v in eval(row["bbox"])] if isinstance(
        row["bbox"], str) else row["bbox"]

    context_h = TILE_H - PLATE_STRIP_H - CAPTION_H
    annotated = image.copy()
    cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), BOX, 1)
    tile[0:context_h, 0:TILE_W] = _fit(annotated, TILE_W, context_h)

    # The plate itself, at nearest-neighbour so the actual pixel count is
    # visible rather than smoothed into looking better than it is.
    px1, py1 = max(0, int(x1)), max(0, int(y1))
    px2, py2 = min(image.shape[1], int(x2)), min(image.shape[0], int(y2))
    if px2 > px1 and py2 > py1:
        plate = image[py1:py2, px1:px2]
        tile[context_h:context_h + PLATE_STRIP_H, 0:TILE_W] = _fit(
            plate, TILE_W, PLATE_STRIP_H)

    base = context_h + PLATE_STRIP_H + 13
    width_px = float(row.get("plate_width_px") or 0)
    height_px = float(row.get("plate_height_px") or 0)
    category = row.get("plate_size_category", "?")
    _put(tile, f"{width_px:.0f}x{height_px:.0f}px  {category}", (6, base), 0.40, FG)
    confidence = float(row.get("detection_confidence") or 0)
    readable = row.get("ocr_readable", "")
    _put(tile, f"conf {confidence:.2f}  {readable[:22]}", (6, base + 14), 0.34, DIM)
    tags = (row.get("difficulty_category") or "")[:40]
    _put(tile, tags, (6, base + 27), 0.32, (120, 200, 245))
    text = row.get("ocr_text") or ""
    if text:
        _put(tile, f"OCR: {text}", (6, base + 40), 0.36, BOX)
    return tile


def build_unverified_tile(row: dict) -> np.ndarray:
    """A proposal awaiting review, rendered from the ORIGINAL frame.

    Unverified proposals have no dataset image yet - that is the point, they
    are not in the dataset. The context is cut live from the frame the detector
    saw, with enough margin around the box to judge whether it is sitting on a
    vehicle's plate position or on a headlight.
    """
    tile = np.full((TILE_H, TILE_W, 3), BG, np.uint8)
    frame = cv2.imread(str(row.get("frame_path", "")))
    if frame is None:
        _put(tile, "FRAME MISSING", (10, TILE_H // 2), 0.5, (80, 80, 240))
        return tile

    bbox = row.get("bbox") or [0, 0, 0, 0]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    h, w = frame.shape[:2]
    # Context window: 6x the plate's own size, so the vehicle around it is
    # visible. A plate judged in isolation is impossible to judge.
    pad_x = max(40.0, (x2 - x1) * 3.0)
    pad_y = max(30.0, (y2 - y1) * 3.0)
    cx1, cy1 = int(max(0, x1 - pad_x)), int(max(0, y1 - pad_y))
    cx2, cy2 = int(min(w, x2 + pad_x)), int(min(h, y2 + pad_y))
    context = frame[cy1:cy2, cx1:cx2].copy()
    if context.size:
        cv2.rectangle(context, (int(x1 - cx1), int(y1 - cy1)),
                      (int(x2 - cx1), int(y2 - cy1)), BOX, 1)

    context_h = TILE_H - PLATE_STRIP_H - CAPTION_H
    tile[0:context_h, 0:TILE_W] = _fit(context, TILE_W, context_h)

    plate = frame[max(0, int(y1)):max(1, int(y2)), max(0, int(x1)):max(1, int(x2))]
    if plate.size:
        tile[context_h:context_h + PLATE_STRIP_H, 0:TILE_W] = _fit(
            plate, TILE_W, PLATE_STRIP_H)

    base = context_h + PLATE_STRIP_H + 13
    _put(tile, f"{float(row.get('plate_width_px') or 0):.0f}x"
               f"{float(row.get('plate_height_px') or 0):.0f}px  "
               f"{row.get('plate_size_category', '?')}", (6, base), 0.40, FG)
    _put(tile, f"conf {float(row.get('detection_confidence') or 0):.2f}  "
               f"{row.get('detection_source', '')}  "
               f"{row.get('readability', '')[:16]}", (6, base + 14), 0.32, DIM)
    _put(tile, "|".join(row.get("difficulty") or [])[:42], (6, base + 27), 0.32,
         (120, 200, 245))
    if row.get("ocr_text"):
        _put(tile, f"OCR: {row['ocr_text']}", (6, base + 40), 0.36, BOX)
    return tile


def build_sheets(rows: list[dict], dataset_root: Path, out_dir: Path,
                 title: str, limit: int, renderer=build_tile) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = rows[:limit]
    per_sheet = COLS * ROWS
    index: list[dict] = []

    for start in range(0, len(rows), per_sheet):
        chunk = rows[start:start + per_sheet]
        sheet_w = COLS * TILE_W + (COLS + 1) * MARGIN
        sheet_h = ROWS * TILE_H + (ROWS + 1) * MARGIN + 30
        sheet = np.full((sheet_h, sheet_w, 3), (18, 18, 22), np.uint8)
        label = f"{title}  [{start + 1}-{start + len(chunk)} of {len(rows)}]"
        _put(sheet, label, (MARGIN, 20), 0.52, FG)

        for i, row in enumerate(chunk):
            r, c = divmod(i, COLS)
            x = MARGIN + c * (TILE_W + MARGIN)
            y = 30 + MARGIN + r * (TILE_H + MARGIN)
            sheet[y:y + TILE_H, x:x + TILE_W] = (
                renderer(row, dataset_root) if renderer is build_tile
                else renderer(row))
            _put(sheet, f"{start + i + 1}", (x + 4, y + 12), 0.36, (250, 210, 90))

        name = f"{title.replace(' ', '_').replace('/', '-')}_{start // per_sheet:03d}.jpg"
        cv2.imwrite(str(out_dir / name), sheet,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        index.append({
            "sheet": name,
            "tiles": [{"n": start + i + 1, "image_id": r.get("image_id"),
                       "image_file": r.get("image_file"),
                       "plate_width_px": r.get("plate_width_px"),
                       "difficulty": r.get("difficulty_category")}
                      for i, r in enumerate(chunk)],
        })
    return index


def leakage_check(rows: list[dict]) -> dict:
    """Prove no (feed, time block) appears in more than one split.

    The dataset's central promise. Asserting it in the report rather than
    trusting the splitter means a future change to the split rule cannot break
    it silently - the number here would stop being zero.
    """
    blocks: dict[tuple, set] = defaultdict(set)
    for row in rows:
        key = (row.get("feed_id"), int(row.get("frame_number") or 0) // 20)
        blocks[key].add(row.get("split"))
    straddling = {f"{feed}:block{block}": sorted(splits)
                  for (feed, block), splits in blocks.items() if len(splits) > 1}
    return {
        "time_blocks": len(blocks),
        "blocks_in_multiple_splits": len(straddling),
        "offenders": dict(list(straddling.items())[:20]),
        "verdict": "CLEAN" if not straddling else "LEAKING",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="v2")
    parser.add_argument("--out", default="dataset")
    parser.add_argument("--sheets", action="store_true",
                        help="Also render contact sheets for visual review.")
    parser.add_argument("--tag", default=None,
                        help="Only sheet samples carrying this difficulty tag.")
    parser.add_argument("--limit", type=int, default=200,
                        help="Max samples per sheet set.")
    parser.add_argument("--unverified", action="store_true",
                        help="Sheet the proposals awaiting review instead of "
                             "the adjudicated dataset. This is the review "
                             "queue: judge each box, then feed the verdicts "
                             "back through prepare_dataset.py --verdicts.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(args.out) / args.version

    if args.unverified:
        pending_path = root / "unverified" / "pending_review.json"
        if not pending_path.exists():
            raise SystemExit(f"No pending_review.json under {root / 'unverified'}.")
        pending = load_json(pending_path)
        rows = pending.get("proposals", [])
        if args.tag:
            rows = [r for r in rows if args.tag in (r.get("difficulty") or [])]
        rows.sort(key=lambda r: float(r.get("plate_width_px") or 0))
        out_dir = root / "review" / "unverified_sheets"
        index = build_sheets(rows, root, out_dir,
                             f"{args.version} UNVERIFIED", args.limit,
                             renderer=build_unverified_tile)
        write_json(out_dir / "index.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "AWAITING ADJUDICATION - not part of any split",
            "how_to_review": (
                "For each numbered tile decide: 'plate' (the box sits at a "
                "plate mounting position on an identifiable vehicle), "
                "'not_plate' (signage, burned-in text, badge, lamp, bodywork, "
                "railings, road markings, background), or 'unsure' (no vehicle "
                "identifiable around the box). READABILITY IS NOT THE "
                "QUESTION - a plate too small to read is still 'plate'."),
            "count": len(rows),
            "sheets": index,
        })
        print(f"UNVERIFIED REVIEW SHEETS: {len(index)} sheets covering "
              f"{min(len(rows), args.limit)} of {len(rows)} proposals")
        print(f"    {out_dir}")
        return 0

    metadata = root / "metadata.csv"
    if not metadata.exists():
        raise SystemExit(f"No metadata.csv under {root}. Run prepare_dataset.py first.")

    with open(metadata, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    manifest = load_json(root / "manifest.json")

    by_split = Counter(r["split"] for r in rows)
    size_by_split: dict[str, Counter] = defaultdict(Counter)
    difficulty_by_split: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        size_by_split[row["split"]][row["plate_size_category"]] += 1
        for tag in (row.get("difficulty_category") or "").split("|"):
            if tag and tag != "none":
                difficulty_by_split[row["split"]][tag] += 1

    widths = [float(r["plate_width_px"]) for r in rows]
    leakage = leakage_check(rows)

    # Balance: what share of the training set is genuinely hard? A set whose
    # easy half dominates produces the failure this project started with.
    train = [r for r in rows if r["split"] == "train"]
    hard_train = [r for r in train
                  if r["plate_size_category"] in ("EXTREMELY_TINY", "VERY_SMALL")]
    balance = {
        "train_boxes": len(train),
        "train_tiny_or_smaller": len(hard_train),
        "train_hard_share": round(len(hard_train) / len(train), 3) if train else 0.0,
        "note": ("Share of training boxes in the two smallest bands. Below ~0.3 "
                 "the set is dominated by plates the detector already finds, and "
                 "training on it will not move small-plate recall."),
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": args.version,
        "unit": manifest.get("unit"),
        "label_provenance": manifest.get("label_provenance"),
        "size_bands": manifest.get("size_bands"),
        "plate_boxes": len(rows),
        "boxes_per_split": dict(by_split),
        "images_per_split": manifest.get("images_per_split"),
        "plate_width_px": percentiles(widths),
        "size_distribution_by_split": {k: dict(v) for k, v in size_by_split.items()},
        "difficulty_by_split": {k: dict(v) for k, v in difficulty_by_split.items()},
        "readability": dict(Counter(r.get("ocr_readable") for r in rows)),
        "balance": balance,
        "temporal_leakage": leakage,
    }

    sheets_written = 0
    if args.sheets:
        pool = rows
        title = f"{args.version} all"
        if args.tag:
            pool = [r for r in rows
                    if args.tag in (r.get("difficulty_category") or "")]
            title = f"{args.version} {args.tag}"
        # Worst first: smallest plates at the top of the first sheet, because
        # that is where a labelling error does the most damage and is hardest
        # to spot.
        pool = sorted(pool, key=lambda r: float(r["plate_width_px"]))
        index = build_sheets(pool, root, root / "review" / "sheets", title,
                             args.limit)
        write_json(root / "review" / "sheets" / "index.json", {
            "generated_at": report["generated_at"],
            "title": title,
            "how_to_review": (
                "Each tile shows the training image with the label box, the "
                "plate blown up at nearest-neighbour, its pixel size and its "
                "difficulty tags. Judge ONLY 'is that box on a plate'. "
                "Readability is a separate question and is not what makes a "
                "box correct - a plate too small to read is still a plate."),
            "sheets": index,
        })
        sheets_written = len(index)
        report["contact_sheets"] = sheets_written

    write_json(root / "report.json", report)

    print("=" * 72)
    print(f"DATASET REPORT {args.version}   {len(rows)} plate boxes")
    print("=" * 72)
    print(f"Boxes per split : {dict(by_split)}")
    print(f"Images per split: {manifest.get('images_per_split')}")
    print(f"Plate width px  : {report['plate_width_px']}")
    print("\nSize distribution by split:")
    header = sorted({c for v in size_by_split.values() for c in v})
    print(f"    {'split':<8}" + "".join(f"{h:>17}" for h in header))
    for split in ("train", "val", "test"):
        counts = size_by_split.get(split, Counter())
        print(f"    {split:<8}" + "".join(f"{counts.get(h, 0):>17}" for h in header))
    print("\nBalance:")
    print(f"    train boxes                {balance['train_boxes']:>6}")
    print(f"    of which tiny/very-small   {balance['train_tiny_or_smaller']:>6}"
          f"   ({balance['train_hard_share']:.0%})")
    print(f"\nTemporal leakage: {leakage['verdict']} "
          f"({leakage['blocks_in_multiple_splits']} of {leakage['time_blocks']} "
          f"blocks straddle a split)")
    print(f"\nLabel provenance: human_verified="
          f"{report['label_provenance'].get('human_verified')}")
    if sheets_written:
        print(f"Contact sheets  : {sheets_written} -> "
              f"{root / 'review' / 'sheets'}")
    print(f"\nWritten: {root / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
