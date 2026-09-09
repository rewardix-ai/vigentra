"""Run the full pipeline on a LIVE grid camera for a few minutes and report
what it confirmed, per vehicle track.

This is the measurement the offline frame folders cannot give: at full
delivery rate a vehicle yields dozens of looks instead of one or two, which
is what the track consensus and the multi-frame fusion exist for.

Uses the worker's own capture path (`app.grid` / `app.worker.iter_grid_frames`):
TCP transport, PTS timing, reconnect with backoff, loop discontinuities. The
grid host and credentials come from the repo's .env and are never written to
the report.

    python tools/live_test.py --camera cam06 --minutes 10 --stride 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
REPO_ROOT = WORKER_ROOT.parent.parent
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(HERE))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from _corpus import write_json  # noqa: E402

log = logging.getLogger("live_test")


def load_env(path: Path) -> None:
    """Minimal .env loader: KEY=value lines, no export, no interpolation."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def iter_video_frames(path: Path, stride: int):
    """(index, image, pts_seconds, cut) from a local clip, every stride-th frame."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    index = 0
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                return
            index += 1
            if index % max(1, stride):
                continue
            yield index, image, float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0, False
    finally:
        cap.release()


def run(camera_id: str, minutes: float, stride: int, out_dir: Path, device: str | None,
        video: Path | None = None) -> dict:
    from anpr import config as anpr_config
    from anpr.pipeline import AnprPipeline
    camera = None
    if video is None:
        load_env(REPO_ROOT / ".env")
        base_url = os.getenv("SENTINEL_GRID_BASE_URL", "").strip()
        if not base_url:
            raise SystemExit("SENTINEL_GRID_BASE_URL is not set (.env)")
        from app import grid, worker

    last_exc = None
    for attempt in range(4 if video is None else 0):
        try:
            cameras = grid.fetch_catalogue(base_url)
            if camera_id not in cameras:
                raise SystemExit(f"{camera_id} not in the catalogue; have: {sorted(cameras)[:12]} ...")
            camera = cameras[camera_id]
            break
        except SystemExit:
            raise
        except Exception as exc:                        # noqa: BLE001 - gateway 502s are routine
            last_exc = exc
            log.warning("catalogue read failed (%s); retrying", type(exc).__name__)
            time.sleep(3.0 * (attempt + 1))
    if camera is None and video is None:
        # The gateway's HTTP side is down but the documented stream pattern
        # still resolves the camera; compose it the way fetch_catalogue does
        # for entries without URLs. Same contract, same host, same port.
        log.warning("catalogue unavailable (%s); composing %s from the documented pattern",
                    type(last_exc).__name__ if last_exc else "?", camera_id)
        camera = grid.GridCamera(
            id=camera_id, name=f"Camera {camera_id}", location="", live=True,
            codec=None, width=None, height=None, declared_fps=None,
            rtsp_url=f"rtsp://{grid.RTSP_HOST}:{grid.RTSP_PORT}/stream/{camera_id}",
            hls_url=f"{base_url.rstrip('/')}/{camera_id}/index.m3u8",
        )

    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    if device:
        cfg.detect.device = device
    pipeline = AnprPipeline(cfg)
    if not pipeline.ready:
        raise SystemExit("plate weights did not load - check models/")
    pipeline.warmup()

    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(exist_ok=True)

    totals = Counter()
    per_minute: list[dict] = []
    confirmed: dict[int, dict] = {}
    reads_by_track: dict[int, list] = defaultdict(list)
    widths: list[float] = []
    tracks_seen: set[int] = set()
    started = time.time()
    deadline = started + minutes * 60.0
    minute_bucket = Counter()
    minute_start = started
    last_pts = None

    if video is not None:
        log.info("video: %s for up to %.1f min of wall time, every %d-th frame", video.name, minutes, stride)
        source = iter_video_frames(video, stride)
        label = video.stem
    else:
        log.info("live: %s for %.1f min, every %d-th delivered frame", camera.described, minutes, stride)
        source = worker.iter_grid_frames(camera, stride, max_frames=10**9)
        label = camera_id
    for index, image, pts_s, cut in source:
        if time.time() >= deadline:
            break
        if cut:
            pipeline.reset()
            totals["discontinuities"] += 1
        result = pipeline.process_frame(image, timestamp=pts_s)
        last_pts = pts_s
        totals["frames"] += 1
        minute_bucket["frames"] += 1
        totals["vehicles"] += len(result.vehicles)
        for v in result.vehicles:
            if v.track_id is not None:
                tracks_seen.add(int(v.track_id))
        for det in result.plates:
            if det.source == "prior":
                totals["prior_bands"] += 1
                continue
            totals["plate_boxes"] += 1
            minute_bucket["plates"] += 1
            if det.vehicle is not None:
                totals["attached"] += 1
            widths.append(float(det.box.w))
        for ev in result.events:
            if not ev.text:
                continue
            totals["reads"] += 1
            minute_bucket["reads"] += 1
            tid = int(getattr(ev, "track_id", -1) or -1)
            reads_by_track[tid].append({"text": ev.text, "score": round(float(ev.score), 3),
                                        "confirmed": bool(ev.confirmed), "pts": round(pts_s, 2)})
            if ev.confirmed and tid not in confirmed:
                totals["confirmed_tracks"] += 1
                minute_bucket["confirmed"] += 1
                crop = pipeline.best_crop(tid)
                crop_path = ""
                if crop is not None and crop.size:
                    crop_path = f"crops/{tid}_{ev.text}.jpg"
                    cv2.imwrite(str(out_dir / crop_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
                confirmed[tid] = {"track": tid, "text": ev.text, "score": round(float(ev.score), 3),
                                  "pts": round(pts_s, 2), "crop": crop_path}
                log.info("CONFIRMED track %s: %s (%.2f)", tid, ev.text, ev.score)
        if time.time() - minute_start >= 60.0:
            per_minute.append({"minute": len(per_minute) + 1, **dict(minute_bucket)})
            log.info("minute %d: %s", len(per_minute), dict(minute_bucket))
            minute_bucket = Counter()
            minute_start = time.time()

    elapsed = time.time() - started
    if minute_bucket:
        per_minute.append({"minute": len(per_minute) + 1, **dict(minute_bucket)})
    pw = np.array(widths) if widths else np.array([0.0])
    report = {
        "camera": label, "source": str(video) if video else "grid", "minutes": round(elapsed / 60.0, 2), "stride": stride,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": {"plate_model": cfg.detect.plate_model, "plate_model_frame": cfg.detect.plate_model_frame,
                     "engines": list(cfg.ocr.engines), "min_plate_width": cfg.ocr.min_plate_width,
                     "fuse_track_logits": cfg.ocr.fuse_track_logits},
        "totals": dict(totals),
        "tracks_seen": len(tracks_seen),
        "plate_width_px": {"p50": round(float(np.percentile(pw, 50)), 1), "p90": round(float(np.percentile(pw, 90)), 1),
                           "max": round(float(pw.max()), 1), "n": len(widths)},
        "fps_processed": round(totals["frames"] / elapsed, 2) if elapsed else 0.0,
        "stream_seconds_covered": round((last_pts or 0.0), 1),
        "per_minute": per_minute,
        "confirmed": list(confirmed.values()),
        "reads_by_track": {str(k): v for k, v in reads_by_track.items()},
        "pipeline": pipeline.report(),
    }
    write_json(out_dir / "live.json", report)
    return report


def sheet(report: dict, out_dir: Path) -> Path | None:
    rows = report.get("confirmed") or []
    if not rows:
        return None
    tiles = []
    for r in rows:
        crop = cv2.imread(str(out_dir / r["crop"])) if r.get("crop") else None
        tile = np.full((110, 420, 3), 255, np.uint8)
        if crop is not None and crop.size:
            h, w = crop.shape[:2]
            s = min(400 / w, 80 / h)
            crop = cv2.resize(crop, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_CUBIC)
            tile[4:4 + crop.shape[0], 10:10 + crop.shape[1]] = crop
        cv2.putText(tile, f"track {r['track']}  {r['text']}  score {r['score']}", (10, 102),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 190), 1)
        tiles.append(tile)
    grid_img = np.vstack(tiles)
    path = out_dir / "confirmed_sheet.jpg"
    cv2.imwrite(str(path), grid_img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", default=None, help="grid camera id, e.g. cam06")
    ap.add_argument("--video", default=None, help="a local full-rate clip instead of the grid")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--stride", type=int, default=3, help="process every N-th delivered frame")
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default=None, help="default reports/live/<camera>")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not args.camera and not args.video:
        raise SystemExit("pass --camera or --video")
    name = args.camera or Path(args.video).stem
    out_dir = Path(args.out) if args.out else WORKER_ROOT / "reports" / "live" / name
    rep = run(args.camera or name, args.minutes, args.stride, out_dir, args.device,
              Path(args.video) if args.video else None)
    t = rep["totals"]
    print("\n" + "=" * 70)
    print(f"LIVE {rep['camera']}  {rep['minutes']} min  stride {rep['stride']}  processed {t.get('frames',0)} frames "
          f"({rep['fps_processed']} fps), stream covered {rep['stream_seconds_covered']} s")
    print(f"vehicle detections {t.get('vehicles',0)}  distinct tracks {rep['tracks_seen']}  plates boxed {t.get('plate_boxes',0)}  "
          f"attached {t.get('attached',0)}  plate width p50 {rep['plate_width_px']['p50']} p90 {rep['plate_width_px']['p90']}")
    print(f"reads {t.get('reads',0)}  CONFIRMED tracks {t.get('confirmed_tracks',0)}")
    for r in rep["confirmed"]:
        print(f"   track {r['track']:<6} {r['text']:<12} score {r['score']}")
    s = sheet(rep, out_dir)
    print(f"Written: {out_dir / 'live.json'}" + (f"  sheet: {s}" if s else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
