#!/usr/bin/env python3
"""Vehicle-level ANPR benchmark: run the engine over recorded clips and measure it.

    python tools/anpr_benchmark.py run --tag baseline --device mps [--clips rec_cam06 delhi_1080p ...]
    python tools/anpr_benchmark.py report --tag baseline [--compare other_tag]

`run` drives the engine exactly as `app/worker.py` does on a clip:
- every frame is decoded;
- `AdaptiveSampler` picks the frames to process;
- `FrameQualityRouter` classifies each frame and enhances it if dark;
- `AnprEngine.process` runs on it, and `sampler.note` receives the widths of plate-bearing vehicles;
- `AnprEngine.finish` settles the pass.

By default the whole clip is processed at stride 5 (the worker's own default). The deployed compose pass is
`--stride 20 --max-frames 25`: one pass sees at most 25 processed frames.

The benchmark changes nothing in the engine. It observes by wrapping four points:
- `CropBankStore.add_crop`: every plate candidate, with its metadata;
- `ANPRPipeline._crop_reads`: each candidate's own reading;
- `CRNNReader.probs`: OCR calls and images;
- a 1 s sampler: CPU, RAM and GPU memory.

Per clip it writes `<out>/<tag>/<camera>/`:
- `summary.json`
- `tracks.csv`: one row per vehicle track;
- `candidates.csv`: one row per plate candidate;
- `vehicles.log`: the per-vehicle log;
- `records.json`

Crop banks go to `--banks-dir` (images; not committed).

`report` scores each clip against `data/eval/anpr_gt/<camera>.csv`, when one exists, and writes `<out>/<tag>/REPORT.md`.

Ground truth is used only by `report`, never by `run`.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent
REPO = WORKER.parent.parent
#: *.mp4 is gitignored, so a worktree can point this at the main checkout's clips
VIDEOS = Path(os.getenv("ANPR_BENCH_VIDEOS", str(REPO / "data" / "videos" / "own")))
GT_DIR = REPO / "data" / "eval" / "anpr_gt"
OUT = WORKER / "reports" / "anpr_benchmark"
BANKS = Path(os.getenv("ANPR_BENCH_BANKS", str(Path.home() / "Downloads" / "ANPR_BENCH")))

#: Plate width bands, in pixels, anchored on the physical readability floor in tools/_corpus.py
#: (10 glyphs x 6 px = 60 px). Fixed on purpose: a regression comparison needs the same edges every run.
SIZE_EDGES = ((20, "EXTREMELY_TINY"), (40, "VERY_SMALL"), (60, "SMALL"), (120, "MEDIUM"))


def size_band(width_px: float) -> str:
    for edge, name in SIZE_EDGES:
        if width_px < edge:
            return name
    return "LARGE"


def default_clips() -> list[str]:
    names = sorted(p.stem for p in VIDEOS.glob("*.mp4"))
    # rendered outputs and the byte-identical copy of the Delhi clip are not inputs
    return [n for n in names if not n.endswith("_annotated") and n != "traffic_01"]


def camera_of(clip: str) -> str:
    return clip[4:] if clip.startswith("rec_") else clip


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def run_clip(clip: str, out_root: Path, banks_root: Path, device: str, stride: int,
             max_frames: int | None = None) -> dict:
    import cv2
    import numpy as np
    import psutil

    from anpr.pipeline import ANPRPipeline
    from anpr.read.crnn import CRNNReader
    from anpr.sampling import AdaptiveSampler
    from anpr.track.crop_bank import CropBankStore
    from app.anpr_engine import AnprEngine, _track_number, router_settings, sampler_settings
    from app.frame_quality import FrameQualityRouter
    from app.plates import PLATE_BEARING_CLASSES
    from app.worker import iter_clip_frames

    path = VIDEOS / f"{clip}.mp4"
    camera = camera_of(clip)
    out = out_root / camera
    out.mkdir(parents=True, exist_ok=True)
    bank_dir = banks_root / camera
    bank_dir.mkdir(parents=True, exist_ok=True)
    for f in bank_dir.glob("*.pkl"):
        f.unlink()

    cap = cv2.VideoCapture(str(path))
    W, H, FPS, N = int(cap.get(3)), int(cap.get(4)), float(cap.get(5)), int(cap.get(7))
    cap.release()

    ocr = Counter()
    candidates: list[dict] = []
    crop_reads: dict[int, tuple[str, float, str]] = {}
    #: the engine numbers the frames it is given (0, 1, 2 ...), not the clip's frames; this maps back
    vframe: dict[int, int] = {}

    orig_probs = CRNNReader.probs
    orig_add = CropBankStore.add_crop
    orig_reads = ANPRPipeline._crop_reads

    def probs(self, gray_batch):
        ocr["calls"] += 1
        ocr["images"] += len(gray_batch)
        return orig_probs(self, gray_batch)

    def add_crop(self, track_id, image, corners, box_frame, frame_idx, pts_ms, det_conf, two_row=False):
        crop = orig_add(self, track_id, image, corners, box_frame, frame_idx, pts_ms, det_conf, two_row)
        q = crop.quality
        g = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        x1, y1, x2, y2 = box_frame
        candidates.append({
            "cid": id(crop), "vehicle_id": f"{camera}_{track_id}", "frame": vframe.get(frame_idx, -1),
            "engine_frame": frame_idx, "timestamp_s": round(vframe.get(frame_idx, -1) / FPS, 3),
            "x1": round(x1, 1), "y1": round(y1, 1), "x2": round(x2, 1), "y2": round(y2, 1),
            "width": round(x2 - x1, 1), "height": round(y2 - y1, 1), "area": round((x2 - x1) * (y2 - y1)),
            "aspect": round((x2 - x1) / max(y2 - y1, 1e-3), 2), "band": size_band(x2 - x1),
            "det_conf": round(float(det_conf), 3), "two_row": int(bool(two_row)),
            "sharpness_lap": round(q.sharpness_lap, 1), "tenengrad": round(q.tenengrad, 1),
            "brightness": round(float(g.mean()), 1), "contrast": round(q.local_contrast, 1),
            "blur_extent": round(q.blur_extent, 2), "skew_deg": round(q.skew_deg, 1),
            "dark_frac": round(q.dark_frac, 3), "bloom_frac": round(q.bloom_frac, 3),
            "quality": round(q.quality_score, 3),
        })
        return crop

    def reads(self, crops):
        res = orig_reads(self, crops)
        for c, t, p, rs in res:
            crop_reads[id(c)] = (t, float(p), "+".join(sorted(rs)))
        return res

    CRNNReader.probs = probs
    CropBankStore.add_crop = add_crop
    ANPRPipeline._crop_reads = reads

    proc = psutil.Process()
    usage = defaultdict(list)
    stop = threading.Event()

    def monitor():
        proc.cpu_percent(None)
        while not stop.wait(1.0):
            usage["cpu"].append(proc.cpu_percent(None))
            usage["rss"].append(proc.memory_info().rss)
            if device == "mps":
                try:
                    import torch
                    usage["gpu"].append(torch.mps.driver_allocated_memory())
                except Exception:
                    pass
            elif device.startswith("cuda"):
                try:
                    import torch
                    usage["gpu"].append(torch.cuda.max_memory_allocated())
                except Exception:
                    pass

    try:
        engine = AnprEngine(camera_id=camera)
        pipe = engine._pipeline
        pipe.bank_dump_dir = bank_dir
        # as the worker does: the camera's profile may set its own sampling and low-light handling
        sampler = AdaptiveSampler(**sampler_settings(engine.profile, stride))
        router = FrameQualityRouter(**router_settings(engine.profile))
        threading.Thread(target=monitor, daemon=True).start()

        processed = skipped = veh = plates = 0
        quality = Counter()
        lat = []
        t0 = time.perf_counter()
        for frame_index, frame in iter_clip_frames(str(path), 1):
            if max_frames is not None and processed >= max_frames:
                break          # the worker's pass budget (--max-frames)
            if not sampler.should_process(frame_index):
                continue
            processed += 1
            img, assess = router.route(frame)
            quality[assess.quality.value] += 1
            if assess.inference_skipped:
                skipped += 1
                continue
            vframe[engine._frames] = frame_index
            t1 = time.perf_counter()
            dets, _ = engine.process(img, captured_at=time.time(), discontinuity=False)
            lat.append((time.perf_counter() - t1) * 1000)
            sampler.note(d.bbox_xyxy[2] - d.bbox_xyxy[0] for d in dets if d.class_name in PLATE_BEARING_CLASSES)
            veh += len(dets)
            plates += len(getattr(pipe, "last_plates", []))
        t_fin = time.perf_counter()
        sightings = engine.finish()
        finish_s = time.perf_counter() - t_fin
        wall = time.perf_counter() - t0
    finally:
        stop.set()
        CRNNReader.probs = orig_probs
        CropBankStore.add_crop = orig_add
        ANPRPipeline._crop_reads = orig_reads

    records = list(pipe.records)
    emitted = {int(s.track_id): s for s in sightings}
    for c in candidates:
        t, p, rs = crop_reads.get(c.pop("cid"), ("", 0.0, ""))
        c.update(ocr_text=t, ocr_conf=round(p, 3), ocr_readers=rs)

    by_track = defaultdict(list)
    for c in candidates:
        by_track[c["vehicle_id"]].append(c)

    rows, log = [], []
    for r in records:
        q = r.get("quality") or {}
        cands = by_track.get(r["track_id"], [])
        best = next((c for c in cands if c["engine_frame"] == r.get("best_frame")), None)
        first, last = vframe.get(r.get("first_frame"), -1), vframe.get(r.get("last_frame"), -1)
        best_frame = vframe.get(r.get("best_frame"), "") if r.get("best_frame") is not None else ""
        s = emitted.get(_track_number(r["track_id"]))
        widths = [c["width"] for c in cands]
        final = s.text if s else ""
        rows.append({
            "vehicle_id": r["track_id"], "vehicle_type": r.get("vehicle_type"),
            "first_frame": first, "last_frame": last,
            "frames_seen": r.get("n_frames_seen"), "number_of_plate_candidates": len(cands),
            "max_plate_width": max(widths, default=0), "size_band": size_band(max(widths, default=0)) if widths else "",
            "best_plate_frame": best_frame,
            "best_plate_size": f"{q.get('width_px', 0):.0f}x{q.get('height_px', 0):.0f}" if q else "",
            "best_plate_confidence": best["det_conf"] if best else "",
            "best_quality": round(float(q.get("quality_score", 0)), 3) if q else "",
            "OCR_result": r.get("plate") or "", "OCR_confidence": r.get("confidence"),
            "status": r.get("status"), "reason": r.get("reason") or "",
            "valid_format": int(bool(r.get("valid_format"))),
            "emitted": int(s is not None), "emitted_confirmed": int(bool(s and s.confirmed)),
            "final_result": final if final else ("UNREADABLE" if cands else "NO_PLATE_DETECTED"),
        })
        if not cands:
            continue
        dur = (last - first) / FPS
        read_lines = [f"  f{c['frame']:>5} {c['width']:.0f}x{c['height']:.0f} q={c['quality']:.2f} "
                      f"det={c['det_conf']:.2f} -> {c['ocr_text'] or '-'} {c['ocr_conf']:.2f}"
                      for c in sorted(cands, key=lambda c: c["frame"])[:40]]
        log.append("\n".join([
            f"[{camera.upper()}] Vehicle ID: {r['track_id']} ({r.get('vehicle_type')})",
            f"Track duration: {dur:.1f} s  frames {first}-{last}",
            f"Plate candidates: {len(cands)}  best frame: {best_frame}  plate size: "
            f"{rows[-1]['best_plate_size']}  quality: {rows[-1]['best_quality']}",
            "OCR (per candidate, first 40):", *read_lines,
            f"FINAL: {r.get('plate') or '-'}  status {r.get('status')}  confidence {r.get('confidence')}  "
            f"{r.get('reason') or ''}  emitted: {final or 'no'}",
            "",
        ]))

    def write_csv(name, data):
        with open(out / name, "w", newline="") as fh:
            keys = list(data[0].keys()) if data else ["empty"]
            w = csv.DictWriter(fh, keys)
            w.writeheader()
            w.writerows(data)

    write_csv("tracks.csv", rows)
    write_csv("candidates.csv", candidates)
    (out / "vehicles.log").write_text("\n".join(log))
    json.dump([{k: v for k, v in r.items() if not k.startswith("_")} for r in records],
              open(out / "records.json", "w"), indent=1, default=str)

    bands = Counter(r["size_band"] for r in rows if r["size_band"])
    lat_sorted = sorted(lat)
    summary = {
        "camera": camera, "clip": path.name, "resolution": f"{W}x{H}", "fps": round(FPS, 2), "frames": N,
        "duration_s": round(N / FPS, 1), "device": device, "sampler_stride": stride, "max_frames": max_frames,
        "engine": engine.describe(),
        "frames_processed": processed, "frames_skipped_quality": skipped, "frame_quality": dict(quality),
        "sampler": sampler.describe(),
        "vehicle_detections": veh,
        "unique_vehicle_tracks": len(records),
        "tracks_with_plate_candidates": sum(1 for r in rows if r["number_of_plate_candidates"]),
        "tracks_by_size_band": dict(bands),
        "plate_detections": plates,
        "plate_candidates_banked": len(candidates),
        "ocr_calls": ocr["calls"], "ocr_images": ocr["images"],
        "tracks_with_ocr_result": sum(1 for r in records if r.get("plate")),
        "unique_ocr_results": len({r["plate"] for r in records if r.get("plate")}),
        "status_counts": dict(Counter(r.get("status") for r in records)),
        "confirmed_plates": sorted({r["plate"] for r in records if r.get("status") == "CONFIRMED"}),
        "emitted": [{"plate": s.text, "confirmed": s.confirmed, "score": round(s.confidence, 3),
                     "track": s.track_id} for s in sightings],
        "unreadable_reasons": dict(Counter((r.get("reason") or "").split(":")[0] for r in records
                                           if r.get("status") == "UNREADABLE")),
        "wall_s": round(wall, 1), "finish_s": round(finish_s, 1),
        "processed_fps": round(processed / wall, 2) if wall else 0,
        "clip_seconds_per_wall_second": round((N / FPS) / wall, 3) if wall else 0,
        "frame_latency_ms_p50": round(lat_sorted[len(lat) // 2], 1) if lat else None,
        "frame_latency_ms_p95": round(lat_sorted[int(len(lat) * 0.95)], 1) if lat else None,
        "cpu_percent_mean": round(float(np.mean(usage["cpu"])), 1) if usage["cpu"] else None,
        "cpu_cores": psutil.cpu_count(),
        "rss_max_mb": round(max(usage["rss"], default=0) / 2**20),
        "gpu_mem_max_mb": round(max(usage["gpu"]) / 2**20) if usage["gpu"] else None,
    }
    json.dump(summary, open(out / "summary.json", "w"), indent=1, default=str)
    return summary


def cmd_run(a) -> int:
    os.environ["ANPR_MODELS_DIR"] = a.models_dir or str(WORKER / "models")
    os.environ["ANPR_CONFIG_DIR"] = a.config_dir or str(WORKER / "config")
    os.environ["ANPR_DEVICE"] = a.device
    os.environ.setdefault("ANPR_REVIEW_SCORE", "0.35")     # docker-compose default
    os.environ.setdefault("ANPR_EMIT_UNCONFIRMED", "false")
    sys.path.insert(0, str(WORKER))
    if a.anpr_root:
        # benchmark another copy of the anpr package (e.g. the research repo) behind the same engine wrapper
        sys.path.insert(0, a.anpr_root)
    os.chdir(WORKER)   # ultralytics resolves (and would download) weights relative to the cwd
    out_root = Path(a.out) / a.tag
    banks_root = BANKS / a.tag
    for clip in a.clips or default_clips():
        t = time.time()
        s = run_clip(clip, out_root, banks_root, a.device, a.stride, a.max_frames)
        print(f"{clip}: {s['unique_vehicle_tracks']} tracks, {s['tracks_with_plate_candidates']} with plates, "
              f"{len(s['confirmed_plates'])} confirmed, {len(s['emitted'])} emitted, "
              f"{s['processed_fps']} fps, {time.time() - t:.0f}s", flush=True)
    return 0


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def load_gt(camera: str) -> list[dict] | None:
    p = GT_DIR / f"{camera}.csv"
    if not p.exists():
        return None
    return [r for r in csv.DictReader(open(p)) if not r.get("vehicle", "").startswith("#")]


def score_clip(d: Path) -> dict:
    s = json.load(open(d / "summary.json"))
    tracks = list(csv.DictReader(open(d / "tracks.csv")))
    gt = load_gt(s["camera"])
    m = {"camera": s["camera"], "tracks": s["unique_vehicle_tracks"],
         "with_plate": s["tracks_with_plate_candidates"], "plate_dets": s["plate_detections"],
         "ocr_images": s["ocr_images"], "ocr_results": s["unique_ocr_results"],
         "confirmed": len(s["confirmed_plates"]), "emitted": len(s["emitted"]),
         "fps": s["processed_fps"], "rt": s["clip_seconds_per_wall_second"],
         "lat50": s["frame_latency_ms_p50"], "cpu": s["cpu_percent_mean"], "rss": s["rss_max_mb"],
         "gpu": s["gpu_mem_max_mb"], "bands": s["tracks_by_size_band"]}
    if gt is None:
        return m
    readable = {r["plate"] for r in gt if r["legibility"] == "readable" and r["plate"]}
    partial = [r["plate"] for r in gt if r["legibility"] != "readable" and r["plate"]]

    def unverifiable(t: str) -> bool:
        # a partly legible plate with the same glyphs wherever it is legible: neither right nor wrong
        return any(len(p) == len(t) and all(a in ("?", b) for a, b in zip(p, t)) for p in partial)

    def false(ts: set) -> set:
        return {t for t in ts - readable if not unverifiable(t)}

    confirmed = {t["OCR_result"] for t in tracks if t["status"] == "CONFIRMED"}
    emitted = {e["plate"] for e in s["emitted"]}
    shown = {t["OCR_result"] for t in tracks if t["OCR_result"] and t["valid_format"] == "1"}
    m.update(
        gt_vehicles=len(gt), gt_readable=len(readable),
        correct_confirmed=len(confirmed & readable), false_confirmed=len(false(confirmed)),
        correct_emitted=len(emitted & readable), false_emitted=len(false(emitted)),
        correct_read=len(shown & readable), false_read=len(false(shown)),
        missed=sorted(readable - confirmed),
        false_list=sorted(false(confirmed | emitted)),
        unverifiable=sorted({t for t in confirmed | emitted if t not in readable and unverifiable(t)}),
    )
    return m


def cmd_report(a) -> int:
    root = Path(a.out) / a.tag
    rows = [score_clip(d) for d in sorted(root.iterdir()) if (d / "summary.json").exists()]
    lines = [f"# ANPR benchmark `{a.tag}`", "",
             "- **correct** = readable ground-truth plates the engine confirmed (exact string);",
             "- **false** = confirmed or emitted strings that are not a readable ground-truth plate on that clip and do not",
             "  fit any partly legible one (those are counted as unverifiable);",
             "- **read** = any valid-format reading shown (confirmed or not).",
             "",
             "| camera | tracks | with plate cand. | plate dets | OCR images | GT readable | correct confirmed | "
             "false confirmed/emitted | valid reads correct/false | fps | clip s per s | CPU % | RAM MB | GPU MB |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    tot = Counter()
    for m in rows:
        g = "gt_readable" in m
        lines.append(
            f"| {m['camera']} | {m['tracks']} | {m['with_plate']} | {m['plate_dets']} | {m['ocr_images']} | "
            f"{m.get('gt_readable', '-')} | {m.get('correct_confirmed', '-')} | "
            f"{(str(m['false_confirmed']) + '/' + str(m['false_emitted'])) if g else '-'} | "
            f"{(str(m['correct_read']) + '/' + str(m['false_read'])) if g else '-'} | "
            f"{m['fps']} | {m['rt']} | {m['cpu']} | {m['rss']} | {m['gpu'] or '-'} |")
        for k in ("tracks", "with_plate", "plate_dets", "ocr_images", "gt_readable", "correct_confirmed",
                  "false_confirmed", "false_emitted", "correct_read", "false_read"):
            if isinstance(m.get(k), int):
                tot[k] += m[k]
    lines += [f"| **all** | {tot['tracks']} | {tot['with_plate']} | {tot['plate_dets']} | {tot['ocr_images']} | "
              f"{tot['gt_readable']} | {tot['correct_confirmed']} | {tot['false_confirmed']}/{tot['false_emitted']} | "
              f"{tot['correct_read']}/{tot['false_read']} | | | | | |", ""]
    lines += ["## Missed and false plates", ""]
    for m in rows:
        if m.get("missed") or m.get("false_list"):
            lines.append(f"- **{m['camera']}**: missed {', '.join(m.get('missed', [])) or '-'}; "
                         f"false {', '.join(m.get('false_list', [])) or '-'}")
    lines += ["", "## Tracks with plate candidates by best-candidate width band", "",
              "| camera | " + " | ".join(n for _, n in SIZE_EDGES) + " | LARGE |", "|---|" + "---:|" * 5]
    for m in rows:
        b = m["bands"]
        lines.append(f"| {m['camera']} | " + " | ".join(str(b.get(n, 0)) for _, n in SIZE_EDGES)
                     + f" | {b.get('LARGE', 0)} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--tag", required=True)
    r.add_argument("--clips", nargs="*")
    r.add_argument("--device", default="cpu")
    r.add_argument("--stride", type=int, default=int(os.getenv("YOLO_FRAME_SAMPLE_INTERVAL", "5")))
    r.add_argument("--max-frames", type=int, help="stop after this many processed frames (compose passes use 25)")
    r.add_argument("--out", default=str(OUT))
    r.add_argument("--anpr-root", help="directory holding an alternative anpr/ package")
    r.add_argument("--config-dir", help="thresholds.yaml / roi.yaml directory (default: config/)")
    r.add_argument("--models-dir", help="weights directory (default: models/)")
    p = sub.add_parser("report")
    p.add_argument("--tag", required=True)
    p.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    return cmd_run(a) if a.cmd == "run" else cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
