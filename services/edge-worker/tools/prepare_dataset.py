"""Build a plate-detection dataset from adjudicated boxes, hard cases first.

The rule this file exists to enforce
------------------------------------
**A model's own output is a proposal, not a label.** Training a detector on
boxes it produced teaches it that its mistakes are correct, and it grows more
confident about them every round. So nothing reaches `images/train` without an
adjudication attached, and every adjudication carries who made it. Unreviewed
proposals - including every proposal made by the detector this dataset is meant
to improve - land in `unverified/` with a review sheet, and stay there.

The second rule, which this whole exercise turns on
---------------------------------------------------
**A plate too small to read is still a plate.** Detection and readability are
different questions about different things. A 9 px plate is a real object at a
real location, and a detector that learns to ignore it is a detector that
cannot see distant vehicles at all. Readability is recorded beside every box,
never used to decide whether the box exists.

Layout produced
---------------
    dataset/<version>/
      images/{train,val,test}/     adjudicated positives + hard negatives
      labels/{train,val,test}/     YOLO boxes; an empty file means "background"
      hard_cases/<tag>/            index of the hard subset, by difficulty
      unverified/                  proposals awaiting human review
      metadata.csv                 one row per plate box, fully described
      manifest.json                provenance, counts, split rule, seed
      data.yaml                    ultralytics dataset config

Splitting
---------
By `(feed, time block)`, never by frame. Consecutive frames of one vehicle are
near-duplicates; scattering them randomly makes the validation set a copy of
the training set and the resulting score meaningless. A block is
`TIME_BLOCK_FRAMES` consecutive frames and is assigned whole, by a stable hash,
so rebuilding reproduces the same split exactly.

Usage
-----
    python tools/prepare_dataset.py --version v2
    python tools/prepare_dataset.py --version v2 --unit frame
"""
from __future__ import annotations

