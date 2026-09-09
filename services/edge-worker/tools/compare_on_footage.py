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
        --weights D:/ANPR/models/plate_detector.pt \
                  runs/detect/runs/plate/C_smallobj_aug/weights/best.pt \
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
    discover_feeds, experiment_name, percentiles, write_json,
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


def contiguous_window(feed, count: int) -> list:
    """*count* CONSECUTIVE frames from the middle of the feed.

    Not an even spread. The tracker needs consecutive sightings to assign an
    id and consensus needs several looks at one track; frames sampled seconds
    apart defeat both, and the reads column then compares zero with zero
    whatever the weights. Consecutive frames from the busiest part of the
    capture (its middle, as a cheap proxy) are what a live stream delivers.
    """
    frames = feed.frames
    if count <= 0 or count >= len(frames):
        return list(frames)
    start = max(0, (len(frames) - count) // 2)
    return frames[start:start + count]


def lighting_of(bgr) -> str:
    """DAY / DIM / NIGHT from mean luma alone - the only thing needed here.

    measure_frame() also computes a Laplacian variance over the whole 1080p
    frame (~50 ms); for a lighting label that is wasted on every frame.
    """
    from _corpus import DIM_LUMA, NIGHT_LUMA
    luma = float(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).mean())
    return "NIGHT" if luma < NIGHT_LUMA else "DIM" if luma < DIM_LUMA else "DAY"


def run_one(weights: str, feeds, per_camera: int, imgsz: int | None,
            conf: float | None, device: str, engines: tuple[str, ...] | None = None,
            min_plate_width: float | None = None, frame_weights: str | None = None) -> dict:
    """Run the full pipeline over the sampled frames with one set of weights."""
    from anpr import config as anpr_config
    from anpr.pipeline import AnprPipeline

    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    cfg.detect.plate_model = weights
    if frame_weights:
        cfg.detect.plate_model_frame = frame_weights
    if engines:
        cfg.ocr.engines = tuple(engines)
    if min_plate_width is not None:
        cfg.ocr.min_plate_width = min_plate_width
    if imgsz:
        cfg.detect.plate_imgsz = imgsz
        cfg.detect.roi_imgsz = imgsz
    if conf is not None:
        cfg.detect.plate_conf = conf
    if device:
        cfg.detect.device = device      # None keeps config.yaml's "auto"

    pipeline = AnprPipeline(cfg)
    if not pipeline.ready:
        # Detector._load swallows the load error and carries on with no plate
        # model, which would make these weights look like they find nothing.
        raise SystemExit(
            f"plate weights did not load: {weights}\n"
            "Pass an absolute path, or a path that exists relative to "
            f"{Path.cwd()}.")
    # Compile the CUDA kernels before the clock starts, or the first weight
    # set pays a multi-second one-off that the second inherits for free.
    pipeline.warmup()
    # Peak VRAM over the run, measured after warmup so the one-off kernel
    # compilation is not charged to the weights. None on a CPU host.
    try:
        import torch
        cuda = torch.cuda.is_available() and str(cfg.detect.device) != "cpu"
        if cuda:
            torch.cuda.reset_peak_memory_stats()
    except Exception:                                   # noqa: BLE001
        cuda = False
    per_camera_out = []
    widths, all_reads = [], []
    totals = Counter()
    started = time.time()

    for feed in feeds:
        pipeline.reset()
        frames = contiguous_window(feed, per_camera)
        counts = Counter()
        lighting = Counter()
        cam_widths, cam_reads = [], []
        interval = feed.frame_interval_s or 1.0
        for index, path in enumerate(frames):
            image = cv2.imread(str(path))
            if image is None:
                continue
            result = pipeline.process_frame(image, timestamp=index * interval)
            counts["frames"] += 1
            lighting[lighting_of(image)] += 1
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
            "lighting": dict(lighting),
        })
        widths.extend(cam_widths)
        all_reads.extend(cam_reads)
        totals.update(counts)
        log.info("  %-16s frames=%-4d vehicles=%-4d plates=%-4d reads=%d",
                 feed.feed_id, counts["frames"], counts["vehicles"],
                 counts["plate_boxes"], counts["reads"])

    elapsed = time.time() - started
    frames_done = totals["frames"] or 1
    peak_vram_mb = None
    if cuda:
        peak_vram_mb = round(torch.cuda.max_memory_allocated() / 2**20)
    return {
        "peak_vram_mb": peak_vram_mb,
        "weights": weights,
        "ocr_engines": list(pipeline.ocr.engine_names),
        "frame_weights": frame_weights,
        "min_plate_width": cfg.ocr.min_plate_width,
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
    ap.add_argument("--device", default=None,
                    help="None lets the pipeline pick (GPU if present).")
    ap.add_argument("--engines", nargs="*", default=None, metavar="A,B",
                    help="OCR engines per --weights entry, comma-separated "
                         "(e.g. paddle reader,paddle). Default: config.yaml's.")
    ap.add_argument("--frame-weights", nargs="*", default=None, metavar="W|-",
                    help="Per --weights entry: a separate detector for the full-frame pass "
                         "('-' = same model for both passes).")
    ap.add_argument("--min-plate-width", nargs="*", type=float, default=None,
                    help="OCR floor in px per --weights entry (default: config.yaml's).")
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

    labels = args.labels or [experiment_name(w) for w in args.weights]
    if len(labels) != len(args.weights):
        raise SystemExit("--labels must have one entry per --weights")
    if len(set(labels)) != len(labels):
        raise SystemExit(f"labels collide: {labels} - pass distinct --labels")

    engines = [tuple(e.split(",")) if e else None for e in (args.engines or [])]
    floors = list(args.min_plate_width or [])
    frames_w = [None if f in ("-", "") else f for f in (args.frame_weights or [])]
    for name, seq in (("--engines", engines), ("--min-plate-width", floors), ("--frame-weights", frames_w)):
        if seq and len(seq) != len(args.weights):
            raise SystemExit(f"{name} must have one entry per --weights")
    results = {}
    for i, (label, weights) in enumerate(zip(labels, args.weights)):
        log.info("\n=== %s (%s) ===", label, weights)
        results[label] = run_one(weights, feeds, args.per_camera, args.imgsz,
                                 args.conf, args.device,
                                 engines[i] if engines else None,
                                 floors[i] if floors else None,
                                 frames_w[i] if frames_w else None)

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
    print(f"{'peak VRAM (MB)':<24}" + "".join(
        f"{str(results[label].get('peak_vram_mb')):>18}" for label in labels))
    print(f"{'ocr engines':<24}" + "".join(
        f"{'+'.join(results[label].get('ocr_engines', [])):>18}" for label in labels))
    print(f"{'ocr floor px':<24}" + "".join(
        f"{str(results[label].get('min_plate_width')):>18}" for label in labels))
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
