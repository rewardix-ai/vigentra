"""Measure every available feed, and find where small plates are lost.

Two questions, answered together because they need the same expensive pass over
the footage:

**What do these cameras actually deliver?** Resolution, lighting, blur, vehicle
counts, and above all the distribution of plate widths in pixels. The size
bands the rest of the tooling uses are derived from this distribution rather
than assumed (see `_corpus.derive_bands`).

**Where does the pipeline lose a plate?** The detector has several sequential
stages and every one of them can discard a candidate silently. A plate that is
never reported might have been missed by the full-frame pass, missed again by
the ROI pass, cut by a minimum-size guard, dropped by NMS, unattached to any
vehicle, or found and then denied an OCR call by the per-frame budget. "The
detector missed it" is a different bug from "the scheduler never looked at it",
and they have opposite fixes, so this counts each stage separately.

The funnel counts are the point of this tool. Everything else is context.

Usage
-----
    python tools/analyze_feeds.py --per-camera 20
    python tools/analyze_feeds.py --roots <dir> --per-camera 40 --ocr
    python tools/analyze_feeds.py --cameras cam01 cam06 --per-camera 60

Set ANPR_MODELS_DIR if the weights are not under services/edge-worker/models.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from _corpus import (  # noqa: E402
    Feed, PlateSample, Readability, angle_category, classify_difficulty,
    derive_bands, discover_feeds, estimate_skew_deg, frame_number_of,
    measure_frame, percentiles, readability_of, sample_frames, write_json,
)

log = logging.getLogger("analyze_feeds")

#: Default places to look for footage, in priority order. These are the
#: capture directories this project has actually produced; none is invented,
#: and a missing one is skipped rather than erroring.
DEFAULT_ROOTS = (
    WORKER_ROOT.parent.parent / "data" / "videos",
    Path(r"D:/tessttt/testttttt/reports/deep/frames"),
    Path(r"D:/tessttt/testttttt/reports/frames"),
)


# ---------------------------------------------------------------------------
# The funnel
# ---------------------------------------------------------------------------

#: Ordered stages a plate candidate passes through. Reported in this order so
#: the drop-off reads top to bottom.
FUNNEL_STAGES = (
    "frames_processed",
    "vehicles_detected",
    "vehicles_plate_bearing",
    "vehicles_too_small_for_roi",       # ROI guard: crop < 24px
    "roi_crops_submitted",
    "plates_won_by_full_frame_pass",
    "plates_won_by_roi_pass",
    "plates_after_nms",
    "plates_attached_to_vehicle",
    "plates_orphaned",
    "prior_bands_emitted",
    "prior_blocked_vehicle_too_narrow",  # prior_min_vehicle guard
    "crops_measured",
    "crops_below_physical_floor",
    "ocr_attempted",
    "ocr_returned_text",
)


class Funnel(Counter):
    """Stage counters, printed in a fixed order with drop-off percentages."""

    def render(self) -> list[str]:
        lines = []
        for stage in FUNNEL_STAGES:
            lines.append(f"    {stage:<36} {self[stage]:>8}")
        return lines


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def build_detector():
    from anpr import config as anpr_config
    from anpr.detect import Detector

    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    detector = Detector(cfg.detect)
    if not detector.ready:
        raise SystemExit(
            "The plate detector did not load. Point ANPR_MODELS_DIR at the "
            "directory holding plate_detector.pt (see docs/anpr.md)."
        )
    return cfg, detector


def build_ocr(cfg):
    from anpr.ocr import OcrEnsemble
    return OcrEnsemble(cfg.ocr)


def analyse_feed(feed: Feed, frames: list[Path], cfg, detector, ocr,
                 enhance_mod) -> tuple[list[PlateSample], Funnel, list[dict]]:
    """Run every detector stage over one feed's frames, counting the drops."""
    funnel = Funnel()
    samples: list[PlateSample] = []
    frame_rows: list[dict] = []

    detector.reset()
    for order, path in enumerate(frames):
        image = cv2.imread(str(path))
        if image is None:
            log.warning("unreadable frame: %s", path)
            continue
        height, width = image.shape[:2]
        frame_no = frame_number_of(path)
        stats = measure_frame(image)
        funnel["frames_processed"] += 1

        # --- the real pipeline, run exactly once -------------------------
        # One call, not a reconstruction: the tracker is stateful, so calling
        # its stages separately would advance it twice per frame and corrupt
        # every track id. Each box now carries the pass that produced it
        # (anpr/detect.py Box.source), which is what makes the split below
        # measurable without re-running anything.
        vehicles, detections = detector.process(image, order)
        funnel["vehicles_detected"] += len(vehicles)

        plate_bearing = [v for v in vehicles
                         if int(v.cls) in cfg.detect.vehicle_classes]
        funnel["vehicles_plate_bearing"] += len(plate_bearing)

        # The two size guards, evaluated exactly as detect.py applies them.
        # These are the places a distant vehicle is dropped before any plate
        # search happens, so they are counted rather than inferred.
        for v in plate_bearing:
            roi = v.expand(0.04, 0.04, width, height)
            x1, y1, x2, y2 = roi.as_int()
            if x2 - x1 < 24 or y2 - y1 < 24:
                funnel["vehicles_too_small_for_roi"] += 1
            else:
                funnel["roi_crops_submitted"] += 1
            if v.w < cfg.detect.prior_min_vehicle:
                funnel["prior_blocked_vehicle_too_narrow"] += 1

        # Attributed AFTER the NMS merge, so these say which pass *won* each
        # surviving plate - not how many raw boxes each pass proposed. That is
        # the more useful question: if the ROI pass wins nearly everything, the
        # full-frame pass is not finding these plates at all.
        real = [d for d in detections if d.source != "prior"]
        funnel["plates_won_by_full_frame_pass"] += len(
            [d for d in real if d.source == "frame"])
        funnel["plates_won_by_roi_pass"] += len(
            [d for d in real if d.source == "roi"])
        funnel["plates_after_nms"] += len(real)
        funnel["prior_bands_emitted"] += len(
            [d for d in detections if d.source == "prior"])
        funnel["plates_attached_to_vehicle"] += len(
            [d for d in real if d.vehicle is not None])
        funnel["plates_orphaned"] += len(
            [d for d in real if d.vehicle is None])

        # --- stage 4: measure every crop ---------------------------------
        for det in detections:
            if det.crop is None or det.crop.size == 0:
                continue
            funnel["crops_measured"] += 1
            quality = enhance_mod.assess(det.crop)
            box = det.box
            plate_w, plate_h = box.w, box.h
            if plate_w < 60.0:
                funnel["crops_below_physical_floor"] += 1

            skew = estimate_skew_deg(det.crop)
            sample = PlateSample(
                image_id=f"{feed.feed_id}_{frame_no:05d}_{len(samples):03d}",
                feed_id=feed.feed_id,
                frame_number=frame_no,
                frame_path=str(path),
                bbox=(box.x1, box.y1, box.x2, box.y2),
                plate_width_px=plate_w,
                plate_height_px=plate_h,
                plate_area_px=plate_w * plate_h,
                plate_area_frac=(plate_w * plate_h) / float(width * height),
                plate_aspect=plate_w / plate_h if plate_h > 0 else 0.0,
                plate_size_category="",          # filled once bands are known
                detection_confidence=float(box.conf),
                detection_source=det.source,
                blur_score=quality.sharpness,
                brightness=quality.luma,
                contrast=quality.contrast,
                glare=quality.glare,
                crop_quality=quality.score,
                skew_deg=skew,
                angle_category=angle_category(skew),
                touches_border=(box.x1 <= 3 or box.y1 <= 3
                                or box.x2 >= width - 3 or box.y2 >= height - 3),
                lighting=stats.lighting,
                vehicles_in_frame=len(vehicles),
            )

            if ocr is not None:
                funnel["ocr_attempted"] += 1
                variants = enhance_mod.build_variants(
                    det.crop, cfg.enhance, None, quality)
                text, confidence = "", 0.0
                if variants:
                    result = ocr.read(variants, allow_fallback=False)
                    if result.candidates:
                        best = max(result.candidates, key=lambda c: c.score)
                        text, confidence = best.text, float(best.score)
                if text:
                    funnel["ocr_returned_text"] += 1
                sample.ocr_text = text
                sample.ocr_confidence = confidence
                sample.readability = readability_of(sample, text, confidence)

            samples.append(sample)

        frame_rows.append({
            "frame_path": str(path), "frame_number": frame_no,
            "vehicles": len(vehicles), "plate_bearing": len(plate_bearing),
            "plates": len([d for d in detections if d.source != "prior"]),
            **stats.as_dict(),
        })

    return samples, funnel, frame_rows


