"""Compare two sets of plate weights on real CCTV frames, through the real pipeline.

Dataset metrics are measured on vehicle crops that a proposer already decided
were worth cropping. That is the right way to compare detectors, and it is not
the same question as "does the platform find more plates on a live junction".
The crops exclude every vehicle no proposer ever looked at, they exclude the
burned-in caption bars that only exist in a full frame, and they exclude the
association step that the funnel analysis showed was discarding 90% of what the
detector found.

So this runs the genuine `AnprPipeline` - vehicle tracking, both plate passes,
the prior band, consensus, the lot - over untouched full frames, once per weight
set, and reports what actually comes out the far end.

Reported per camera and in total:

  * plate boxes found, and their size distribution
  * how many were attached to a tracked vehicle rather than orphaned
  * confirmed plate readings (OCR is measured, never used to judge detection)
  * boxes with banner-like geometry, the signature of firing on burned-in text

Usage
-----
    python tools/compare_on_footage.py \
        --weights D:/ANPR/models/plate_detector.pt runs/plate/C_smallobj_aug/weights/best.pt \
        --labels baseline C_smallobj_aug --cameras cam06 cam07 --per-camera 25
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
for candidate in (str(WORKER_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import cv2  # noqa: E402

from _corpus import (  # noqa: E402
    discover_feeds, measure_frame, percentiles, sample_frames, write_json,
)

log = logging.getLogger("compare_on_footage")

DEFAULT_ROOTS = (
    Path(r"D:/tessttt/testttttt/reports/deep/frames"),
    Path(r"D:/tessttt/testttttt/reports/frames"),
)

#: A box this much wider than tall is not a plate shape; on this estate it is
#: the camera's caption bar. Counted rather than filtered, so the two weight
#: sets can be compared on how often they fire on one.
BANNER_ASPECT = 8.0


def run_one(weights: str, feeds, per_camera: int, imgsz: int | None,
            conf: float | None, device: str) -> dict:
    """Run the full pipeline over the sampled frames with one set of weights."""
    from anpr import config as anpr_config
    from anpr.pipeline import AnprPipeline

    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    cfg.detect.plate_model = weights
    if imgsz:
        cfg.detect.plate_imgsz = imgsz
        cfg.detect.roi_imgsz = imgsz
    if conf is not None:
        cfg.detect.plate_conf = conf
    if device:
        cfg.detect.device = device

    pipeline = AnprPipeline(cfg)
    per_camera_out = []
    widths, all_reads = [], []
    totals = Counter()
    started = time.time()

    for feed in feeds:
        pipeline.reset()
        frames = sample_frames(feed, per_camera)
        counts = Counter()
        cam_widths, cam_reads = [], []
        for index, path in enumerate(frames):
            image = cv2.imread(str(path))
            if image is None:
                continue
            stats = measure_frame(image)
            result = pipeline.process_frame(image, timestamp=index * 1.0)
            counts["frames"] += 1
            counts[f"lighting_{stats.lighting}"] += 1
            counts["vehicles"] += len(result.vehicles)
            for det in result.plates:
                if det.source == "prior":
                    counts["prior_bands"] += 1
                    continue
                counts["plate_boxes"] += 1
                counts[f"pass_{det.source}"] += 1
                if det.vehicle is not None:
                    counts["attached_to_vehicle"] += 1
                else:
                    counts["orphaned"] += 1
                box = det.box
                width, height = box.w, max(1e-6, box.h)
                cam_widths.append(width)
                if width / height > BANNER_ASPECT:
                    counts["banner_like"] += 1
            for event in result.events:
                if event.text:
                    cam_reads.append({"text": event.text, "score": event.score,
                                      "confirmed": event.confirmed})
                    counts["reads"] += 1
                    if event.confirmed:
                        counts["reads_confirmed"] += 1

        per_camera_out.append({
            "feed_id": feed.feed_id,
            "frames": counts["frames"],
            "vehicles": counts["vehicles"],
            "plate_boxes": counts["plate_boxes"],
            "attached_to_vehicle": counts["attached_to_vehicle"],
            "orphaned": counts["orphaned"],
            "banner_like": counts["banner_like"],
            "reads": counts["reads"],
            "reads_confirmed": counts["reads_confirmed"],
            "plate_width_px": percentiles(cam_widths),
            "lighting": {k[9:]: v for k, v in counts.items()
                         if k.startswith("lighting_")},
        })
        widths.extend(cam_widths)
        all_reads.extend(cam_reads)
        totals.update(counts)
        log.info("  %-16s frames=%-4d vehicles=%-4d plates=%-4d reads=%d",
                 feed.feed_id, counts["frames"], counts["vehicles"],
                 counts["plate_boxes"], counts["reads"])

    elapsed = time.time() - started
    frames_done = totals["frames"] or 1
    return {
        "weights": weights,
        "totals": dict(totals),
        "plate_width_px": percentiles(widths),
        "reads": all_reads,
        "seconds": round(elapsed, 1),
        "seconds_per_frame": round(elapsed / frames_done, 2),
        "fps": round(frames_done / elapsed, 2) if elapsed else 0.0,
        "per_camera": per_camera_out,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", nargs="+", required=True)
    ap.add_argument("--labels", nargs="*", default=None)
    ap.add_argument("--roots", nargs="*", default=None)
    ap.add_argument("--cameras", nargs="*", default=None)
    ap.add_argument("--per-camera", type=int, default=25)
    ap.add_argument("--imgsz", type=int, default=None,
                    help="Override plate_imgsz and roi_imgsz for both runs.")
    ap.add_argument("--conf", type=float, default=None)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default="reports/footage_comparison.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    roots = [Path(r) for r in args.roots] if args.roots else list(DEFAULT_ROOTS)
    feeds = [f for f in discover_feeds(roots) if f.kind == "frames" and f.frames]
    if args.cameras:
        wanted = set(args.cameras)
        feeds = [f for f in feeds if f.camera_id in wanted]
    if not feeds:
        raise SystemExit("No frame feeds found. Pass --roots.")

    labels = args.labels or [Path(w).stem for w in args.weights]
    if len(labels) != len(args.weights):
        raise SystemExit("--labels must have one entry per --weights")

    results = {}
    for label, weights in zip(labels, args.weights):
        log.info("\n=== %s (%s) ===", label, weights)
        results[label] = run_one(weights, feeds, args.per_camera, args.imgsz,
                                 args.conf, args.device)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feeds": [f.feed_id for f in feeds],
        "per_camera_frames": args.per_camera,
        "settings": {"imgsz": args.imgsz, "conf": args.conf},
        "runs": results,
    }
    write_json(Path(args.out), report)

    print("\n" + "=" * 78)
    print("REAL-FOOTAGE COMPARISON (full frames, full pipeline)")
    print("=" * 78)
    rows = ("frames", "vehicles", "plate_boxes", "attached_to_vehicle",
            "orphaned", "banner_like", "prior_bands", "reads", "reads_confirmed")
    print(f"{'metric':<24}" + "".join(f"{label:>18}" for label in labels))
    print("-" * 78)
    for row in rows:
        print(f"{row:<24}" + "".join(
            f"{results[label]['totals'].get(row, 0):>18}" for label in labels))
    print("-" * 78)
    for stat in ("p50", "p90", "max"):
        print(f"{'plate width ' + stat:<24}" + "".join(
            f"{results[label]['plate_width_px'].get(stat, 0):>18}"
            for label in labels))
    print("-" * 78)
    print(f"{'seconds/frame':<24}" + "".join(
        f"{results[label]['seconds_per_frame']:>18}" for label in labels))
    print(f"{'pipeline fps':<24}" + "".join(
        f"{results[label]['fps']:>18}" for label in labels))
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
