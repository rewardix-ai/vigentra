#!/usr/bin/env python3
"""Export a benchmark run's plate observations as a per-vehicle dataset for training and regression.

    python tools/anpr_export_dataset.py --tag opt3 [--per-vehicle 8] [--out DIR]

For every vehicle track that banked a plate, writes

    <out>/<camera>/vehicle_<track>/<frame>.png     the plate crops, best first, at native resolution
    <out>/<camera>/vehicle_<track>/meta.json       the vehicle, its observations and their categories

Each observation carries its frame, timestamp, box, size band (EXTREMELY_TINY … LARGE), sharpness,
brightness, contrast, blur, skew, detector score, layout and its own reading. The vehicle carries the
engine's verdict and, where the clip has ground truth, the plate a person read and whether the engine
got it right.

The crops come from the run's own banks ($ANPR_BENCH_BANKS/<tag>/<camera>/*.pkl), so the export is of
exactly what the pipeline saw.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import sys
from pathlib import Path

import cv2

WORKER = Path(__file__).resolve().parent.parent
REPO = WORKER.parent.parent
BANKS = Path(os.getenv("ANPR_BENCH_BANKS", str(Path.home() / "Downloads" / "ANPR_BENCH")))
OUT = WORKER / "reports" / "anpr_benchmark"
GT_DIR = REPO / "data" / "eval" / "anpr_gt"
SIZE_EDGES = ((20, "EXTREMELY_TINY"), (40, "VERY_SMALL"), (60, "SMALL"), (120, "MEDIUM"))


def band(w: float) -> str:
    for edge, name in SIZE_EDGES:
        if w < edge:
            return name
    return "LARGE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--per-vehicle", type=int, default=8)
    ap.add_argument("--out", default=str(BANKS / "dataset"))
    a = ap.parse_args()
    sys.path.insert(0, str(WORKER))
    root = OUT / a.tag
    out_root = Path(a.out) / a.tag
    n_veh = n_obs = 0
    for cam_dir in sorted(d for d in root.iterdir() if (d / "tracks.csv").exists()):
        cam = cam_dir.name
        bank_dir = BANKS / a.tag / cam
        if not bank_dir.is_dir():
            continue
        tracks = {t["vehicle_id"]: t for t in csv.DictReader(open(cam_dir / "tracks.csv"))}
        cands: dict[str, list] = {}
        cfile = cam_dir / "candidates.csv"
        if cfile.exists():
            for c in csv.DictReader(open(cfile)):
                cands.setdefault(c["vehicle_id"], []).append(c)
        gt = {}
        gp = GT_DIR / f"{cam}.csv"
        if gp.exists():
            gt = {r["plate"]: r for r in csv.DictReader(open(gp)) if r["plate"]}
        for f in sorted(bank_dir.glob("[0-9]*_*.pkl")):
            b = pickle.load(open(f, "rb"))
            if not b.crops:
                continue
            vid = f"{cam}_{b.track_id}"
            rec = tracks.get(vid, {})
            crops = sorted(b.crops, key=lambda c: -(c.quality.quality_score * (0.4 + 0.6 * min(max(c.det_conf, 0), 1))))
            crops = crops[:a.per_vehicle]
            d = out_root / cam / f"vehicle_{b.track_id}"
            d.mkdir(parents=True, exist_ok=True)
            by_frame = {c["engine_frame"]: c for c in cands.get(vid, [])}
            obs = []
            for c in crops:
                name = f"{c.frame_idx:06d}.png"
                cv2.imwrite(str(d / name), c.image)
                meta = by_frame.get(str(c.frame_idx), {})
                obs.append({
                    "file": name, "engine_frame": c.frame_idx, "clip_frame": meta.get("frame"),
                    "timestamp_s": meta.get("timestamp_s"), "box": [round(float(v), 1) for v in c.box_frame],
                    "width_px": round(c.quality.width_px, 1), "height_px": round(c.quality.height_px, 1),
                    "size_band": band(c.quality.width_px), "aspect": meta.get("aspect"),
                    "detector_confidence": round(float(c.det_conf), 3), "layout": "two_row" if c.two_row else "one_row",
                    "sharpness_lap": round(c.quality.sharpness_lap, 1), "contrast": round(c.quality.local_contrast, 1),
                    "brightness": meta.get("brightness"), "blur_extent": round(c.quality.blur_extent, 2),
                    "skew_deg": round(c.quality.skew_deg, 1), "dark_frac": round(c.quality.dark_frac, 3),
                    "bloom_frac": round(c.quality.bloom_frac, 3), "quality": round(c.quality.quality_score, 3),
                    "reading": meta.get("ocr_text") or "", "reading_confidence": meta.get("ocr_conf"),
                })
            plate = rec.get("OCR_result") or ""
            truth = gt.get(plate)
            json.dump({
                "camera": cam, "vehicle": b.track_id, "vehicle_type": b.vehicle_type,
                "first_frame": rec.get("first_frame"), "last_frame": rec.get("last_frame"),
                "plate_candidates": len(b.crops), "observations_exported": len(obs),
                "engine_plate": plate, "engine_status": rec.get("status"),
                "engine_reason": rec.get("reason") or "", "engine_confidence": rec.get("OCR_confidence"),
                "ground_truth_plate": (truth or {}).get("plate", ""),
                "ground_truth_legibility": (truth or {}).get("legibility", ""),
                "correct": bool(truth and truth.get("legibility") == "readable" and rec.get("status") == "CONFIRMED"),
                "observations": obs,
            }, open(d / "meta.json", "w"), indent=1)
            n_veh += 1
            n_obs += len(obs)
    print(f"{n_veh} vehicles, {n_obs} observations -> {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
