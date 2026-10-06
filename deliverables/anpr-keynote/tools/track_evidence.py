#!/usr/bin/env python3
"""Real detection and tracking output for the evidence vehicle, from the deployed models.

    /Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/track_evidence.py

Runs the edge worker's own models on CAM06's 1080p recording around the evidence plate's track
(frames 1251-1323): YOLO11s + ByteTrack (config/bytetrack.yaml, the deployed settings) for the
vehicle, then the deployed plate detector (models/plate_det_mix_n.pt) inside the vehicle box at
640 px, as anpr/detect/plate.py does. Writes per-frame boxes and crops for the keynote's tracking,
detection and multi-frame scenes, and appends K.TRACK to js/data/evidence.js. Run build_assets.py
first.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
from ultralytics import YOLO

HERE = Path(__file__).resolve().parents[1]
EW = Path("/Users/uchit/Vigentra/vigentra/services/edge-worker")
CLIP = Path("/Users/uchit/Vigentra/vigentra/data/videos/own/cam06_1080p.mp4")
IMG = HERE / "assets/img"
FIRST, LAST, BEST = 1190, 1335, 1299          # the evidence track is 1251-1323; the replay starts earlier
VEHICLE_CLASSES = [2, 3, 5, 7]                 # car, motorcycle, bus, truck (THR: vehicle classes)
EVIDENCE_PLATE = (549.6, 802.0)                # centre of the evidence plate box at frame 1299 (px)


def main() -> int:
    ev_js = (HERE / "js/data/evidence.js").read_text()
    vehicles = YOLO(str(EW / "models/yolo11s.pt"))
    plates = YOLO(str(EW / "models/plate_det_mix_n.pt"))
    cap = cv2.VideoCapture(str(CLIP))
    i, rows, frames, times = 0, {}, {}, {}
    while i <= LAST:
        ok, frame = cap.read()
        if not ok:
            break
        if i >= FIRST:
            times[i] = round(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000, 3)  # the frame's own timestamp
            res = vehicles.track(frame, persist=True, tracker=str(EW / "config/bytetrack.yaml"), classes=VEHICLE_CLASSES,
                                 conf=0.15, imgsz=1920, device="mps", verbose=False)[0]
            boxes = []
            if res.boxes is not None and res.boxes.id is not None:
                for xyxy, tid, conf, cls in zip(res.boxes.xyxy.tolist(), res.boxes.id.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist()):
                    boxes.append({"id": int(tid), "box": [round(v, 1) for v in xyxy], "conf": round(conf, 3), "cls": res.names[int(cls)]})
            rows[i] = boxes
            frames[i] = frame
        i += 1
    cap.release()
    # the evidence vehicle: the track whose box holds the evidence plate at the best frame
    target = next(b["id"] for b in rows[BEST] if b["box"][0] <= EVIDENCE_PLATE[0] <= b["box"][2] and b["box"][1] <= EVIDENCE_PLATE[1] <= b["box"][3])
    h, w = frames[BEST].shape[:2]
    seq = []
    for f in sorted(rows):
        veh = next((b for b in rows[f] if b["id"] == target), None)
        if veh is None:
            continue
        x1, y1, x2, y2 = (int(v) for v in veh["box"])
        car = frames[f][max(0, y1):y2, max(0, x1):x2]
        found = plates.predict(car, imgsz=640, conf=0.2, device="mps", verbose=False)[0]
        plate = None
        if found.boxes is not None and len(found.boxes):
            k = int(found.boxes.conf.argmax())
            px1, py1, px2, py2 = found.boxes.xyxy[k].tolist()
            plate = {"box": [round(x1 + px1, 1), round(y1 + py1, 1), round(x1 + px2, 1), round(y1 + py2, 1)],
                     "conf": round(float(found.boxes.conf[k]), 3), "width_px": int(px2 - px1)}
        seq.append({"frame": f, "vehicle": veh, "plate": plate})
    keep = sorted({s["frame"] for s in seq if s["plate"]} )
    picks = [f for j, f in enumerate(keep) if j % max(1, len(keep) // 8) == 0] + [BEST]
    picks = sorted(set(p for p in picks if p in keep))
    for s in seq:
        if s["frame"] in picks and s["plate"]:
            x1, y1, x2, y2 = (int(v) for v in s["plate"]["box"])
            mx, my = int((x2 - x1) * 0.25), int((y2 - y1) * 0.5)
            cv2.imwrite(str(IMG / f"plate_{s['frame']}.png"), frames[s["frame"]][max(0, y1 - my):y2 + my, max(0, x1 - mx):x2 + mx])
            s["plate"]["file"] = f"assets/img/plate_{s['frame']}.png"
    for f in (picks[0], BEST, picks[-1]):
        cv2.imwrite(str(IMG / f"frame_{f}.jpg"), cv2.resize(frames[f], (1280, 720)), [cv2.IMWRITE_JPEG_QUALITY, 86])
    norm = lambda b: [round(b[0] / w, 4), round(b[1] / h, 4), round((b[2] - b[0]) / w, 4), round((b[3] - b[1]) / h, 4)]
    track = {
        "track_id": target, "frames": [seq[0]["frame"], seq[-1]["frame"]], "seen": len(seq),
        "with_plate": len(keep), "picks": picks, "full_frames": [picks[0], BEST, picks[-1]],
        "sequence": [{"frame": s["frame"], "t": times[s["frame"]], "vehicle": norm(s["vehicle"]["box"]), "vehicle_conf": s["vehicle"]["conf"],
                      "class": s["vehicle"]["cls"],
                      "plate": norm(s["plate"]["box"]) if s["plate"] else None,
                      "plate_conf": s["plate"]["conf"] if s["plate"] else None,
                      "plate_px": s["plate"]["width_px"] if s["plate"] else None,
                      "file": s["plate"].get("file") if s["plate"] else None} for s in seq],
        "others_at_best": [{"id": b["id"], "class": b["cls"], "conf": b["conf"], "box": norm(b["box"])} for b in rows[BEST]],
        # every vehicle the deployed models saw, frame by frame, for the replay over the playing video:
        # [track id, class, confidence, x, y, w, h] with the box as fractions of the frame
        "replay": [{"f": f, "t": times[f], "boxes": [[b["id"], b["cls"], b["conf"], *norm(b["box"])] for b in rows[f]]} for f in sorted(rows)],
        "models": "yolo11s.pt + ByteTrack (config/bytetrack.yaml), conf 0.15, imgsz 1920; plate_det_mix_n.pt at 640, conf 0.2",
    }
    marker = "\nK.TRACK = "
    if marker in ev_js:
        ev_js = ev_js[: ev_js.index(marker)]
    (HERE / "js/data/evidence.js").write_text(ev_js.rstrip() + f"\nK.TRACK = {json.dumps(track, indent=1)};\n")
    print("track", target, "frames", track["frames"], "seen", track["seen"], "with plate", track["with_plate"])
    print("picks", [(s["frame"], s["plate_px"], s["plate_conf"]) for s in track["sequence"] if s["frame"] in picks])
    print("at best frame", [(b["id"], b["class"], b["conf"]) for b in track["others_at_best"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
