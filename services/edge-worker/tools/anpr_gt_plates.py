#!/usr/bin/env python3
"""One compact labelling sheet per clip: only the plate crops a person could possibly read.

    python tools/anpr_gt_plates.py cam06 [cam01 ...] [--min-plate 22]

Uses the independent tracker pass of tools/anpr_gt_sheets.py
($ANPR_BENCH_BANKS/gt_sheets/<camera>/tracks.json). For every track:
- take its 4 widest vehicle observations;
- run the plate detector on each vehicle crop (conf 0.05);
- keep the best proposal if it is at least --min-plate px wide.

A narrower plate (10 glyphs in under ~22 px) cannot be read by anyone. Such a track counts as
unreadable without being shown.

Writes $ANPR_BENCH_BANKS/gt_plates/<camera>.jpg (crops scaled to 64 px high, labelled T<track> f<frame>
<w>px) and <camera>.json (the tracks shown, and the counts of the rest).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

WORKER = Path(__file__).resolve().parent.parent
REPO = WORKER.parent.parent
VIDEOS = Path(os.getenv("ANPR_BENCH_VIDEOS", str(REPO / "data" / "videos" / "own")))
BENCH = Path(os.getenv("ANPR_BENCH_BANKS", str(Path.home() / "Downloads" / "ANPR_BENCH")))
ROW_H, SHEET_W = 64, 1500


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cameras", nargs="+")
    ap.add_argument("--min-plate", type=float, default=22.0)
    ap.add_argument("--device", default="mps")
    a = ap.parse_args()
    os.chdir(WORKER)
    from ultralytics import YOLO
    model = YOLO(str(WORKER / "models" / "plate_det_mix_n.pt"))
    out_dir = BENCH / "gt_plates"
    out_dir.mkdir(parents=True, exist_ok=True)
    for cam in a.cameras:
        d = json.load(open(BENCH / "gt_sheets" / cam / "tracks.json"))
        cap = cv2.VideoCapture(str(VIDEOS / d["clip"]) if d["clip"].endswith(".mp4") else str(VIDEOS / f"{d['clip']}.mp4"))
        shown, tiles = [], []
        for t in d["tracks"]:
            obs = sorted(t["obs"], key=lambda o: o[3] - o[1], reverse=True)[:4]
            best = []
            for o in sorted(obs, key=lambda o: o[0]):
                fi, x1, y1, x2, y2 = o[0], *[int(round(v)) for v in o[1:5]]
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ok, frame = cap.read()
                if not ok:
                    continue
                x1, y1 = max(0, x1), max(0, y1)
                veh = frame[y1:y2, x1:x2]
                if veh.size == 0 or veh.shape[1] < 30:
                    continue
                s = max(1.0, 640 / veh.shape[1])
                big = cv2.resize(veh, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s > 1 else veh
                r = model.predict(big, imgsz=640, conf=0.05, device=a.device, verbose=False)[0]
                if r.boxes is None or not len(r.boxes):
                    continue
                j = int(r.boxes.conf.argmax())
                bx1, by1, bx2, by2 = (r.boxes.xyxy[j].cpu().numpy() / s).tolist()
                bw, bh = bx2 - bx1, by2 - by1
                if bw < a.min_plate:
                    continue
                cx1, cy1 = int(max(0, x1 + bx1 - 0.15 * bw)), int(max(0, y1 + by1 - 0.25 * bh))
                cx2, cy2 = int(min(frame.shape[1], x1 + bx2 + 0.15 * bw)), int(min(frame.shape[0], y1 + by2 + 0.25 * bh))
                crop = frame[cy1:cy2, cx1:cx2]
                if crop.size:
                    best.append((bw, fi, crop, float(r.boxes.conf[j])))
            if not best:
                continue
            best = sorted(best, key=lambda b: -b[0])[:2]
            shown.append({"track": t["track"], "class": t["class"], "first": t["first"], "last": t["last"],
                          "plates": [[fi, round(bw), round(c, 2)] for bw, fi, _, c in best]})
            for bw, fi, crop, c in best:
                sc = ROW_H / crop.shape[0]
                img = cv2.resize(crop, (max(1, int(crop.shape[1] * sc)), ROW_H), interpolation=cv2.INTER_CUBIC)
                lab = np.full((16, max(img.shape[1], 130), 3), 0, np.uint8)
                cv2.putText(lab, f"T{t['track']} f{fi} {bw:.0f}px", (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                            (0, 255, 0), 1)
                body = np.zeros((ROW_H, lab.shape[1], 3), np.uint8)
                body[:, :img.shape[1]] = img[:, :lab.shape[1]]
                tiles.append(np.vstack([lab, body]))
        cap.release()
        rows, row, w = [], [], 0
        for tl in tiles:
            if w + tl.shape[1] + 6 > SHEET_W and row:
                rows.append(row)
                row, w = [], 0
            row.append(tl)
            w += tl.shape[1] + 6
        if row:
            rows.append(row)
        if rows:
            lines = []
            for r_ in rows:
                line = np.zeros((ROW_H + 16 + 6, SHEET_W, 3), np.uint8)
                x = 0
                for tl in r_:
                    line[:tl.shape[0], x:x + tl.shape[1]] = tl
                    x += tl.shape[1] + 6
                lines.append(line)
            cv2.imwrite(str(out_dir / f"{cam}.jpg"), np.vstack(lines), [cv2.IMWRITE_JPEG_QUALITY, 95])
        json.dump({"camera": cam, "min_plate_px": a.min_plate, "tracks_total": len(d["tracks"]),
                   "tracks_shown": len(shown), "shown": shown}, open(out_dir / f"{cam}.json", "w"), indent=1)
        print(f"{cam}: {len(shown)} of {len(d['tracks'])} tracks have a plate >= {a.min_plate:.0f}px, "
              f"{len(tiles)} crops, {len(rows)} rows", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
