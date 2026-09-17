#!/usr/bin/env python3
"""Profile every camera clip: image conditions, traffic, vehicle and plate geometry, difficulty class.

    python tools/anpr_profile_cameras.py [--tag baseline] [--clips rec_cam06 ...]

Sources:
- the clip itself: resolution, fps, bitrate, luma, contrast, sharpness, blockiness, sampled every 0.5 s;
- the independent tracker pass of tools/anpr_gt_sheets.py ($ANPR_BENCH_BANKS/gt_sheets/<camera>/tracks.json):
  vehicle density, size, direction, approach/recede, occlusion;
- the engine's plate candidates from a benchmark run (reports/anpr_benchmark/<tag>/<camera>/candidates.csv):
  plate size and skew.

Writes reports/anpr_benchmark/<tag>/CAMERA_PROFILES.md and camera_profiles.json.

The difficulty class is a diagnostic label, not a result. It comes from four penalties:
- plate size: the median of each track's widest plate candidate, against the 60 px readability floor;
- darkness;
- blur;
- compression.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

WORKER = Path(__file__).resolve().parent.parent
REPO = WORKER.parent.parent
#: *.mp4 is gitignored, so a worktree can point this at the main checkout's clips
VIDEOS = Path(os.getenv("ANPR_BENCH_VIDEOS", str(REPO / "data" / "videos" / "own")))
BENCH = Path(os.getenv("ANPR_BENCH_BANKS", str(Path.home() / "Downloads" / "ANPR_BENCH")))
OUT = WORKER / "reports" / "anpr_benchmark"
CLASSES = ("GOOD", "MEDIUM", "DIFFICULT", "VERY_DIFFICULT", "EXTREME")


def blockiness(gray: np.ndarray) -> float:
    """Mean jump across 8-px block edges divided by the mean jump inside blocks (1.0 = no blocking)."""
    g = gray.astype(np.float32)
    dx = np.abs(np.diff(g, axis=1))
    cols = np.arange(dx.shape[1])
    edge = dx[:, cols % 8 == 7].mean()
    inner = dx[:, cols % 8 != 7].mean() + 1e-6
    return float(edge / inner)


def image_stats(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    fps, n = cap.get(5), int(cap.get(7))
    w, h = int(cap.get(3)), int(cap.get(4))
    step = max(1, int(round(fps / 2)))
    luma, contrast, sharp, block, dark = [], [], [], [], []
    for i in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        luma.append(float(g.mean()))
        contrast.append(float(g.std()))
        sharp.append(float(cv2.Laplacian(g, cv2.CV_32F).var()))
        block.append(blockiness(g))
        dark.append(float((g < 40).mean()))
    cap.release()
    dur = n / fps if fps else 0
    return {
        "resolution": f"{w}x{h}", "fps": round(fps, 2), "frames": n, "duration_s": round(dur, 1),
        "bitrate_kbps": round(path.stat().st_size * 8 / 1000 / max(dur, 1e-6)),
        "bits_per_pixel_frame": round(path.stat().st_size * 8 / max(n * w * h, 1), 4),
        "luma_p10_p50_p90": [round(float(np.percentile(luma, q)), 1) for q in (10, 50, 90)],
        "contrast_std": round(float(np.median(contrast)), 1),
        "sharpness_lap": round(float(np.median(sharp)), 1),
        "blockiness": round(float(np.median(block)), 3),
        "dark_pixel_frac": round(float(np.median(dark)), 3),
        "lighting": "dark" if np.median(luma) < 50 else "dim" if np.median(luma) < 80 else "bright",
    }


def traffic_stats(cam: str, dur: float) -> dict:
    p = BENCH / "gt_sheets" / cam / "tracks.json"
    if not p.exists():
        return {}
    d = json.load(open(p))
    tracks = [t for t in d["tracks"] if t.get("obs")]
    if not tracks:
        return {"vehicle_tracks": 0}
    per_frame = Counter()
    widths, dirs, grow, cls = [], Counter(), Counter(), Counter()
    boxes_by_frame: dict[int, list] = {}
    for t in tracks:
        obs = t["obs"]
        cls[t["class"]] += 1
        widths.append(max(o[3] - o[1] for o in obs))
        for o in obs:
            per_frame[o[0]] += 1
            boxes_by_frame.setdefault(o[0], []).append(o[1:5])
        if len(obs) >= 3:
            (f0, *b0), (f1, *b1) = obs[0][:5], obs[-1][:5]
            dx = (b1[0] + b1[2] - b0[0] - b0[2]) / 2
            dy = (b1[1] + b1[3] - b0[1] - b0[3]) / 2
            if abs(dx) + abs(dy) > 20:
                dirs[("right" if dx > 0 else "left") if abs(dx) > abs(dy) else ("down" if dy > 0 else "up")] += 1
            w0, w1 = b0[2] - b0[0], b1[2] - b1[0]
            grow["approaching" if w1 > 1.15 * w0 else "receding" if w1 < 0.87 * w0 else "steady"] += 1
    occl = tot = 0
    for bs in boxes_by_frame.values():
        for i, a in enumerate(bs):
            tot += 1
            for j, b in enumerate(bs):
                if i == j:
                    continue
                ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
                iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
                if ix * iy > 0.3 * (a[2] - a[0]) * (a[3] - a[1]):
                    occl += 1
                    break
    moving = sum(grow.values()) or 1
    side = "front" if grow["approaching"] > 1.5 * grow["receding"] else \
        "rear" if grow["receding"] > 1.5 * grow["approaching"] else "both"
    return {
        "vehicle_tracks": len(tracks), "tracks_per_min": round(len(tracks) / max(dur, 1e-6) * 60, 1),
        "vehicles_per_frame_mean": round(float(np.mean(list(per_frame.values()))), 2) if per_frame else 0,
        "vehicle_width_p50_p90": [round(float(np.percentile(widths, q))) for q in (50, 90)],
        "classes": dict(cls), "direction": dict(dirs), "motion": dict(grow),
        "approach_share": round(grow["approaching"] / moving, 2), "plate_side_guess": side,
        "occlusion_rate": round(occl / max(tot, 1), 3),
    }


def plate_stats(cam: str, tag: str) -> dict:
    p = OUT / tag / cam / "candidates.csv"
    if not p.exists():
        return {}
    rows = list(csv.DictReader(open(p)))
    if not rows:
        return {"plate_candidates": 0}
    per_track: dict[str, float] = {}
    for r in rows:
        per_track[r["vehicle_id"]] = max(per_track.get(r["vehicle_id"], 0.0), float(r["width"]))
    w = np.array(list(per_track.values()))
    return {
        "plate_candidates": len(rows), "tracks_with_candidates": len(per_track),
        "track_max_plate_width_p25_p50_p90": [round(float(np.percentile(w, q))) for q in (25, 50, 90)],
        "tracks_max_plate_ge_40px": int((w >= 40).sum()), "tracks_max_plate_ge_60px": int((w >= 60).sum()),
        "plate_skew_deg_p50": round(float(np.median([abs(float(r["skew_deg"])) for r in rows])), 1),
        "plate_brightness_p50": round(float(np.median([float(r["brightness"]) for r in rows])), 1),
        "plate_sharpness_p50": round(float(np.median([float(r["sharpness_lap"]) for r in rows])), 1),
        "two_row_share": round(float(np.mean([int(r["two_row"]) for r in rows])), 2),
    }


def classify(img: dict, plates: dict) -> tuple[str, list[str]]:
    pen, why = 0.0, []
    p50 = (plates.get("track_max_plate_width_p25_p50_p90") or [0, 0, 0])[1]
    if p50 < 20:
        pen += 2.0
        why.append(f"median widest plate {p50}px < 20")
    elif p50 < 40:
        pen += 1.5
        why.append(f"median widest plate {p50}px < 40")
    elif p50 < 60:
        pen += 0.75
        why.append(f"median widest plate {p50}px < 60")
    luma = img["luma_p10_p50_p90"][1]
    if luma < 30:
        pen += 1.5
        why.append(f"very dark (luma {luma})")
    elif luma < 60:
        pen += 0.75
        why.append(f"dark (luma {luma})")
    if img["sharpness_lap"] < 30:
        pen += 0.75
        why.append(f"soft image (Laplacian var {img['sharpness_lap']})")
    if img["blockiness"] > 1.3:
        pen += 0.5
        why.append(f"visible compression blocking ({img['blockiness']})")
    if not plates.get("tracks_with_candidates"):
        pen += 1.0
        why.append("the engine banked no plate candidate")
    k = 0 if pen < 0.5 else 1 if pen < 1.25 else 2 if pen < 2.0 else 3 if pen < 3.0 else 4
    return CLASSES[k], why


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--clips", nargs="*")
    a = ap.parse_args()
    clips = a.clips or sorted(p.stem for p in VIDEOS.glob("*.mp4")
                              if not p.stem.endswith("_annotated") and p.stem != "traffic_01")
    out = []
    for clip in clips:
        cam = clip[4:] if clip.startswith("rec_") else clip
        img = image_stats(VIDEOS / f"{clip}.mp4")
        prof = {"camera": cam, "clip": f"{clip}.mp4", **img, **traffic_stats(cam, img["duration_s"]),
                **plate_stats(cam, a.tag)}
        prof["difficulty"], prof["difficulty_reasons"] = classify(img, prof)
        out.append(prof)
        print(cam, prof["difficulty"], "; ".join(prof["difficulty_reasons"]), flush=True)
    dest = OUT / a.tag
    dest.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(dest / "camera_profiles.json", "w"), indent=1)
    lines = ["# Camera profiles", "",
             "Image statistics are sampled every 0.5 s.",
             "Traffic comes from the independent tracker pass (`tools/anpr_gt_sheets.py`).",
             f"Plate sizes are the engine's candidates in the `{a.tag}` run.",
             "The difficulty class is a diagnostic label only.", "",
             "| camera | res | fps | kbps | luma p10/50/90 | lighting | sharp | block | tracks/min | veh/frame | "
             "veh w p50 | motion (appr/rec/steady) | side | occl | plate w p25/50/90 | >=40px | >=60px | skew | class |",
             "|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|---|---|---:|---|---:|---:|---:|---|"]
    for p in out:
        m = p.get("motion", {})
        lines.append(
            f"| {p['camera']} | {p['resolution']} | {p['fps']} | {p['bitrate_kbps']} | "
            f"{'/'.join(str(x) for x in p['luma_p10_p50_p90'])} | {p['lighting']} | {p['sharpness_lap']} | "
            f"{p['blockiness']} | {p.get('tracks_per_min', '-')} | {p.get('vehicles_per_frame_mean', '-')} | "
            f"{(p.get('vehicle_width_p50_p90') or ['-'])[0]} | "
            f"{m.get('approaching', 0)}/{m.get('receding', 0)}/{m.get('steady', 0)} | {p.get('plate_side_guess', '-')} | "
            f"{p.get('occlusion_rate', '-')} | "
            f"{'/'.join(str(x) for x in p.get('track_max_plate_width_p25_p50_p90', [])) or '-'} | "
            f"{p.get('tracks_max_plate_ge_40px', '-')} | {p.get('tracks_max_plate_ge_60px', '-')} | "
            f"{p.get('plate_skew_deg_p50', '-')} | **{p['difficulty']}** |")
    lines += ["", "## Why each class", ""]
    lines += [f"- **{p['camera']}** ({p['difficulty']}): {'; '.join(p['difficulty_reasons']) or 'no penalty'}" for p in out]
    (dest / "CAMERA_PROFILES.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
