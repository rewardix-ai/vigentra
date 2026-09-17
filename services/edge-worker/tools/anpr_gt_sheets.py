#!/usr/bin/env python3
"""Contact sheets for labelling vehicle-level ANPR ground truth, independent of the engine.

    python tools/anpr_gt_sheets.py rec_cam06 [rec_cam01 ...] [--device mps] [--stride 2]

For each clip:
- Track vehicles with a plain Ultralytics YOLO + ByteTrack pass (yolo11s, imgsz 1280, conf 0.2, every
  `--stride` frames). None of the engine's filters, gates or readers are used.
- For every track, pick up to 4 frames: the widest vehicle box, then the widest ones at least 0.4 s away
  from those already picked.
- Cut the vehicle out of each picked frame at native resolution.
- Plate proposals: the plate detector runs on the vehicle crop at a low confidence (0.02). A proposal only
  says where to look; the labeller reads the pixels. When no box is proposed, the middle band of the
  vehicle is shown instead.

Tiles are drawn onto sheets under $ANPR_BENCH_BANKS/gt_sheets/<camera>/ (sheet_NN.jpg). The clip's
track table goes to tracks.json there, and every plate panel's native-resolution crop to
crops/T<track>_f<frame>.png (for a closer look).

The labeller writes data/eval/anpr_gt/<camera>.csv with columns
    vehicle, tracks, plate, legibility, vehicle_type, side, notes
where:
- legibility is one of: readable, partial, unreadable, no_plate_visible, not_vehicle;
- tracks lists the sheet track ids separated by '+' (fragments of one vehicle);
- plate is the full registration when readable, otherwise the visible part with '?' for unknown glyphs.
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
#: *.mp4 is gitignored, so a worktree can point this at the main checkout's clips
VIDEOS = Path(os.getenv("ANPR_BENCH_VIDEOS", str(REPO / "data" / "videos" / "own")))
ROOT = Path(os.getenv("ANPR_BENCH_BANKS", str(Path.home() / "Downloads" / "ANPR_BENCH"))) / "gt_sheets"
VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
TILE_W, TILE_H = 900, 520


def track_clip(path: Path, device: str, stride: int) -> tuple[dict, float, tuple[int, int]]:
    from ultralytics import YOLO
    model = YOLO(str(WORKER / "models" / "yolo11s.pt"))
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(5)
    size = (int(cap.get(3)), int(cap.get(4)))
    tracks: dict[int, list] = {}
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            r = model.track(frame, persist=True, classes=list(VEHICLE_CLASSES), imgsz=1280, conf=0.2,
                            tracker="bytetrack.yaml", device=device, verbose=False)[0]
            if r.boxes is not None and r.boxes.id is not None:
                for box, tid, conf, cls in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.id.cpu().numpy(),
                                               r.boxes.conf.cpu().numpy(), r.boxes.cls.cpu().numpy()):
                    tracks.setdefault(int(tid), []).append((i, [float(v) for v in box], float(conf), int(cls)))
        i += 1
    cap.release()
    return tracks, fps, size


def pick_frames(obs: list, fps: float, k: int = 4) -> list:
    by_w = sorted(obs, key=lambda o: o[1][2] - o[1][0], reverse=True)
    out = []
    for o in by_w:
        if all(abs(o[0] - p[0]) >= 0.4 * fps for p in out):
            out.append(o)
        if len(out) == k:
            break
    return sorted(out, key=lambda o: o[0])


def fit(img: np.ndarray, w: int, h: int) -> np.ndarray:
    s = min(w / img.shape[1], h / img.shape[0])
    interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
    return cv2.resize(img, (max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))), interpolation=interp)


def paste(canvas, img, x, y):
    h, w = img.shape[:2]
    canvas[y:y + h, x:x + w] = img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--min-width", type=float, default=60.0, help="tracks narrower than this at their widest get no tile")
    a = ap.parse_args()
    os.chdir(WORKER)
    from ultralytics import YOLO
    plate_model = YOLO(str(WORKER / "models" / "plate_det_mix_n.pt"))
    for clip in a.clips:
        cam = clip[4:] if clip.startswith("rec_") else clip
        out = ROOT / cam
        out.mkdir(parents=True, exist_ok=True)
        for f in out.glob("sheet_*.jpg"):
            f.unlink()
        path = VIDEOS / f"{clip}.mp4"
        tracks, fps, size = track_clip(path, a.device, a.stride)
        table = []
        for tid, obs in sorted(tracks.items(), key=lambda kv: kv[1][0][0]):
            wmax = max(o[1][2] - o[1][0] for o in obs)
            cls = max(set(o[3] for o in obs), key=[o[3] for o in obs].count)
            table.append({"track": tid, "first": obs[0][0], "last": obs[-1][0], "n": len(obs),
                          "class": VEHICLE_CLASSES[cls], "max_w": round(wmax), "tile": wmax >= a.min_width,
                          "frames": [o[0] for o in pick_frames(obs, fps)] if wmax >= a.min_width else [],
                          "obs": [[o[0], *[round(v, 1) for v in o[1]], round(o[2], 3), o[3]] for o in obs]})
        json.dump({"clip": clip, "fps": fps, "size": size, "stride": a.stride, "tracks": table},
                  open(out / "tracks.json", "w"), indent=1)

        cap = cv2.VideoCapture(str(path))
        tiles = []
        for t in table:
            if not t["tile"]:
                continue
            obs = {o[0]: o for o in tracks[t["track"]]}
            tile = np.full((TILE_H, TILE_W, 3), 32, np.uint8)
            cv2.putText(tile, f"{cam} T{t['track']} {t['class']} f{t['first']}-{t['last']} "
                              f"({t['first'] / fps:.1f}-{t['last'] / fps:.1f}s) maxw {t['max_w']}px",
                        (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            veh_x = 8
            plates_y = 260
            px = 8
            for fi in t["frames"]:
                cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ok, frame = cap.read()
                if not ok:
                    continue
                x1, y1, x2, y2 = [int(round(v)) for v in obs[fi][1]]
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
                veh = frame[y1:y2, x1:x2]
                if veh.size == 0:
                    continue
                v = fit(veh, 215, 220)
                paste(tile, v, veh_x, 34)
                cv2.putText(tile, f"f{fi}", (veh_x, 34 + v.shape[0] + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (200, 200, 200), 1)
                veh_x += 222
                # plate proposal on the vehicle crop (upscaled like the engine does for small vehicles)
                scale = max(1.0, 640 / max(veh.shape[1], 1))
                big = cv2.resize(veh, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else veh
                r = plate_model.predict(big, imgsz=640, conf=0.02, device=a.device, verbose=False)[0]
                crop = None
                if r.boxes is not None and len(r.boxes):
                    j = int(r.boxes.conf.argmax())
                    bx1, by1, bx2, by2 = (r.boxes.xyxy[j].cpu().numpy() / scale).tolist()
                    bw, bh = bx2 - bx1, by2 - by1
                    cx1, cy1 = int(max(0, x1 + bx1 - 0.3 * bw)), int(max(0, y1 + by1 - 0.5 * bh))
                    cx2 = int(min(frame.shape[1], x1 + bx2 + 0.3 * bw))
                    cy2 = int(min(frame.shape[0], y1 + by2 + 0.5 * bh))
                    crop = frame[cy1:cy2, cx1:cx2]
                    label = f"{bw:.0f}x{bh:.0f} p{float(r.boxes.conf[j]):.2f}"
                if crop is None or crop.size == 0:
                    # where plates sit on a vehicle seen from the front or back: the middle band
                    vh, vw = veh.shape[:2]
                    crop = veh[int(vh * 0.35):int(vh * 0.85), int(vw * 0.15):int(vw * 0.85)]
                    label = "plate band (no box)"
                (out / "crops").mkdir(exist_ok=True)
                cv2.imwrite(str(out / "crops" / f"T{t['track']}_f{fi}.png"), crop)
                c = fit(crop, 212, 230)
                paste(tile, c, px, plates_y)
                cv2.putText(tile, f"f{fi} {label}", (px, plates_y + c.shape[0] + 14), cv2.FONT_HERSHEY_SIMPLEX,
                            0.42, (120, 255, 120), 1)
                px += 222
            tiles.append(tile)
        cap.release()
        per = 4
        for s in range(0, len(tiles), per):
            chunk = tiles[s:s + per]
            sheet = np.full((TILE_H * 2, TILE_W * 2, 3), 0, np.uint8)
            for k, tl in enumerate(chunk):
                paste(sheet, tl, (k % 2) * TILE_W, (k // 2) * TILE_H)
            cv2.imwrite(str(out / f"sheet_{s // per:02d}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
        n_small = sum(1 for t in table if not t["tile"])
        print(f"{clip}: {len(table)} tracks, {len(tiles)} tiles, {n_small} narrower than {a.min_width:.0f}px, "
              f"{(len(tiles) + per - 1) // per} sheets -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