import argparse
import csv
import logging
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
for candidate in (str(WORKER_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import cv2  # noqa: E402

from anpr import enhance  # noqa: E402
from _corpus import (  # noqa: E402
    PHYSICAL_FLOOR_PX, PlateSample, Readability, SizeBands, angle_category,
    classify_difficulty, derive_bands, estimate_skew_deg, load_json,
    measure_frame, percentiles, split_of, to_yolo, write_json,
    TIME_BLOCK_FRAMES,
)

log = logging.getLogger("prepare_dataset")

#: Where the adjudicated proposals and the frames they refer to live. These are
#: real paths produced by this project's own capture and review tools; override
#: with --proposals / --verdicts / --frames-root.
DEFAULT_PROPOSALS = Path(r"D:/tessttt/testttttt/reports/proposals/proposals.json")
DEFAULT_VERDICTS = Path(r"D:/tessttt/testttttt/reports/proposals/verdicts.json")
DEFAULT_FRAMES = Path(r"D:/tessttt/testttttt/reports/deep/frames")

#: Margin added around a vehicle box when it becomes a training image, as a
#: fraction of the box. Matches detect._roi_plates so the crops the model
#: trains on have the same framing as the crops it will be given at run time.
VEHICLE_MARGIN = 0.04

#: A registration plate is always wider than it is tall. A single-row Indian
#: plate is about 4.5:1 and the stacked two-row variant still about 2:1, so a
#: box taller than it is wide cannot be a plate whatever the adjudication says.
#:
#: This guard exists because visual review of the small end found 36 boxes with
#: this shape, 22 of them 4 px wide, sitting on car windows and bodywork. They
#: were adjudicated "plate" by a vision model that could not resolve a 4 px box
#: and fell back on the plausible answer - there is a car, boxes on cars are
#: plates. Training on them teaches the detector that any small dark patch on a
#: vehicle is a plate, which is the false-positive behaviour we are trying to
#: remove.
#:
#: This is a GEOMETRY test, never a size test. Quarantining a plate for being
#: small is exactly the bug this project exists to fix, so size-based exclusion
#: is off by default and has to be asked for (--min-plate-width).
MIN_PLATE_ASPECT = 1.2

SPLITS = ("train", "val", "test")


# ---------------------------------------------------------------------------
# Grouping proposals into images
# ---------------------------------------------------------------------------


def group_by_image(proposals: list[dict], verdicts: dict, unit: str
                   ) -> dict[tuple, list[dict]]:
    """Collect every adjudicated box that belongs to the same output image.

    For `vehicle_crop` the image is one vehicle in one frame, so boxes are
    keyed by (frame, vehicle box). For `frame` the whole frame is the image.
    Grouping first is what makes the positive/negative decision safe: a crop is
    only a background image when EVERY box in it was judged not-a-plate.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for proposal in proposals:
        verdict = verdicts.get(proposal.get("proposal_id"))
        if verdict is None:
            continue
        row = dict(proposal)
        row["verdict"] = verdict.get("verdict")
        row["adjudicated_by"] = verdict.get("adjudicated_by")
        row["plate_text"] = verdict.get("plate_text")
        if unit == "frame":
            key = (proposal["frame"], None)
        else:
            key = (proposal["frame"], tuple(proposal.get("vehicle_box") or ()))
        groups[key].append(row)
    return groups


def crop_region(frame_bgr, vehicle_box, margin: float = VEHICLE_MARGIN):
    """Vehicle box expanded by *margin*, clipped. Returns (crop, x1, y1)."""
    height, width = frame_bgr.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in vehicle_box]
    dx, dy = (x2 - x1) * margin, (y2 - y1) * margin
    x1 = int(max(0, x1 - dx))
    y1 = int(max(0, y1 - dy))
    x2 = int(min(width, x2 + dx))
    y2 = int(min(height, y2 + dy))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None, 0, 0
    return frame_bgr[y1:y2, x1:x2].copy(), x1, y1


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build(args) -> dict:
    proposals_doc = load_json(Path(args.proposals))
    proposals = (proposals_doc if isinstance(proposals_doc, list)
                 else proposals_doc.get("proposals", []))
    verdicts_doc = load_json(Path(args.verdicts))
    verdicts = verdicts_doc.get("verdicts", verdicts_doc)
    frames_root = Path(args.frames_root)

    if args.cameras:
        wanted = set(args.cameras)
        before = len(proposals)
        proposals = [p for p in proposals if (p.get("camera_id") or "") in wanted]
        log.info("camera filter %s: %d of %d proposals",
                 sorted(wanted), len(proposals), before)

    adjudicators = Counter(v.get("adjudicated_by") for v in verdicts.values())
    human_verified = set(adjudicators) == {"human"}

    root = Path(args.out) / args.version
    if root.exists() and args.overwrite:
        shutil.rmtree(root)
    for split in SPLITS:
        (root / "images" / split).mkdir(parents=True, exist_ok=True)
        (root / "labels" / split).mkdir(parents=True, exist_ok=True)
    (root / "unverified").mkdir(parents=True, exist_ok=True)

    groups = group_by_image(proposals, verdicts, args.unit)
    log.info("adjudicated proposals: %d, grouped into %d candidate images",
             sum(len(v) for v in groups.values()), len(groups))

    # First pass: measure every accepted plate box so the size bands can be
    # derived from THIS dataset rather than inherited from somewhere else.
    widths = [float(p.get("plate_w_px") or 0.0)
              for rows in groups.values() for p in rows
              if p["verdict"] == "plate"]
    bands = derive_bands(widths, provenance=(
        f"{len(widths)} adjudicated plate boxes in dataset {args.version}"))

    rows_out: list[dict] = []
    quarantined: list[dict] = []
    counts = Counter()
    per_split = Counter()
    hard_index: dict[str, list[str]] = defaultdict(list)
    frame_cache: dict[str, object] = {}

    for (frame_name, vehicle_box), rows in sorted(groups.items()):
        kinds = {r["verdict"] for r in rows}
        # An image containing anything a reviewer could not judge is not safe
        # as a positive OR a negative, so it is left out and counted.
        if "unsure" in kinds:
            counts["skipped_contains_unsure"] += 1
            continue
        plates = [r for r in rows if r["verdict"] == "plate"]
        negatives = [r for r in rows if r["verdict"] == "not_plate"]
        if not plates and not negatives:
            continue

        frame_path = frames_root / frame_name
        if not frame_path.exists():
            counts["frame_missing"] += 1
            continue
        image = frame_cache.get(frame_name)
        if image is None:
            image = cv2.imread(str(frame_path))
            if image is None:
                counts["frame_unreadable"] += 1
                continue
            frame_cache = {frame_name: image}      # 1-frame cache; frames are big
        frame_stats = measure_frame(image)

        camera = rows[0].get("camera_id") or frame_name.split("_")[0]
        frame_number = int("".join(c for c in Path(frame_name).stem
                                   if c.isdigit()) or 0)
        split = split_of(camera, frame_number, seed=args.seed,
                         block=args.time_block)

        if args.unit == "frame":
            crop, ox, oy = image, 0, 0
        else:
            crop, ox, oy = crop_region(image, vehicle_box)
            if crop is None:
                counts["vehicle_box_degenerate"] += 1
                continue
        crop_h, crop_w = crop.shape[:2]

        stem = (f"{camera}_{frame_number:04d}"
                f"_{int(ox)}_{int(oy)}_{crop_w}x{crop_h}")
        image_name = f"{stem}.jpg"
        cv2.imwrite(str(root / "images" / split / image_name), crop,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 95])

        label_lines: list[str] = []
        for plate in plates:
            px1, py1, px2, py2 = [float(v) for v in plate["plate_box"]]
            bbox = (px1 - ox, py1 - oy, px2 - ox, py2 - oy)
            # A plate whose box falls outside the crop is a grouping error, not
            # a label. Dropped and counted rather than clamped into fiction.
            if bbox[2] <= 0 or bbox[3] <= 0 or bbox[0] >= crop_w or bbox[1] >= crop_h:
                counts["plate_box_outside_crop"] += 1
                continue
            cx, cy, bw, bh = to_yolo(bbox, crop_w, crop_h)
            if bw <= 0 or bh <= 0:
                counts["plate_box_degenerate"] += 1
                continue

            plate_w = float(plate.get("plate_w_px") or (px2 - px1))
            plate_h = float(plate.get("plate_h_px") or (py2 - py1))
            aspect = plate_w / plate_h if plate_h > 0 else 0.0

            # Quarantine, not deletion: the box stays in metadata.csv with the
            # reason recorded, so the count is auditable and a reviewer can
            # overrule it. It simply does not become a training label.
            quarantine = ""
            if aspect < MIN_PLATE_ASPECT:
                quarantine = (f"implausible geometry: {plate_w:.0f}x{plate_h:.0f}px "
                              f"is aspect {aspect:.2f}, a plate is never taller "
                              f"than it is wide")
            elif args.min_plate_width and plate_w < args.min_plate_width:
                quarantine = (f"below --min-plate-width {args.min_plate_width}px "
                              f"(size-based exclusion, requested explicitly)")
            if quarantine:
                counts["quarantined"] += 1
                quarantined.append({
                    "proposal_id": plate.get("proposal_id"),
                    "frame": frame_name, "camera_id": camera,
                    "plate_box": plate.get("plate_box"),
                    "plate_w_px": plate_w, "plate_h_px": plate_h,
                    "aspect": round(aspect, 3),
                    "adjudicated_by": plate.get("adjudicated_by"),
                    "reason": quarantine,
                })
                continue

            label_lines.append(f"0 {cx} {cy} {bw} {bh}")
            patch = crop[max(0, int(bbox[1])):max(1, int(bbox[3])),
                         max(0, int(bbox[0])):max(1, int(bbox[2]))]
            skew = estimate_skew_deg(patch) if patch.size else None
            # Measured on the PLATE, not the frame. A sharp, well-exposed
            # junction frame routinely contains a plate that is blurred, dark
            # or blown out, and frame-level statistics report none of it - they
            # describe the scene, and the model is being trained on the patch.
            patch_q = (enhance.assess(patch) if patch.size
                       else enhance.assess(crop))
            sample = PlateSample(
                image_id=stem,
                feed_id=camera,
                frame_number=frame_number,
                frame_path=str(frame_path),
                bbox=bbox,
                plate_width_px=plate_w,
                plate_height_px=plate_h,
                plate_area_px=plate_w * plate_h,
                plate_area_frac=(plate_w * plate_h) / float(crop_w * crop_h),
                plate_aspect=plate_w / plate_h if plate_h > 0 else 0.0,
                plate_size_category=bands.classify(plate_w),
                detection_confidence=float(plate.get("confidence") or 0.0),
                detection_source=plate.get("source") or "vehicle_crop",
                blur_score=patch_q.sharpness,
                brightness=patch_q.luma,
                contrast=patch_q.contrast,
                glare=patch_q.glare,
                crop_quality=patch_q.score,
                skew_deg=skew,
                angle_category=angle_category(skew),
                touches_border=(bbox[0] <= 3 or bbox[1] <= 3
                                or bbox[2] >= crop_w - 3 or bbox[3] >= crop_h - 3),
                lighting=frame_stats.lighting,
                vehicles_in_frame=1,
            )
            # Readability is recorded, and is NEVER what decided the box above.
            # A plate below the physical floor is marked unreadable and stays a
            # plate; that distinction is the point of the whole dataset.
            text = plate.get("plate_text") or ""
            sample.ocr_text = text
            sample.readability = (
                Readability.READABLE.value if text
                else Readability.UNREADABLE_TOO_SMALL.value
                if plate_w < PHYSICAL_FLOOR_PX
                else Readability.NOT_ATTEMPTED.value)
            sample.difficulty = classify_difficulty(sample, bands)
            # Occlusion and visibility cannot be read off pixels. They are
            # emitted as UNKNOWN rather than guessed, and the row is flagged so
            # a reviewer can fill them in.
            row = sample.as_dict()
            row.update({
                "split": split,
                "image_file": f"images/{split}/{image_name}",
                "unit": args.unit,
                # Scene-level context, kept alongside the plate-level numbers
                # above so a reviewer can tell "dark night" from "dark plate on
                # a bright afternoon" - which need different fixes.
                "frame_blur_score": round(frame_stats.blur_score, 3),
                "frame_brightness": round(frame_stats.brightness, 2),
                "frame_contrast": round(frame_stats.contrast, 2),
                "visibility": "UNKNOWN",
                "occlusion": "UNKNOWN",
                "ocr_readable": sample.readability,
                "difficulty_category": "|".join(sample.difficulty) or "none",
                "label_source": "adjudicated_proposal",
                "adjudicated_by": plate.get("adjudicated_by"),
                "verified_by_human": plate.get("adjudicated_by") == "human",
                "proposal_id": plate.get("proposal_id"),
            })
            rows_out.append(row)
            counts[f"plate_boxes_{split}"] += 1
            for tag in sample.difficulty:
                hard_index[tag].append(row["image_file"])

        # An empty label file is a POSITIVE instruction to YOLO: "this image
        # contains no plate". That is exactly what a not_plate crop is, and it
        # is how the hard negatives teach the model to stop firing on
        # headlights and badges.
        label_path = root / "labels" / split / f"{stem}.txt"
        label_path.write_text("\n".join(label_lines) + ("\n" if label_lines else ""),
                              encoding="utf-8")
        per_split[split] += 1
        counts["images_positive" if label_lines else "images_negative"] += 1
        if not label_lines:
            counts[f"negatives_{split}"] += 1

    # --- unverified proposals from the current detector -------------------
    unverified = 0
    if args.samples and Path(args.samples).exists():
        doc = load_json(Path(args.samples))
        pending = [s for s in doc.get("samples", [])
                   if s.get("detection_source") != "prior"]
        write_json(root / "unverified" / "pending_review.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "why": ("Proposals from the deployed detector (plate_detector.pt). "
                    "These are NOT labels and are excluded from every split. "
                    "Adjudicate them with tools/dataset_report.py --sheets, "
                    "then re-run with --verdicts pointing at the result."),
            "count": len(pending),
            "proposals": pending,
        })
        unverified = len(pending)

    # --- metadata.csv -----------------------------------------------------
    if rows_out:
        fields = list(rows_out[0].keys())
        with open(root / "metadata.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows_out:
                writer.writerow(row)

    # --- hard-case index --------------------------------------------------
    for tag, files in hard_index.items():
        write_json(root / "hard_cases" / f"{tag}.json", {
            "difficulty": tag,
            "count": len(files),
            "note": ("Index into images/, not a copy. The same image may appear "
                     "under several tags, and duplicating it would silently "
                     "reweight the training set."),
            "images": sorted(set(files)),
        })

    # --- data.yaml --------------------------------------------------------
    (root / "data.yaml").write_text(
        f"# Vigentra plate-detection dataset {args.version}\n"
        f"# Built {datetime.now(timezone.utc).isoformat()}\n"
        f"# Labels: {'HUMAN VERIFIED' if human_verified else 'NOT human verified'}"
        f" - see manifest.json\n"
        f"path: {root.resolve().as_posix()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"test: images/test\n"
        f"nc: 1\n"
        f"names: ['plate']\n", encoding="utf-8")

    size_dist = Counter(r["plate_size_category"] for r in rows_out)
    difficulty_dist = Counter(t for r in rows_out
                              for t in (r["difficulty_category"].split("|")
                                        if r["difficulty_category"] != "none" else []))
    if quarantined:
        write_json(root / "quarantine" / "excluded_boxes.json", {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "why": ("Adjudicated 'plate' but excluded from the training labels "
                    "on geometry, not on size. Retained here so the exclusion "
                    "is auditable and a reviewer can overrule it."),
            "count": len(quarantined),
            "boxes": quarantined,
        })

    manifest = {
        "version": args.version,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "cameras": sorted(args.cameras) if args.cameras else "all",
        "unit": args.unit,
        "seed": args.seed,
        "time_block_frames": args.time_block,
        "split_rule": ("stable sha256 of (camera_id, frame_number // time_block); "
                       "a vehicle sequence cannot straddle splits"),
        "sources": {
            "proposals": str(args.proposals),
            "verdicts": str(args.verdicts),
            "frames_root": str(frames_root),
            "unverified_samples": str(args.samples or ""),
        },
        "label_provenance": {
            "label_source": "adjudicated_proposal",
            "adjudicated_by": dict(adjudicators),
            "human_verified": human_verified,
            "WARNING": (
                "human_verified is false unless every adjudication came from a "
                "person. A vision-model adjudication is a stronger filter than "
                "an unreviewed proposal and a weaker one than human review. Do "
                "not report accuracy from this set as measured against human "
                "ground truth."),
            "KNOWN_RESIDUAL": (
                "Plates no proposer ever proposed are absent from the labels "
                "and act as false negatives. This residual is unmeasured and "
                "cannot be measured without an exhaustively hand-labelled set. "
                "It biases most strongly against the smallest plates, which is "
                "the very class this dataset exists to improve."),
        },
        "size_bands": bands.as_dict(),
        "counts": dict(counts),
        "images_per_split": dict(per_split),
        "plate_boxes": len(rows_out),
        "size_distribution": dict(size_dist),
        "difficulty_distribution": dict(difficulty_dist),
        "plate_width_px": percentiles([r["plate_width_px"] for r in rows_out]),
        "unverified_proposals": unverified,
        "quarantined_boxes": len(quarantined),
        "quarantine_rule": (
            f"aspect < {MIN_PLATE_ASPECT} (geometrically impossible for a "
            f"plate); min_plate_width={args.min_plate_width}px"),
    }
    write_json(root / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default="v2")
    parser.add_argument("--out", default="dataset")
    parser.add_argument("--unit", choices=("vehicle_crop", "frame"),
                        default="vehicle_crop",
                        help="vehicle_crop matches the deployed ROI pass, where "
                             "a distant plate is upscaled before detection.")
    parser.add_argument("--proposals", default=str(DEFAULT_PROPOSALS))
    parser.add_argument("--verdicts", default=str(DEFAULT_VERDICTS))
    parser.add_argument("--frames-root", default=str(DEFAULT_FRAMES))
    parser.add_argument("--samples", default="reports/plate_samples.json",
                        help="Current-detector proposals, written to unverified/.")
    parser.add_argument("--cameras", nargs="*", default=None,
                        help="Restrict the build to these camera ids.")
    parser.add_argument("--min-plate-width", type=float, default=0.0,
                        help="Quarantine plates narrower than this. DEFAULT 0 - "
                             "off. Excluding plates for being small is the bug "
                             "this project exists to fix, so it must be asked "
                             "for deliberately.")
    parser.add_argument("--time-block", type=int, default=TIME_BLOCK_FRAMES)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest = build(args)

    print("=" * 72)
    print(f"DATASET {manifest['version']}   unit={manifest['unit']}   "
          f"{manifest['plate_boxes']} plate boxes")
    print("=" * 72)
    print("Images per split:", dict(manifest["images_per_split"]))
    print("Size distribution:", manifest["size_distribution"])
    print("Plate width px:", manifest["plate_width_px"])
    print("\nDifficulty:")
    for tag, n in sorted(manifest["difficulty_distribution"].items(),
                         key=lambda kv: -kv[1]):
        print(f"    {tag:<32} {n:>6}")
    print("\nBuild counters:", dict(manifest["counts"]))
    print(f"\nhuman_verified: {manifest['label_provenance']['human_verified']}"
          f"  (adjudicated_by={manifest['label_provenance']['adjudicated_by']})")
    print(f"unverified proposals held for review: "
          f"{manifest['unverified_proposals']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
