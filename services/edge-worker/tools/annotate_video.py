#!/usr/bin/env python3
"""Render the ANPR engine's output on a clip as a Vigentra-branded video.

It runs the same engine, models and thresholds the readers run
(`app.anpr_engine`), so the video shows what the platform reads: vehicle
tracks, plate boxes as they are found, and each vehicle's reading when its
track closes - CONFIRMED in green and CANDIDATE in amber, exactly as the
engine labels it. Nothing is drawn that the engine did not produce, and a
reading whose grammar the engine would drop is not shown either; nor is an
unconfirmed one below the output report's confidence floor.

    python tools/annotate_video.py CLIP OUT.mp4 --title "Delhi test clip" \
        --subtitle "1080p street footage" [--stride 1] [--max-frames 600]

Writes OUT.mp4 (MPEG-4) at the clip's own frame rate and prints a summary.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

WORKER = Path(__file__).resolve().parent.parent
os.environ.setdefault("ANPR_MODELS_DIR", str(WORKER / "models"))
os.environ.setdefault("ANPR_CONFIG_DIR", str(WORKER / "config"))
sys.path.insert(0, str(WORKER))
from app.anpr_engine import MIN_GRAMMAR_PRIOR, AnprEngine  # noqa: E402
from app.plates import PLATE_BEARING_CLASSES  # noqa: E402

LOGO = WORKER.parent.parent / "deliverables" / "presentation" / "logo-on-dark.png"
W, H, HEAD, VW, VH = 1920, 1080, 84, 1440, 810
FOOT = HEAD + VH
NAVY, NAVY2, MUTE, ICE = (14, 30, 51), (27, 47, 75), (174, 189, 209), (143, 180, 230)
VEH, PLATE = (111, 160, 224), (125, 220, 159)
STATUS = {"CONFIRMED": ((26, 127, 71), (255, 255, 255)), "CANDIDATE": ((240, 168, 48), (20, 20, 20))}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    return ImageFont.truetype(f"/System/Library/Fonts/Supplemental/{name}", size)


def base_canvas(title: str, subtitle: str) -> Image.Image:
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    d.rectangle((VW, HEAD, W, FOOT), fill=NAVY2)
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA")
        logo = logo.resize((int(logo.width * 64 / logo.height), 64))
        img.paste(logo, (20, 10), logo)
    d.text((170, 12), title, font=font(30, True), fill=(255, 255, 255))
    d.text((170, 50), subtitle, font=font(19), fill=MUTE)
    d.text((VW + 24, HEAD + 18), "PLATE READINGS", font=font(18, True), fill=ICE)
    y = FOOT + 26
    for x, label, colour in ((24, "vehicle track", VEH), (250, "plate located", PLATE)):
        d.rectangle((x, y + 3, x + 26, y + 21), outline=colour, width=3)
        d.text((x + 36, y), label, font=font(19), fill=(255, 255, 255))
    for x, status in ((470, "CONFIRMED"), (680, "CANDIDATE")):
        bg, fg = STATUS[status]
        d.rounded_rectangle((x, y, x + 150, y + 26), radius=6, fill=bg)
        d.text((x + 12, y + 3), status, font=font(17, True), fill=fg)
    d.text((24, FOOT + 136), "Vigentra ANPR engine: YOLO11 + ByteTrack vehicle tracks · plate detector · "
           "CRNN readers · Indian plate grammar · consensus vote across each track",
           font=font(17), fill=MUTE)
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--title", default="Vigentra — live ANPR")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--camera", default=None)
    ap.add_argument("--stride", type=int, default=1, help="base stride between processed frames; the camera profile may override it, and a burst is added while a plate is in view")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--min-confidence", type=float, default=0.10,
                    help="hide unconfirmed readings scored below this - the output report's own floor")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0     # a local file's own rate; grid streams are never sized this way
    engine = AnprEngine(camera_id=a.camera or Path(a.clip).stem)
    pipe = engine._pipeline
    # Sample as the worker and the benchmark do - the camera's stride plus a burst of consecutive
    # frames while a readable plate is in view - not a plain stride. On the noon clip a plain
    # stride of 2 confirmed two plates wrongly that the burst sampler reads right: the burst is
    # what gives the vote enough crops of the plate while it is legible.
    from anpr.sampling import AdaptiveSampler
    from app.anpr_engine import sampler_settings
    sampler = AdaptiveSampler(**sampler_settings(engine.profile, a.stride))
    base = base_canvas(a.title, a.subtitle)
    sx, sy = VW / 1920.0, VH / 1080.0
    writer = None
    vehicles, plates, last_box, labels, panel = [], [], {}, {}, []
    seen_tracks, n_records, idx, started = set(), 0, 0, time.perf_counter()
    last = None
    hold = int(fps * 2.5)

    def settle(frame_idx: int) -> None:
        nonlocal n_records
        for rec in pipe.records[n_records:]:
            prior = rec.get("grammar_prior")
            sure = rec.get("status") == "CONFIRMED" or float(rec.get("confidence") or 0) >= a.min_confidence
            if rec.get("status") in STATUS and rec.get("plate") and sure and (prior is None or prior >= MIN_GRAMMAR_PRIOR):
                tid = str(rec.get("track_id")).split("_")[-1]
                labels[tid] = (rec, frame_idx)
                panel.append(rec)
        n_records = len(pipe.records)

    def draw(frame, frame_idx: int) -> None:
        nonlocal writer
        h, w = frame.shape[:2]
        global_sx, global_sy = VW / w, VH / h
        canvas = base.copy()
        canvas.paste(Image.fromarray(cv2.cvtColor(cv2.resize(frame, (VW, VH)), cv2.COLOR_BGR2RGB)), (0, HEAD))
        d = ImageDraw.Draw(canvas)
        box = lambda b: (b[0] * global_sx, HEAD + b[1] * global_sy, b[2] * global_sx, HEAD + b[3] * global_sy)
        for tid, b, cls in vehicles:
            x1, y1, x2, y2 = box(b)
            d.rectangle((x1, y1, x2, y2), outline=VEH, width=3)
            d.text((x1 + 2, max(HEAD, y1 - 20)), f"#{tid} {cls}", font=font(16, True), fill=VEH)
        for b in plates:
            d.rectangle(box(b), outline=PLATE, width=3)
        placed: list[tuple[float, float, float, float]] = []
        for tid, (rec, f0) in list(labels.items()):
            # the engine revises a record in place when it merges track
            # fragments at the end of a pass; show only what still stands
            if frame_idx - f0 > hold or rec.get("status") not in STATUS or not rec.get("plate"):
                continue
            b = last_box.get(tid) or rec.get("bbox")
            if not b:
                continue
            x1, _, _, y2 = box(b)
            bg, fg = STATUS[rec["status"]]
            text = f"{rec['plate']}  {float(rec.get('confidence') or 0):.2f}"
            wl = 18 + 15 * len(text)
            x1, y = min(max(0, x1), VW - wl - 4), min(y2 + 6, FOOT - 38)
            # a vehicle leaving at the frame edge would stack its label on the
            # last one there; step up until it sits clear of the others
            for _ in range(10):
                if not any(x1 < qx2 and qx1 < x1 + wl and y < qy2 and qy1 < y + 34 for qx1, qy1, qx2, qy2 in placed):
                    break
                y -= 40
            placed.append((x1, y, x1 + wl, y + 34))
            d.rounded_rectangle((x1, y, x1 + wl, y + 34), radius=6, fill=bg)
            d.text((x1 + 9, y + 5), text, font=font(22, True), fill=fg)
        standing = [r for r in panel if r.get("status") in STATUS and r.get("plate")]
        y = HEAD + 56
        for rec in standing[::-1][:9]:
            bg, fg = STATUS[rec["status"]]
            d.text((VW + 24, y), rec["plate"], font=font(30, True), fill=(255, 255, 255))
            d.rounded_rectangle((VW + 300, y + 4, VW + 452, y + 30), radius=6, fill=bg)
            d.text((VW + 311, y + 7), rec["status"], font=font(16, True), fill=fg)
            d.text((VW + 24, y + 38), f"confidence {float(rec.get('confidence') or 0):.2f} · "
                   f"{int(rec.get('frames_fused') or 0)} frames agreed", font=font(17), fill=MUTE)
            y += 82
        confirmed = sum(1 for r in standing if r["status"] == "CONFIRMED")
        d.text((W - 360, 26), f"{frame_idx / fps:6.1f} s", font=font(28, True), fill=(255, 255, 255))
        d.text((24, FOOT + 80), f"Vehicles tracked  {len(seen_tracks)}      Plate readings  {len(standing)}      "
               f"Confirmed  {confirmed}", font=font(24, True), fill=(255, 255, 255))
        out = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
        if writer is None:
            writer = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        writer.write(out)

    while True:
        ok, frame = cap.read()
        if not ok or (a.max_frames and idx >= a.max_frames):
            break
        last = frame
        if sampler.should_process(idx):
            dets, _ = engine.process(frame, captured_at=cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            sampler.note(d.bbox_xyxy[2] - d.bbox_xyxy[0] for d in dets if d.class_name in PLATE_BEARING_CLASSES)
            vehicles = [(str(t).split("_")[-1], b, c) for t, b, c, _ in pipe.last_vehicles]
            plates = [b for _, b, _, _ in pipe.last_plates]
            for tid, b, _ in vehicles:
                last_box[tid] = b
                seen_tracks.add(tid)
            settle(idx)
        draw(frame, idx)
        idx += 1
    pipe.flush()
    settle(idx)
    vehicles, plates = [], []
    for k in range(int(fps * 3)):                 # hold the final state so late readings are seen
        draw(last if last is not None else np.zeros((1080, 1920, 3), np.uint8), idx + k)
    if writer is not None:
        writer.release()
    wall = time.perf_counter() - started
    panel = [r for r in panel if r.get("status") in STATUS and r.get("plate")]
    confirmed = [r["plate"] for r in panel if r["status"] == "CONFIRMED"]
    print(f"{idx} frames in {wall:.0f} s ({idx / max(wall, 1e-9):.2f} fps); {len(seen_tracks)} vehicles; "
          f"{len(panel)} readings; confirmed: {', '.join(confirmed) or 'none'}")
    print("readings:", ", ".join(f"{r['plate']} {float(r.get('confidence') or 0):.2f} {r['status'][:4]}" for r in panel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
