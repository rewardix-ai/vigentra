"""Run the full ANPR pipeline on a LIVE grid camera for a few minutes and
report what it read, per vehicle track.

The offline estate frames give each vehicle one or two looks; live, a vehicle
yields dozens, which is what the track consensus and the multi-frame
enhancement were built for. This is the measurement that decides whether the
chain reads plates on this camera in production.

Credentials come from the environment (SENTINEL_GRID_EMAIL /
SENTINEL_GRID_PASSWORD, loaded from the repo's .env); the camera list from the
grid catalogue; the transport from app.grid (RTSP over TCP, PTS timing,
reconnect, HLS fallback). Nothing secret is written to the report.

    python tools/live_test.py --camera cam06 --minutes 10
    python tools/live_test.py --camera cam10 cam16 --minutes 5 --out reports/live_cam10_cam16.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(HERE))

log = logging.getLogger("live_test")


def load_env(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run_camera(camera_id: str, minutes: float, cfg, base_url: str, frame_stride: int) -> dict:
    import cv2  # noqa: F401  (transport option is set by app.grid before capture)
    from app import grid
    from anpr.pipeline import AnprPipeline

    catalogue = grid.fetch_catalogue(base_url)
    cam = catalogue.get(camera_id)
    if cam is None:
        raise SystemExit(f"{camera_id} is not in the grid catalogue ({len(catalogue)} cameras)")
    pipeline = AnprPipeline(cfg)
    if not pipeline.ready:
        raise SystemExit("plate detector did not load; check models/")
    pipeline.warmup()
    capture = grid.open_capture(cam)
    log.info("camera %s via %s", camera_id, capture.label.split(" ")[0])

    deadline = time.time() + minutes * 60
    frames = processed = 0
    widths: list[float] = []
    per_frame_plates = Counter()
    events = []
    t_first = None
    for frame in capture.frames():
        frames += 1
        if frame.discontinuity:
            pipeline.reset()
        if frames % frame_stride:
            if time.time() > deadline:
                break
            continue
        ts = frame.pts_ms / 1000.0
        result = pipeline.process_frame(frame.image, timestamp=ts)
        processed += 1
        t_first = t_first or time.time()
        per_frame_plates["vehicles"] += len(result.vehicles)
        for det in result.plates:
            if det.source == "prior":
                per_frame_plates["prior_bands"] += 1
                continue
            per_frame_plates["plate_boxes"] += 1
            per_frame_plates["attached" if det.vehicle is not None else "orphaned"] += 1
            widths.append(float(det.box.w))
        for ev in result.events:
            if ev.text:
                events.append({"t": round(ts, 1), "track": ev.track_id, "text": ev.text,
                               "score": round(float(ev.score), 3), "confirmed": bool(ev.confirmed)})
        if time.time() > deadline:
            break
    try:
        capture.release()
    except Exception:                                    # noqa: BLE001
        pass

    tracks = []
    for tc in pipeline.tracks.all_tracks():
        v = tc.verdict
        sizes = pipeline.readability.tracks.get(tc.track_id)
        tracks.append({
            "track": tc.track_id, "observations": v.observations,
            "best_plate_width_px": round(sizes.best_width, 1) if sizes else None,
            "crops": sizes.crops if sizes else 0,
            "text": v.text, "score": round(float(v.score), 3), "confirmed": bool(v.confirmed),
            "valid": bool(v.valid), "margin": round(float(v.margin), 3),
        })
    tracks.sort(key=lambda t: (-int(t["confirmed"]), -(t["best_plate_width_px"] or 0)))
    elapsed = time.time() - (t_first or time.time())
    widths.sort()
    def pct(p):
        return round(widths[min(len(widths) - 1, int(p * len(widths)))], 1) if widths else None
    return {
        "camera": camera_id, "transport": capture.label.split(" ")[0],
        "minutes": round(minutes, 1), "frames_received": frames, "frames_processed": processed,
        "processing_fps": round(processed / elapsed, 2) if elapsed > 0 else None,
        "totals": dict(per_frame_plates),
        "plate_width_px": {"p50": pct(0.5), "p90": pct(0.9), "max": pct(1.0), "n": len(widths)},
        "tracks_total": len(tracks),
        "tracks_with_plate_box": sum(1 for t in tracks if t["crops"]),
        "tracks_read": sum(1 for t in tracks if t["text"]),
        "tracks_confirmed": sum(1 for t in tracks if t["confirmed"]),
        "confirmed_plates": [t["text"] for t in tracks if t["confirmed"]],
        "tracks": tracks[:80],
        "events": events[-200:],
        "readability": pipeline.readability.describe(),
        "pipeline": pipeline.stats(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", nargs="+", required=True)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--frame-stride", type=int, default=5,
                    help="Process every Nth received frame (the worker's own cadence is 5).")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_env(WORKER_ROOT.parent.parent / ".env")
    if not os.getenv("SENTINEL_GRID_PASSWORD"):
        raise SystemExit("SENTINEL_GRID_PASSWORD is not set (repo .env)")
    from anpr import config as anpr_config
    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    stride = max(1, int(args.frame_stride))
    base_url = os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud")

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "frame_stride": stride,
              "config": {"engines": list(cfg.ocr.engines), "min_plate_width": cfg.ocr.min_plate_width,
                         "plate_model": cfg.detect.plate_model, "plate_model_frame": cfg.detect.plate_model_frame,
                         "sr_backend": cfg.enhance.sr_backend, "mfsr_model": cfg.enhance.mfsr_model},
              "cameras": {}}
    for cam in args.camera:
        try:
            report["cameras"][cam] = run_camera(cam, args.minutes, cfg, base_url, stride)
        except Exception as exc:                          # noqa: BLE001
            report["cameras"][cam] = {"camera": cam, "error": f"{type(exc).__name__}: {exc}"}
            log.error("%s: %s", cam, exc)
    out = Path(args.out or f"reports/live_{'_'.join(args.camera)}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 78)
    print("LIVE TEST")
    print("=" * 78)
    for cam, r in report["cameras"].items():
        if "error" in r:
            print(f"{cam:<10} ERROR {r['error']}")
            continue
        print(f"{cam:<10} {r['transport']:<5} {r['minutes']}min  frames {r['frames_received']} "
              f"(processed {r['frames_processed']}, {r['processing_fps']} fps)  vehicles/frame-sum {r['totals'].get('vehicles',0)}")
        print(f"           plate boxes {r['totals'].get('plate_boxes',0)} (attached {r['totals'].get('attached',0)})  "
              f"width p50 {r['plate_width_px']['p50']} p90 {r['plate_width_px']['p90']}")
        print(f"           tracks {r['tracks_total']}  with plate {r['tracks_with_plate_box']}  read {r['tracks_read']}  "
              f"CONFIRMED {r['tracks_confirmed']}: {r['confirmed_plates']}")
        for t in [t for t in r["tracks"] if t["text"]][:12]:
            print(f"             track {t['track']:<6} obs {t['observations']:<3} best {t['best_plate_width_px']:>6} px  "
                  f"{t['text']:<12} score {t['score']:.2f} margin {t['margin']:.2f} {'CONFIRMED' if t['confirmed'] else ''}")
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
