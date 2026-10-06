#!/usr/bin/env python3
"""Build the keynote's media from the project's real footage and evidence. Nothing is invented.

    /Users/uchit/Downloads/ANPR/.venv/bin/python deliverables/anpr-keynote/tools/build_assets.py

Needs cv2 and imageio-ffmpeg (both in the research venv). Writes:
  assets/video/*.mp4   short H.264 cuts of real clips (faststart, muted) the browser plays offline
  assets/img/*         real crops, frames, sheets and screenshots
  js/data/evidence.js  the measured facts behind the pictures (the evidence plate, the failures)

To swap footage, change a path in VIDEOS or IMAGES and run again. assets/ is not committed: it
holds government CCTV footage and real registration numbers.
"""
from __future__ import annotations

import glob
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

HERE = Path(__file__).resolve().parents[1]
P = Path("/Users/uchit/Vigentra/vigentra")
R = Path("/Users/uchit/Downloads/ANPR")
B = Path("/Users/uchit/Downloads/ANPR_BENCH")
S = Path("/Users/uchit/Downloads/Vigentra_Submission")
OWN = P / "data/videos/own"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
VID = HERE / "assets/video"
IMG = HERE / "assets/img"

# slot: (source, height, start s, seconds) - None keeps the whole clip
VIDEOS = {
    "cam06_noon": (OWN / "cam06_noon.mp4", 480, None, None),
    "cam06_night": (OWN / "cam06_night.mp4", 480, 20, 60),
    "cam06_1080p": (OWN / "cam06_1080p.mp4", 1080, None, None),
    "delhi_raw": (OWN / "delhi_1080p.mp4", 720, None, None),
    "cam06_vigentra": (P / "deliverables/Vigentra_CAM06_Noon_ANPR_720p.mp4", 720, None, None),
    "delhi_vigentra": (P / "deliverables/Vigentra_Delhi_ANPR_720p.mp4", 720, None, None),
    "app_demo": (P / "deliverables/Vigentra_Demo_Short.mp4", 720, None, None),
    "tfl_low": (OWN / "tfl_03.mp4", 288, None, None),
    **{f"wall_{c}": (OWN / f"rec_{c}.mp4", 360, None, 20) for c in ("cam01", "cam02", "cam04", "cam05", "cam07", "cam12")},
}

IMAGES = {
    "gt_sheet.jpg": B / "gt_sheets/cam06_noon/sheet_00.jpg",
    "verify_sheet.png": R / "out/verify_cam06/verify_sheet.png",
    "app_live_wall.jpg": S / "Screenshots/04_Live_Wall_50_Cameras_Live_Detection.jpg",
    "app_delhi_live.jpg": S / "Screenshots/05_Delhi_Feed_Live_ANPR.jpg",
    "app_cam06_live.jpg": S / "Screenshots/06_Grid_CAM06_Live_ANPR.jpg",
    "app_trace.jpg": S / "Screenshots/08_Trace_Vehicle_Route_Map.jpg",
    "app_report.jpg": S / "Screenshots/09_ANPR_Output_Report.jpg",
    "app_audit.jpg": S / "Screenshots/10_Audit_Log.jpg",
}

#: The evidence plate: a confirmed read from the research engine's evidence pack (Sep 2026),
#: on CAM06's 1080p recording - R/data/sandbox/cam06.mp4, the file own/cam06_1080p.mp4 links to.
EVIDENCE = R / "evidence/confirmed/cam06_s0_t132"
EVIDENCE_CLIP = OWN / "cam06_1080p.mp4"


def cut(slot: str, src: Path, height: int, start, seconds) -> None:
    dst = VID / f"{slot}.mp4"
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)
    print(f"video {slot}: {dst.stat().st_size / 1e6:.1f} MB")


