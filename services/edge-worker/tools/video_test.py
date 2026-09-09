"""Run the full ANPR pipeline over a video file and report what it read.

Same chain as production (detection with one model per pass, association,
single- and multi-frame enhancement, PaddleOCR, grammar, track consensus),
driven by the file's own timestamps. Reports per vehicle track: best plate
width, observations, consensus text, confirmed or not. Writes a JSON report
and a sheet of the best crop per read track with its text, plus an
annotated frame every few seconds.

    python tools/video_test.py "D:/ANPR/data/uploads/delhi_small.mp4"
    python tools/video_test.py clip.mp4 --stride 2 --out reports/video_clip.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))

log = logging.getLogger("video_test")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--stride", type=int, default=2, help="process every Nth frame")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--annotate", default=None, metavar="OUT.mp4",
                    help="Write every processed frame with vehicle boxes, plate boxes and the "
                         "track's current read (green once confirmed) to this video.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from anpr import config as anpr_config
    from anpr.pipeline import AnprPipeline
    cfg = anpr_config.load(str(WORKER_ROOT / "config.yaml"))
    pipeline = AnprPipeline(cfg)
    if not pipeline.ready:
        raise SystemExit("plate detector did not load; check models/")
    pipeline.warmup()

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width, height = int(cap.get(3)), int(cap.get(4))
    stem = Path(args.video).stem
    out = Path(args.out or f"reports/video_{stem}.json")
    sheet_path = Path(args.sheet or f"reports/video_{stem}_sheet.jpg")
    frames_dir = out.parent / f"video_{stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    writer = None
    if args.annotate:
        Path(args.annotate).parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(args.annotate, cv2.VideoWriter_fourcc(*"mp4v"),
                                 max(1.0, fps / args.stride), (width, height))
    idx = processed = 0
    counts = Counter()
    widths: list[float] = []
    events = []
    t0 = time.time()
    last_saved = -1e9
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if args.max_frames and idx > args.max_frames:
            break
        if (idx - 1) % args.stride:
            continue
        pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if not pts:
            pts = idx / fps
        result = pipeline.process_frame(frame, timestamp=pts)
        processed += 1
        counts["vehicles"] += len(result.vehicles)
        for det in result.plates:
            if det.source == "prior":
                counts["prior_bands"] += 1
                continue
            counts["plate_boxes"] += 1
            counts["attached" if det.vehicle is not None else "orphaned"] += 1
            widths.append(float(det.box.w))
        for ev in result.events:
            if ev.text:
                events.append({"t": round(pts, 2), "track": ev.track_id, "text": ev.text,
                               "score": round(float(ev.score), 3), "confirmed": bool(ev.confirmed)})
        if writer is not None:
            ann = frame.copy()
            for v in result.vehicles:
                cv2.rectangle(ann, (int(v.x1), int(v.y1)), (int(v.x2), int(v.y2)), (0, 200, 0), 1)
            for det in result.plates:
                b = det.box
                cv2.rectangle(ann, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), (0, 0, 255), 2)
                tc = pipeline.tracks.tracks.get(det.track_id) if hasattr(pipeline.tracks, "tracks") else None
                verdict = tc.verdict if tc is not None else None
                if verdict is not None and verdict.text:
                    colour = (0, 255, 0) if verdict.confirmed else (0, 200, 255)
                    label = verdict.pretty or verdict.text
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                    x, y = int(b.x1), max(th + 6, int(b.y1) - 6)
                    cv2.rectangle(ann, (x - 2, y - th - 6), (x + tw + 4, y + 4), (0, 0, 0), -1)
                    cv2.putText(ann, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
            cv2.putText(ann, f"t={pts:5.1f}s  confirmed {sum(1 for t in pipeline.tracks.all_tracks() if t.verdict.confirmed)}",
                        (10, height - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            writer.write(ann)
        if pts - last_saved >= 3.0:
            ann = frame.copy()
            for v in result.vehicles:
                cv2.rectangle(ann, (int(v.x1), int(v.y1)), (int(v.x2), int(v.y2)), (0, 200, 0), 1)
            for det in result.plates:
                b = det.box
                col = (0, 0, 255) if det.source != "prior" else (0, 160, 255)
                cv2.rectangle(ann, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), col, 1)
            for ev in result.events:
                if ev.text:
                    cv2.putText(ann, f"{ev.text}{' *' if ev.confirmed else ''}", (8, 20 + 18 * (len(events) % 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            cv2.imwrite(str(frames_dir / f"t{pts:06.1f}.jpg"), ann, [cv2.IMWRITE_JPEG_QUALITY, 85])
            last_saved = pts
    cap.release()
    if writer is not None:
        writer.release()
    elapsed = time.time() - t0

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
            "runners_up": [t for t, _ in (v.runners_up or [])][:3],
        })
    tracks.sort(key=lambda t: (-int(t["confirmed"]), -(t["best_plate_width_px"] or 0)))

    # Sheet: best crop per track that produced any text.
    tiles = []
    for t in tracks:
        if not t["text"]:
            continue
        crop = pipeline.best_crop(t["track"])
        if crop is None or crop.size == 0:
            continue
        h, w = crop.shape[:2]
        s = min(320 / w, 90 / h)
        im = cv2.resize(crop, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_CUBIC)
        tile = np.full((120, 330, 3), 255, np.uint8)
        tile[:im.shape[0], :im.shape[1]] = im
        status = "CONFIRMED" if t["confirmed"] else f"score {t['score']:.2f}"
        cv2.putText(tile, f"{t['text']}  {status}", (2, 104), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 140, 0) if t["confirmed"] else (0, 0, 200), 1)
        cv2.putText(tile, f"track {t['track']}  best {t['best_plate_width_px']} px  obs {t['observations']}",
                    (2, 117), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 90, 90), 1)
        tiles.append(tile)
    if tiles:
        cols = 3
        rows = (len(tiles) + cols - 1) // cols
        sheet = np.full((rows * 122, cols * 332, 3), 255, np.uint8)
        for i, tile in enumerate(tiles):
            r, c = divmod(i, cols)
            sheet[r * 122:r * 122 + 120, c * 332:c * 332 + 330] = tile
        cv2.imwrite(str(sheet_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])

    widths.sort()
    def pct(p):
        return round(widths[min(len(widths) - 1, int(p * len(widths)))], 1) if widths else None
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "video": args.video,
        "size": [width, height], "fps": round(fps, 2), "frames": total, "processed": processed, "stride": args.stride,
        "processing_fps": round(processed / elapsed, 2) if elapsed else None,
        "config": {"engines": list(cfg.ocr.engines), "min_plate_width": cfg.ocr.min_plate_width,
                   "plate_model": cfg.detect.plate_model, "plate_model_frame": cfg.detect.plate_model_frame},
        "totals": dict(counts),
        "plate_width_px": {"p50": pct(0.5), "p90": pct(0.9), "max": pct(1.0), "n": len(widths)},
        "tracks_total": len(tracks), "tracks_with_plate_box": sum(1 for t in tracks if t["crops"]),
        "tracks_read": sum(1 for t in tracks if t["text"]), "tracks_confirmed": sum(1 for t in tracks if t["confirmed"]),
        "confirmed_plates": [t["text"] for t in tracks if t["confirmed"]],
        "tracks": tracks, "events": events, "readability": pipeline.readability.describe(),
        "sheet": str(sheet_path) if tiles else None, "frames_dir": str(frames_dir),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 78)
    print(f"VIDEO TEST  {args.video}  {width}x{height} {fps:.1f}fps  {total} frames, processed {processed} "
          f"(stride {args.stride}) at {report['processing_fps']} fps")
    print("=" * 78)
    print(f"vehicle detections {counts['vehicles']}  plate boxes {counts['plate_boxes']} (attached {counts['attached']}, "
          f"orphaned {counts['orphaned']})  prior bands {counts['prior_bands']}")
    print(f"plate width p50 {pct(0.5)}  p90 {pct(0.9)}  max {pct(1.0)}")
    print(f"tracks {len(tracks)}  with plate {report['tracks_with_plate_box']}  read {report['tracks_read']}  "
          f"CONFIRMED {report['tracks_confirmed']}: {report['confirmed_plates']}")
    for t in [t for t in tracks if t["text"]]:
        print(f"  track {t['track']:<5} obs {t['observations']:<3} best {str(t['best_plate_width_px']):>6} px  "
              f"{t['text']:<12} score {t['score']:.2f} margin {t['margin']:.2f} "
              f"{'CONFIRMED' if t['confirmed'] else ''}  runners-up {t['runners_up']}")
    print(f"\nWritten: {out}" + (f"\nSheet: {sheet_path}" if tiles else "") + f"\nFrames: {frames_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
