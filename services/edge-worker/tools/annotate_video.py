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



def verify(a) -> int:
    """Two passes, for checking the engine by eye rather than watching it work.

    The engine settles a vehicle's reading when its track closes - after the vehicle has left - so
    a live overlay can only show a reading where the vehicle used to be. Here the whole clip is read
    first, exactly as the worker reads it, and then drawn again: every vehicle carries the reading
    the engine settled for it for as long as it is on screen, beside the crop the engine itself
    ranked best, so each reading can be checked against the plate in the frame.
    """
    from bisect import bisect_right
    from collections import Counter
    from anpr.sampling import AdaptiveSampler
    from anpr.track.crop_bank import CropBankStore
    from app.anpr_engine import sampler_settings

    key = lambda t: str(t).split("_")[-1]
    cap = cv2.VideoCapture(a.clip)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    engine = AnprEngine(camera_id=a.camera or Path(a.clip).stem)
    pipe = engine._pipeline
    sampler = AdaptiveSampler(**sampler_settings(engine.profile, a.stride))
    veh, plt, cls, best, score = {}, {}, {}, {}, {}   # score: each track's highest detection confidence

    from anpr.pipeline import ANPRPipeline
    orig_add, orig_reads = CropBankStore.add_crop, ANPRPipeline._crop_reads
    owner, evidence = {}, {}    # id(crop) -> (track, crop); track -> {text read off one crop: (conf, width, image)}

    def reads(self, crops):                               # every crop's own reading, kept per track and string
        res = orig_reads(self, crops)
        for r in res:
            crop, text, conf = r[0], r[1], float(r[2])
            o = owner.get(id(crop))
            if o is None or not text:
                continue
            width = float(crop.box_frame[2] - crop.box_frame[0])
            cur = evidence.setdefault(o[0], {}).get(text)
            if cur is None or conf * min(width, 140) > cur[0] * min(cur[1], 140):
                evidence[o[0]][text] = (conf, width, crop.image.copy())
        return res

    def add_crop(self, track_id, *args, **kw):          # keep the crop the bank ranks best, per track
        out = orig_add(self, track_id, *args, **kw)
        owner[id(out)] = (key(track_id), out)             # holding the crop keeps its id unique for the run
        bank = self.banks.get(track_id)
        top = bank.best if bank is not None else None
        if top is not None and best.get(key(track_id), (None,))[0] is not top:
            best[key(track_id)] = (top, top.image.copy())
        return out

    import pickle
    cached = Path(a.cache) if a.cache else None
    idx, started = 0, time.perf_counter()
    if cached and cached.exists():
        veh, plt, cls, records, evidence, best, idx, score = pickle.load(open(cached, "rb"))
        cap.release()
    else:
        records = None
    CropBankStore.add_crop, ANPRPipeline._crop_reads = add_crop, reads
    try:
        while records is None:
            ok, frame = cap.read()
            if not ok or (a.max_frames and idx >= a.max_frames):
                break
            if sampler.should_process(idx):
                dets, _ = engine.process(frame, captured_at=cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
                sampler.note(d.bbox_xyxy[2] - d.bbox_xyxy[0] for d in dets if d.class_name in PLATE_BEARING_CLASSES)
                for t, b, c, sc in pipe.last_vehicles:
                    veh.setdefault(key(t), []).append((idx, tuple(float(v) for v in b)))
                    cls.setdefault(key(t), Counter())[c] += 1
                    score[key(t)] = max(score.get(key(t), 0.0), float(sc))
                for t, b, _, _ in pipe.last_plates:
                    plt.setdefault(key(t), []).append((idx, tuple(float(v) for v in b)))
            idx += 1
        if records is None:
            pipe.flush()
            records = pipe.records
            best = {t: (None, img) for t, (_, img) in best.items()}
            if cached:
                pickle.dump((veh, plt, cls, records, evidence, best, idx, score), open(cached, "wb"))
    finally:
        CropBankStore.add_crop, ANPRPipeline._crop_reads = orig_add, orig_reads
        cap.release()
    frames, analysed = idx, time.perf_counter() - started

    shown = {}
    for rec in records:
        prior, conf = rec.get("grammar_prior"), float(rec.get("confidence") or 0)
        if (rec.get("status") in STATUS and rec.get("plate") and (prior is None or prior >= MIN_GRAMMAR_PRIOR)
                and (rec["status"] == "CONFIRMED" or conf >= a.min_confidence)):
            shown[key(rec.get("track_id"))] = rec
    # vehicles, not tracker ids: an id switch, a double box or a re-acquired parked car is one vehicle
    from anpr.track.vehicle_count import STILL_MIN_SCORE, link_fragments, reader_groups
    majority = {t: c.most_common(1)[0][0] for t, c in cls.items()}
    joined = reader_groups(records, {t: o[0][0] for t, o in veh.items()}, fps)
    number, n_vehicles = link_fragments(veh, majority, fps=fps, same_vehicle=joined, scores=score,
                                        min_score=STILL_MIN_SCORE)
    tracks = {t: (obs[0][0], obs[-1][0]) for t, obs in veh.items() if number.get(t)}
    first_of = {}
    for t, (f0, _) in tracks.items():
        first_of[number[t]] = min(f0, first_of.get(number[t], f0))
    group_cls = {}
    for t in tracks:
        group_cls.setdefault(number[t], Counter()).update(cls[t])
    # the side panel: each plate once, from the moment its vehicle first appears
    listing = {}
    for t, rec in shown.items():
        first = tracks[t][0] if t in tracks else (veh[t][0][0] if t in veh else 0)
        cur = listing.get(rec["plate"])
        if cur is None or (rec["status"] == "CONFIRMED" and cur[1]["status"] != "CONFIRMED"):
            listing[rec["plate"]] = (min(first, cur[0]) if cur else first, rec, t)
    listing = sorted(listing.values(), key=lambda x: x[0])

    def box_at(obs, i, gap):
        """The box at frame i: observed, interpolated between observations up to `gap` frames apart,
        or held for two frames after the last one."""
        fs = [f for f, _ in obs]
        j = bisect_right(fs, i) - 1
        if j < 0:
            return None
        f0, b0 = obs[j]
        if f0 == i:
            return b0
        if j + 1 < len(obs) and obs[j + 1][0] - f0 <= gap:
            f1, b1 = obs[j + 1]
            u = (i - f0) / (f1 - f0)
            return tuple(p + (q - p) * u for p, q in zip(b0, b1))
        return b0 if i - f0 <= 2 else None

    import editdistance

    def evidence_for(t, rec):
        """The crop a reading can be checked against: one whose own read is the settled plate - this
        track's first, then its merged fragments' - else the closest read, else the bank's best crop.
        The bank's best alone can be a strip of body text or a hoarding the detector proposed."""
        group = [t] + [key(x) for x in (rec.get("merged_from") or []) if key(x) != t]
        items = [(editdistance.eval(text, rec["plate"]), g != t, -e[0] * min(e[1], 140), e[2])
                 for g in group for text, e in evidence.get(g, {}).items()]
        if items:
            dist, _, _, img = min(items, key=lambda x: x[:3])
            if dist <= 2:
                return img
        return best[t][1] if t in best else None

    views = {t: evidence_for(t, rec) for t, rec in shown.items()}

    def zoom(t, wpx, hpx):
        img = views.get(t)
        if img is None:
            return None
        h2 = max(8, min(hpx, int(round(wpx * img.shape[0] / max(img.shape[1], 1)))))
        return Image.fromarray(cv2.cvtColor(cv2.resize(img, (wpx, h2), interpolation=cv2.INTER_CUBIC), cv2.COLOR_BGR2RGB))

    base = base_canvas(a.title, a.subtitle)
    cap = cv2.VideoCapture(a.clip)
    writer, last = None, None
    for i in range(frames + int(fps * 3)):
        ok, frame = cap.read() if i < frames else (False, None)
        frame = frame if ok else last
        if frame is None:
            break
        last = frame
        fi = min(i, frames - 1)
        h, w = frame.shape[:2]
        sx, sy = VW / w, VH / h
        canvas = base.copy()
        canvas.paste(Image.fromarray(cv2.cvtColor(cv2.resize(frame, (VW, VH)), cv2.COLOR_BGR2RGB)), (0, HEAD))
        d = ImageDraw.Draw(canvas)
        to = lambda b: (b[0] * sx, HEAD + b[1] * sy, b[2] * sx, HEAD + b[3] * sy)
        placed, labels = [], []
        for t, (f_first, f_last) in tracks.items():
            if not f_first <= fi <= f_last + 2 or i >= frames:
                continue
            vb = box_at(veh[t], fi, 12)
            if vb is None:
                continue
            rec = shown.get(t)
            colour = STATUS[rec["status"]][0] if rec else VEH
            x1, y1, x2, y2 = to(vb)
            d.rectangle((x1, y1, x2, y2), outline=colour, width=3)
            tag = (group_cls[number[t]].most_common(1)[0][0] if a.no_count
                   else f"V{number[t]} {group_cls[number[t]].most_common(1)[0][0]}")
            ty = max(HEAD, y1 - 22)
            d.rectangle((x1, ty, x1 + 8 + 9 * len(tag), ty + 22), fill=(20, 30, 50))
            d.text((x1 + 4, ty + 2), tag, font=font(16, True), fill=(220, 230, 245))
            pb = box_at(plt[t], fi, 6) if t in plt else None
            if pb is not None:
                d.rectangle(to(pb), outline=PLATE, width=3)
            if rec:
                labels.append((t, rec, to(pb) if pb is not None else None, (x1, y1, x2, y2)))
        for t, rec, pb, (x1, y1, x2, y2) in labels:
            bg, fg = STATUS[rec["status"]]
            text = rec["plate"] + ("" if rec["status"] == "CONFIRMED" else "  unconfirmed")
            view = zoom(t, 180, 56)
            wl = 20 + 16 * len(text) + (192 if view else 0)
            anchor = pb or (x1, y2 - 10, x2, y2)
            lx = min(max(0, anchor[0] - 10), VW - wl - 4)
            ly = anchor[3] + 8 if anchor[3] + 74 <= FOOT else anchor[1] - 74
            for _ in range(12):   # step clear of labels already placed
                if not any(lx < qx2 and qx1 < lx + wl and ly < qy2 and qy1 < ly + 66 for qx1, qy1, qx2, qy2 in placed):
                    break
                ly -= 70
            ly = max(HEAD + 2, ly)
            placed.append((lx, ly, lx + wl, ly + 66))
            d.rounded_rectangle((lx, ly, lx + wl, ly + 66), radius=6, fill=bg)
            d.text((lx + 10, ly + 20), text, font=font(24, True), fill=fg)
            if view:
                canvas.paste(view, (int(lx + wl - 188), int(ly + 5)))
                d.rectangle((lx + wl - 188, ly + 5, lx + wl - 8, ly + 5 + view.height), outline=(255, 255, 255), width=2)
            if pb:
                d.line(((pb[0] + pb[2]) / 2, pb[3] if ly > pb[3] else pb[1], lx + 24, ly if ly > pb[3] else ly + 66),
                       fill=bg, width=3)
        # the panel: readings whose vehicle has appeared, newest first
        y = HEAD + 56
        so_far = [x for x in listing if x[0] <= fi]
        for first, rec, t in so_far[::-1][:7]:
            bg, fg = STATUS[rec["status"]]
            d.text((VW + 24, y), rec["plate"], font=font(30, True), fill=(255, 255, 255))
            d.rounded_rectangle((VW + 300, y + 4, VW + 452, y + 30), radius=6, fill=bg)
            d.text((VW + 311, y + 7), rec["status"], font=font(16, True), fill=fg)
            d.text((VW + 24, y + 38), f"first seen {first / fps:5.1f} s · {int(rec.get('frames_fused') or 0)} crops agreed · V{number.get(t) or '?'}",
                   font=font(17), fill=MUTE)
            view = zoom(t, 220, 60)
            if view:
                canvas.paste(view, (VW + 24, y + 62))
                d.rectangle((VW + 24, y + 62, VW + 244, y + 62 + view.height), outline=(255, 255, 255), width=2)
            y += 132
        seen = sum(1 for f0 in first_of.values() if f0 <= fi)
        confirmed = sum(1 for x in so_far if x[1]["status"] == "CONFIRMED")
        d.text((W - 360, 26), f"{fi / fps:6.1f} s", font=font(28, True), fill=(255, 255, 255))
        d.text((24, FOOT + 80), (f"Plates confirmed  {confirmed}      " if a.no_count else
                                 f"Vehicles counted  {seen}      Plates confirmed  {confirmed}      ")
               + f"Each vehicle: the reading the engine settled, beside the crop it was read from", font=font(22, True), fill=(255, 255, 255))
        out = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
        if writer is None:
            writer = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
        writer.write(out)
    cap.release()
    if writer is not None:
        writer.release()
    confirmed = sorted({x[1]["plate"] for x in listing if x[1]["status"] == "CONFIRMED"})
    print(f"{frames} frames analysed in {analysed:.0f} s; {n_vehicles} vehicles from {len(veh)} tracker ids; "
          f"{len(confirmed)} plates confirmed: {', '.join(confirmed) or 'none'}")
    print("candidates shown: " + ", ".join(f"{x[1]['plate']} {float(x[1].get('confidence') or 0):.2f}"
                                           for x in listing if x[1]["status"] != "CONFIRMED"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--title", default="Vigentra — live ANPR")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--camera", default=None)
    ap.add_argument("--stride", type=int, default=1, help="base stride between processed frames; the camera profile may override it, and a burst is added while a plate is in view")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--no-count", action="store_true",
                    help="--verify: show no vehicle count or numbers (for dense scenes where fragments cannot be linked reliably)")
    ap.add_argument("--cache", default=None, help="--verify: keep the engine pass in this file and reuse it")
    ap.add_argument("--verify", action="store_true",
                    help="two passes: label every vehicle with the reading the engine settled for it, while it is on screen")
    ap.add_argument("--min-confidence", type=float, default=0.10,
                    help="hide unconfirmed readings scored below this - the output report's own floor")
    a = ap.parse_args()
    if a.verify:
        return verify(a)

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
    last_plate, best_view = {}, {}   # per track: its last plate box, and (score, image) of its most plate-like view
    seen_tracks, n_records, idx, started = set(), 0, 0, time.perf_counter()
    last = None
    hold = int(fps * 4.0)

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
            tag = f"#{tid} {cls}"
            d.rectangle((x1, max(HEAD, y1 - 22), x1 + 8 + 9 * len(tag), max(HEAD, y1 - 22) + 22), fill=(20, 30, 50))
            d.text((x1 + 4, max(HEAD, y1 - 20)), tag, font=font(16, True), fill=(220, 230, 245))
        for b in plates:
            d.rectangle(box(b), outline=PLATE, width=3)

        def inset(tid, x, y, wpx=200, hpx=64):
            """The widest view of this track's plate, zoomed, at (x, y); returns its width or 0."""
            view = best_view.get(tid)
            if view is None:
                return 0
            img = view[1]
            hpx2 = min(hpx, int(round(wpx * img.shape[0] / max(img.shape[1], 1))))
            zoom = cv2.resize(img, (wpx, hpx2), interpolation=cv2.INTER_CUBIC)
            canvas.paste(Image.fromarray(cv2.cvtColor(zoom, cv2.COLOR_BGR2RGB)), (int(x), int(y)))
            d.rectangle((x, y, x + wpx, y + hpx2), outline=(255, 255, 255), width=2)
            return wpx
        placed: list[tuple[float, float, float, float]] = []
        for tid, (rec, f0) in list(labels.items()):
            # the engine revises a record in place when it merges track
            # fragments at the end of a pass; show only what still stands
            if frame_idx - f0 > hold or rec.get("status") not in STATUS or not rec.get("plate"):
                continue
            b = last_plate.get(tid) or last_box.get(tid) or rec.get("bbox")
            if not b:
                continue
            x1, _, _, y2 = box(b)
            bg, fg = STATUS[rec["status"]]
            text = f"{rec['plate']}  {float(rec.get('confidence') or 0):.2f}"
            wl = 18 + 15 * len(text) + (212 if tid in best_view else 0)   # room for the plate view beside the text
            x1, y = min(max(0, x1), VW - wl - 4), min(y2 + 6, FOOT - 70)
            # a vehicle leaving at the frame edge would stack its label on the
            # last one there; step up until it sits clear of the others
            for _ in range(10):
                if not any(x1 < qx2 and qx1 < x1 + wl and y < qy2 and qy1 < y + 34 for qx1, qy1, qx2, qy2 in placed):
                    break
                y -= 40
            placed.append((x1, y, x1 + wl, y + 66))
            d.rounded_rectangle((x1, y, x1 + wl, y + 66), radius=6, fill=bg)
            d.text((x1 + 9, y + 20), text, font=font(22, True), fill=fg)
            inset(tid, x1 + wl - 206, y + 3, 200, 60)
            bp = last_plate.get(tid)
            if bp:   # tie the reading to the plate it was read from
                px1, py1, px2, py2 = box(bp)
                d.line(((px1 + px2) / 2, py2, x1 + 30, y), fill=bg, width=3)
        standing = [r for r in panel if r.get("status") in STATUS and r.get("plate")]
        y = HEAD + 56
        for rec in standing[::-1][:7]:
            bg, fg = STATUS[rec["status"]]
            tid = str(rec.get("track_id")).split("_")[-1]
            d.text((VW + 24, y), rec["plate"], font=font(30, True), fill=(255, 255, 255))
            d.rounded_rectangle((VW + 300, y + 4, VW + 452, y + 30), radius=6, fill=bg)
            d.text((VW + 311, y + 7), rec["status"], font=font(16, True), fill=fg)
            d.text((VW + 24, y + 38), f"confidence {float(rec.get('confidence') or 0):.2f} · "
                   f"{int(rec.get('frames_fused') or 0)} frames agreed · #{tid}", font=font(17), fill=MUTE)
            inset(tid, VW + 24, y + 60, 220, 62)
            y += 132
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
            for t, b, _, pconf in pipe.last_plates:
                # the widest view of each vehicle's plate, so a reading can be checked against the
                # pixels it came from; a margin so the plate's edge is visible
                tid = str(t).split("_")[-1]
                last_plate[tid] = b
                x1, y1, x2, y2 = (int(round(v)) for v in b)
                w_, h_ = x2 - x1, y2 - y1
                # the most plate-like view, not the widest: on a bus the widest proposal can be a
                # hoarding, and a view that is not the plate defeats the point of showing it
                score = float(pconf) * min(w_, 120) ** 0.5
                if w_ > 8 and score > best_view.get(tid, (0, None))[0]:
                    mx, my = int(w_ * 0.12) + 2, int(h_ * 0.25) + 2
                    crop = frame[max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx]
                    if crop.size:
                        best_view[tid] = (score, crop.copy())
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