def summarise(camera: str, samples: list[PlateSample], frame_rows: list[dict],
              funnel: Funnel, bands) -> dict:
    widths = [s.plate_width_px for s in samples if s.detection_source != "prior"]
    sizes = Counter(s.plate_size_category for s in samples
                    if s.detection_source != "prior")
    difficulty = Counter()
    for s in samples:
        difficulty.update(s.difficulty)
    return {
        "feed_id": camera,
        "frames_analysed": len(frame_rows),
        "resolutions": Counter(f"{r['width']}x{r['height']}"
                               for r in frame_rows).most_common(),
        "lighting": Counter(r["lighting"] for r in frame_rows).most_common(),
        "frame_blur": percentiles([r["blur_score"] for r in frame_rows]),
        "frame_brightness": percentiles([r["brightness"] for r in frame_rows]),
        "frame_contrast": percentiles([r["contrast"] for r in frame_rows]),
        "vehicles_total": funnel["vehicles_detected"],
        "vehicles_per_frame": round(
            funnel["vehicles_detected"] / max(1, len(frame_rows)), 2),
        "plate_boxes": len(widths),
        "plate_width_px": percentiles(widths),
        "plate_height_px": percentiles(
            [s.plate_height_px for s in samples if s.detection_source != "prior"]),
        "plate_area_frac": percentiles(
            [s.plate_area_frac for s in samples if s.detection_source != "prior"]),
        "plate_aspect": percentiles(
            [s.plate_aspect for s in samples if s.detection_source != "prior"]),
        "detection_confidence": percentiles(
            [s.detection_confidence for s in samples
             if s.detection_source != "prior"]),
        "size_distribution": dict(sizes),
        "difficulty_distribution": dict(difficulty),
        "detection_source": dict(Counter(s.detection_source for s in samples)),
        "readability": dict(Counter(s.readability for s in samples)),
        "funnel": dict(funnel),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--roots", nargs="*", default=None,
                        help="Directories of frames or videos. Defaults to the "
                             "project's known capture locations.")
    parser.add_argument("--cameras", nargs="*", default=None,
                        help="Restrict to these camera ids.")
    parser.add_argument("--per-camera", type=int, default=20,
                        help="Frames to sample per feed, spread evenly (0 = all).")
    parser.add_argument("--ocr", action="store_true",
                        help="Also run OCR, to measure readability. Much slower.")
    parser.add_argument("--out", default="reports/feed_analysis.json")
    parser.add_argument("--samples-out", default="reports/plate_samples.json",
                        help="Every measured plate box, for the mining stage.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s")

    roots = [Path(r) for r in args.roots] if args.roots else list(DEFAULT_ROOTS)
    feeds = discover_feeds(roots)
    if args.cameras:
        wanted = set(args.cameras)
        feeds = [f for f in feeds if f.camera_id in wanted]
    feeds = [f for f in feeds if f.kind == "frames" and f.frames]
    if not feeds:
        raise SystemExit(
            f"No frame feeds found under: {', '.join(str(r) for r in roots)}\n"
            "Pass --roots with a directory of captured frames.")

    log.info("Feeds discovered: %d", len(feeds))
    for f in feeds:
        log.info("  %-18s %5d frames   %s", f.feed_id, f.count, f.source)

    from anpr import enhance as enhance_mod
    cfg, detector = build_detector()
    ocr = build_ocr(cfg) if args.ocr else None
    log.info("\nplate_imgsz=%d roi_imgsz=%d roi_min_size=%d plate_conf=%.2f "
             "vehicle_conf=%.2f ocr=%s",
             cfg.detect.plate_imgsz, cfg.detect.roi_imgsz,
             cfg.detect.roi_min_size, cfg.detect.plate_conf,
             cfg.detect.vehicle_conf, bool(ocr))

    all_samples: list[PlateSample] = []
    per_feed: list[tuple[Feed, list[PlateSample], Funnel, list[dict]]] = []
    total_funnel = Funnel()
    started = time.time()

    for index, feed in enumerate(feeds, 1):
        frames = sample_frames(feed, args.per_camera or None)
        log.info("[%d/%d] %s: %d frames", index, len(feeds), feed.feed_id,
                 len(frames))
        samples, funnel, rows = analyse_feed(
            feed, frames, cfg, detector, ocr, enhance_mod)
        per_feed.append((feed, samples, funnel, rows))
        all_samples.extend(samples)
        total_funnel.update(funnel)
        log.info("        vehicles=%d plates=%d elapsed=%.0fs",
                 funnel["vehicles_detected"],
                 len([s for s in samples if s.detection_source != "prior"]),
                 time.time() - started)

    # Bands are derived from the WHOLE estate's detected plates, then applied
    # back to every sample - so "SMALL" means the same thing on every camera.
    detected = [s.plate_width_px for s in all_samples
                if s.detection_source != "prior"]
    bands = derive_bands(detected, provenance=(
        f"{len(detected)} detected plate boxes across {len(feeds)} feeds, "
        f"{total_funnel['frames_processed']} frames"))
    for s in all_samples:
        s.plate_size_category = bands.classify(s.plate_width_px)
        s.difficulty = classify_difficulty(s, bands)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": [str(r) for r in roots],
        "feeds": len(feeds),
        "frames_analysed": total_funnel["frames_processed"],
        "ocr_run": bool(ocr),
        "detector_config": {
            "plate_model": cfg.detect.plate_model,
            "vehicle_model": cfg.detect.vehicle_model,
            "plate_imgsz": cfg.detect.plate_imgsz,
            "roi_imgsz": cfg.detect.roi_imgsz,
            "vehicle_imgsz": cfg.detect.vehicle_imgsz,
            "roi_min_size": cfg.detect.roi_min_size,
            "plate_conf": cfg.detect.plate_conf,
            "vehicle_conf": cfg.detect.vehicle_conf,
            "process_width": cfg.detect.process_width,
        },
        "size_bands": bands.as_dict(),
        "estate_totals": {
            "plate_width_px": percentiles(detected),
            "size_distribution": dict(Counter(
                s.plate_size_category for s in all_samples
                if s.detection_source != "prior")),
            "difficulty_distribution": dict(Counter(
                t for s in all_samples for t in s.difficulty)),
            "detection_source": dict(Counter(
                s.detection_source for s in all_samples)),
            "readability": dict(Counter(s.readability for s in all_samples)),
        },
        "funnel_total": dict(total_funnel),
        "per_camera": [summarise(f.feed_id, s, r, fn, bands)
                       for f, s, fn, r in per_feed],
    }

    write_json(Path(args.out), report)
    write_json(Path(args.samples_out), {
        "generated_at": report["generated_at"],
        "size_bands": bands.as_dict(),
        "samples": [s.as_dict() for s in all_samples],
    })

    # --- human-readable summary ------------------------------------------
    print("\n" + "=" * 74)
    print(f"FEEDS {len(feeds)}   FRAMES {total_funnel['frames_processed']}   "
          f"PLATE BOXES {len(detected)}   {time.time() - started:.0f}s")
    print("=" * 74)
    print("\nDerived plate-size bands (px width):")
    for name, span in bands.as_dict()["edges_px"].items():
        count = report["estate_totals"]["size_distribution"].get(name, 0)
        print(f"    {name:<16} {span:>12}   n={count}")
    print(f"    physical readability floor: {bands.as_dict()['physical_readability_floor_px']:.0f}px "
          f"(10 glyphs x 6px)")
    print("\nPlate width percentiles (px):")
    print("   ", percentiles(detected))
    print("\nFunnel (whole estate):")
    for line in total_funnel.render():
        print(line)
    print("\nDifficulty tags:")
    for tag, n in sorted(report["estate_totals"]["difficulty_distribution"].items(),
                         key=lambda kv: -kv[1]):
        print(f"    {tag:<32} {n:>7}")
    print(f"\nWritten: {args.out}\n         {args.samples_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
