"""Measure whether each camera can actually support ANPR, and say why.

"Is this camera good enough to read plates?" is a placement and bitrate fact,
not a software setting, and no model trains past it. This answers it by
measurement rather than assumption: it samples real frames from a camera,
detects the vehicles in them, and asks how large those vehicles - and therefore
their plates - actually are in the frame.

Ported from the ANPR platform in D:/tessttt (tools/survey.py). The estimate is
deliberately a favourable-case UPPER bound: plate width is taken as a fraction
of vehicle-box width, so a camera this tool calls INFEASIBLE genuinely cannot
do ANPR, while one it calls CAPABLE is worth trying rather than guaranteed.

It reuses the edge worker's own grid capture and detector, so it measures the
exact pipeline that would run in production, and touches nothing the live
services depend on - it is a standalone read-only probe.

    python -m tools.diagnose_cameras --cameras cam01,cam02 --frames 40
    python -m tools.diagnose_cameras --all --out reports/anpr_suitability.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

#: The worker package sits one level up from tools/, and this file is run both
#: as `python -m tools.diagnose_cameras` and as a plain script - only the first
#: of those puts the parent on the path by itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The plate is roughly a quarter of a vehicle box's width, head-on. A generous
# upper bound on purpose - see the module docstring.
PLATE_FACTOR = 0.25

# The size bands come from the running pipeline rather than being restated
# here. They describe the same physical quantity - plate width in pixels - and
# two copies of that number drifting apart is how a tool ends up calling a
# camera capable that the pipeline then reports as unreadable.
#
# This tool applies them to an ESTIMATE of plate width, and the pipeline
# applies them to a measured plate box, so this side is the optimistic one.
# Sharing the constants keeps the two answers comparable and makes the
# optimism the only difference between them.
from anpr.readability import (  # noqa: E402 - needs the path setup above
    COMFORTABLE_PX,
    MARGINAL_PX,
    READABLE_PX,
)

#: COCO vehicle class ids -> label, matching the edge detector's vocabulary.
VEHICLE_CLASSES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (q / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return round(ordered[lo] * (1 - frac) + ordered[hi] * frac, 1)


def _verdict(widths: list[float], frames_scored: int) -> dict:
    """The suitability call and the numbers behind it."""
    est = [w * PLATE_FACTOR for w in widths]
    p90 = _percentile(est, 90) or 0.0
    frac_readable = (sum(1 for e in est if e >= READABLE_PX) / len(est)) if est else 0.0
    frac_marginal = (sum(1 for e in est if e >= MARGINAL_PX) / len(est)) if est else 0.0

    if not est:
        verdict = "NO_VEHICLES_OBSERVED"
        reason = f"no vehicles detected across {frames_scored} sampled frames"
    elif frac_readable >= 0.10 or p90 >= COMFORTABLE_PX:
        verdict = "ANPR_CAPABLE"
        reason = (f"{frac_readable:.0%} of vehicles reach the {READABLE_PX:.0f}px "
                  f"estimated plate width (p90={p90:.0f}px)")
    elif frac_marginal >= 0.15 or p90 >= READABLE_PX:
        verdict = "ANPR_MARGINAL"
        reason = (f"only {frac_marginal:.0%} of vehicles reach the marginal "
                  f"{MARGINAL_PX:.0f}px estimate (p90={p90:.0f}px)")
    else:
        verdict = "ANPR_INFEASIBLE"
        reason = (f"p90 estimated plate width {p90:.0f}px is below the "
                  f"{MARGINAL_PX:.0f}px floor - vehicles are too small in frame")

    return {
        "anpr_verdict": verdict,
        "anpr_verdict_reason": reason,
        "vehicle_detections": len(widths),
        "fraction_readable": round(frac_readable, 3),
        "fraction_marginal": round(frac_marginal, 3),
        "estimated_plate_width_px": {
            "_method": f"vehicle_bbox_width * {PLATE_FACTOR}; favourable-case upper "
                       "bound, NOT a measured plate",
            "p50": _percentile(est, 50),
            "p90": p90,
            "max": round(max(est), 1) if est else None,
        },
    }


def _measure(camera, detector, frames: int, sample_interval: int) -> dict:
    """Capture, detect, and judge one camera."""
    from anpr.detect import Box  # noqa: F401 - ensures the CV stack is importable

    widths: list[float] = []
    classes: list[str] = []
    resolution: str | None = None
    scored = 0
    from app import grid

    try:
        with grid.open_capture(camera) as capture:
            for frame in capture.frames(max_frames=frames * sample_interval):
                if frame.index % max(1, sample_interval) != 0:
                    continue
                scored += 1
                if resolution is None and frame.image is not None:
                    h, w = frame.image.shape[:2]
                    resolution = f"{w}x{h}"
                vehicles = detector.track_vehicles(frame.image)
                for box in vehicles:
                    if int(box.cls) not in VEHICLE_CLASSES:
                        continue
                    if box.w < 4 or box.h < 4:
                        continue
                    widths.append(box.w)
                    classes.append(VEHICLE_CLASSES[int(box.cls)])
                if scored >= frames:
                    break
    except Exception as exc:  # noqa: BLE001 - a dead camera is data, not a crash
        return {
            "camera_id": camera.id,
            "stream_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "anpr_verdict": "STREAM_UNAVAILABLE",
        }

    result = {
        "camera_id": camera.id,
        "stream_ok": scored > 0,
        "frames_scored": scored,
        "resolution": resolution,
        "class_mix": dict(Counter(classes).most_common()),
        **_verdict(widths, scored),
    }
    return result


def _load_detector():
    from anpr import config as _config
    from anpr.detect import Detector

    cfg = _config.load(os.getenv("ANPR_CONFIG", "config.yaml"))
    # The tool needs vehicle boxes and calls the same tracker the pipeline uses,
    # so the measurement matches what production would actually see.
    return Detector(cfg.detect)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cameras", help="comma-separated grid ids, e.g. cam01,cam02")
    ap.add_argument("--all", action="store_true", help="every camera in the catalogue")
    ap.add_argument("--frames", type=int, default=40, help="frames to score per camera")
    ap.add_argument("--sample-interval", type=int, default=10)
    ap.add_argument("--out", help="write the full JSON report here")
    args = ap.parse_args(argv)

    from app import grid

    base = os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud")
    catalogue = grid.fetch_catalogue(base)
    if not catalogue:
        print("No cameras in the catalogue - check SENTINEL_GRID_* credentials.")
        return 1

    if args.all:
        wanted = list(catalogue.values())
    elif args.cameras:
        ids = [c.strip() for c in args.cameras.split(",")]
        wanted = [catalogue[i] for i in ids if i in catalogue]
    else:
        print("Pass --all or --cameras cam01,cam02")
        return 2

    detector = _load_detector()
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "floors_px": {"comfortable": COMFORTABLE_PX, "readable": READABLE_PX,
                            "marginal": MARGINAL_PX},
              "cameras": []}

    print(f"{'camera':10} {'verdict':20} {'res':11} {'veh':>4}  reason")
    print("-" * 90)
    for camera in wanted:
        row = _measure(camera, detector, args.frames, args.sample_interval)
        report["cameras"].append(row)
        print(f"{row['camera_id']:10} {row.get('anpr_verdict','?'):20} "
              f"{str(row.get('resolution') or '-'):11} "
              f"{row.get('vehicle_detections', 0):>4}  "
              f"{row.get('anpr_verdict_reason', row.get('error', ''))}")

    counts = Counter(r.get("anpr_verdict", "?") for r in report["cameras"])
    print("-" * 90)
    print("summary:", ", ".join(f"{k}={v}" for k, v in counts.most_common()))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