def frames(path: Path, wanted: set[int]) -> dict[int, np.ndarray]:
    """Decode sequentially (seeking by index is unreliable on these files) and keep `wanted`."""
    cap = cv2.VideoCapture(str(path))
    got, i, last = {}, 0, max(wanted)
    while i <= last:
        ok, frame = cap.read()
        if not ok:
            break
        if i in wanted:
            got[i] = frame
        i += 1
    cap.release()
    return got


def locate(frame: np.ndarray, template: np.ndarray, near: tuple[float, float], radius: int = 260):
    """Find the plate in a frame by multi-scale template match around where it was last seen."""
    h, w = frame.shape[:2]
    x0, y0 = int(max(0, near[0] - radius)), int(max(0, near[1] - radius))
    x1, y1 = int(min(w, near[0] + radius)), int(min(h, near[1] + radius))
    search = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    tgray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    best = (-1.0, None)
    for scale in np.linspace(0.35, 1.25, 19):
        t = cv2.resize(tgray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if t.shape[0] >= search.shape[0] or t.shape[1] >= search.shape[1] or t.shape[1] < 10:
            continue
        res = cv2.matchTemplate(search, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best[0]:
            best = (score, (x0 + loc[0], y0 + loc[1], t.shape[1], t.shape[0]))
    return best


def evidence() -> dict:
    meta = json.loads(Path(f"{EVIDENCE}.json").read_text())
    for kind in ("best", "fused", "enhanced", "charconf"):
        shutil.copy(f"{EVIDENCE}_{kind}.png", IMG / f"journey_{kind}.png")
    first, last, best = meta["first_frame"], meta["last_frame"], meta["best_frame"]
    step = max(1, (last - first) // 7)
    seq = sorted(set(range(first, last + 1, step)) | {best})
    got = frames(EVIDENCE_CLIP, set(seq) | {best})
    frame = got[best]
    h, w = frame.shape[:2]
    cv2.imwrite(str(IMG / "cam06_best_frame.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    px, py, px2, py2 = meta["bbox"]
    vx, vy, vx2, vy2 = meta["vehicle_box"]
    template = frame[int(py):int(py2), int(px):int(px2)]
    # same vehicle across its track: locate the plate in each frame, walking out from the best frame
    track = []
    centre = ((px + px2) / 2, (py + py2) / 2)
    order = sorted(seq, key=lambda f: abs(f - best))
    found = {}
    for f in order:
        score, box = locate(got[f], template, found.get("last", centre))
        if box is None:
            continue
        x, y, bw, bh = box
        found["last"] = (x + bw / 2, y + bh / 2)
        pad_x, pad_y = int(bw * 0.6), int(bh * 1.2)
        crop = got[f][max(0, y - pad_y):y + bh + pad_y, max(0, x - pad_x):x + bw + pad_x]
        name = f"track_{f}.png"
        cv2.imwrite(str(IMG / name), crop)
        track.append({"frame": f, "file": f"assets/img/{name}", "match": round(float(score), 3), "plate_px": int(bw)})
    track.sort(key=lambda t: t["frame"])
    hyps = {}
    for text, score, _fused, source, *_ in meta.get("_hyps", []):
        entry = hyps.setdefault(text, {"text": text, "count": 0, "best": 0.0, "sources": set()})
        entry["count"] += 1
        entry["best"] = max(entry["best"], float(score))
        entry["sources"].add(str(source).split("/")[-1])
    top = sorted(hyps.values(), key=lambda e: (-e["count"], -e["best"]))[:8]
    for e in top:
        e["sources"] = sorted(e["sources"])
        e["best"] = round(e["best"], 3)
    return {
        "plate": meta["plate"], "status": meta["status"], "confidence": meta["confidence"],
        "alternates": meta.get("alternates", []), "camera": meta["camera_id"], "track": meta["track_id"],
        "frames": {"first": first, "last": last, "best": best, "seen": meta["n_frames_seen"],
                   "plate_hits": meta["n_plate_hits"], "fused": meta["frames_fused"], "agreeing": meta["frames_agreeing"]},
        "hypotheses": meta["n_hypotheses"], "hypotheses_total": len(meta.get("_hyps", [])),
        "detector_confidence": meta["det_conf"], "valid_format": meta["valid_format"],
        "per_char_conf": meta["per_char_conf"], "quality": meta["quality"], "gate": meta["gate"],
        "frame_size": [w, h],
        "plate_box": [round(px / w, 4), round(py / h, 4), round((px2 - px) / w, 4), round((py2 - py) / h, 4)],
        "vehicle_box": [round(vx / w, 4), round(vy / h, 4), round((vx2 - vx) / w, 4), round((vy2 - vy) / h, 4)],
        "best_time_s": None,
        "sequence": track, "top_readings": top,
        "source": "ANPR research engine evidence pack, Sep 2026 (evidence/confirmed/cam06_s0_t132.json)",
    }


def failures() -> list[dict]:
    """One real unreadable or refused crop per cause, chosen by its own measurements."""
    rows = []
    for path in glob.glob(str(R / "evidence/unreadable/*.json")) + glob.glob(str(R / "evidence/candidate/*.json")):
        meta = json.loads(Path(path).read_text())
        q = meta.get("quality") or {}
        crop = path[:-5] + "_best.png"
        if q and Path(crop).exists():
            rows.append((meta, q, crop))
    picks = {
        "Tiny plate": min((r for r in rows if r[1].get("width_px")), key=lambda r: r[1]["width_px"]),
        "Low light": max(rows, key=lambda r: r[1].get("dark_frac", 0)),
        "Headlight glare": max(rows, key=lambda r: r[1].get("bloom_frac", 0)),
        "Motion blur": max((r for r in rows if r[1].get("width_px", 0) >= 30), key=lambda r: r[1].get("blur_extent", 0)),
        "Low contrast": min((r for r in rows if r[1].get("width_px", 0) >= 30), key=lambda r: r[1].get("local_contrast", 1e9)),
    }
    out = []
    for label, (meta, q, crop) in picks.items():
        name = "fail_" + label.lower().replace(" ", "_") + ".png"
        shutil.copy(crop, IMG / name)
        out.append({"label": label, "file": f"assets/img/{name}", "camera": meta.get("camera_id"),
                    "status": meta.get("status"), "reason": meta.get("reason") or "", "plate": meta.get("plate"),
                    "width_px": q.get("width_px"), "height_px": q.get("height_px"),
                    "dark_frac": round(q.get("dark_frac", 0), 3), "bloom_frac": round(q.get("bloom_frac", 0), 3),
                    "blur_extent": round(q.get("blur_extent", 0), 2), "contrast": round(q.get("local_contrast", 0), 1),
                    "source": Path(crop).name})
    return out


def main() -> int:
    VID.mkdir(parents=True, exist_ok=True)
    IMG.mkdir(parents=True, exist_ok=True)
    for slot, (src, height, start, seconds) in VIDEOS.items():
        if src.exists():
            cut(slot, src, height, start, seconds)
        else:
            print(f"MISSING video {slot}: {src}")
    for name, src in IMAGES.items():
        if src.exists():
            shutil.copy(src, IMG / name)
        else:
            print(f"MISSING image {name}: {src}")
    ev = evidence()
    fails = failures()
    (HERE / "js/data/evidence.js").write_text(
        "/* Generated by tools/build_assets.py from the project's evidence files. Do not edit by hand. */\n"
        f"window.K = window.K || {{}};\nK.EVIDENCE = {json.dumps(ev, indent=1, default=list)};\n"
        f"K.FAILURES = {json.dumps(fails, indent=1)};\n")
    print("evidence plate", ev["plate"], "sequence", [(t["frame"], t["match"], t["plate_px"]) for t in ev["sequence"]])
    print("failures", [(f["label"], f["width_px"], f["reason"]) for f in fails])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
