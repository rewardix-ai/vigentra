"""The packaged model: one object that runs the exact pipeline behind every
number in reports/ on any input (video file, RTSP URL, HLS playlist).

    from anpr.api import ANPR
    result = ANPR().process("data/sandbox_hls/cam06.ts", out_dir="out/cam06", annotate=True)
    for r in result.confirmed:
        print(r["plate"], r["confidence"], r["best_crop_uri"])

Frozen configuration (what produced reports/FINAL_REPORT.md on 2026-09-10):
  vehicles   models/yolo11s.pt + ByteTrack (config/bytetrack.yaml), imgsz 1920
  plates     models/plate_det_mix_n.pt (YOLO11n fine-tune, Vehicle-Rear + CCPD hard) + retro proposer
  overlay    config/roi.yaml (persistent-white OSD text, derived full-width strips, overlay-by-motion)
  scene text anpr/detect/static_text.py (hoardings / sign boards never become candidates)
  reader     models/reader_crnn.onnx (CRNN-CTC 64x256, Indian alphabet) + grammar beam + ROVER
  decision   config/thresholds.yaml (CONFIRMED: conf >= 0.75, >= 3 fused or agreeing single frames,
             best crop >= 40 px, >= 6 hypotheses, every character's vote >= 0.5, runner-up < 0.5 x
             winner, >= 2 single frames agreeing; else CANDIDATE / UNREADABLE)
  reading    config/thresholds.yaml: reading (2026-09-11): two-row plates read both ways, 6 single
             crops, class-agnostic vehicle NMS, 1 plate box per vehicle per frame (reports/LOOP_LOG.md H9)
SHA256 of every weight file: models/PROVENANCE.json.
"""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import yaml

from anpr.pipeline import ANPRPipeline
from anpr.sources import FileSource, HLSSource, RTSPSource
from anpr.sources.grid import camera_url, is_camera_id, safe_url, with_credentials

ROOT = Path(__file__).resolve().parent.parent
FROZEN = {
    "vehicle_weights": "models/yolo11s.pt",
    "plate_weights": "models/plate_det_mix_n.pt",
    "reader_weights": "models/reader_crnn.onnx",
    "thresholds": "config/thresholds.yaml",
    "roi": "config/roi.yaml",
    "frame_stride": 5,
    "vehicle_backend": "yolo",
}
COL = {"CONFIRMED": (0, 200, 0), "CANDIDATE": (0, 200, 255), "UNREADABLE": (140, 140, 140)}


@dataclass
class Result:
    camera: str
    source: str
    records: list[dict]
    out_dir: Optional[Path]
    frames_processed: int
    wall_s: float
    devices: dict = field(default_factory=dict)

    @property
    def confirmed(self) -> list[dict]:
        return [r for r in self.records if r["status"] == "CONFIRMED"]

    @property
    def candidates(self) -> list[dict]:
        return [r for r in self.records if r["status"] == "CANDIDATE"]

    def summary(self) -> dict:
        st = {}
        for r in self.records:
            st[r["status"]] = st.get(r["status"], 0) + 1
        return {"camera": self.camera, "source": self.source, "frames_processed": self.frames_processed,
                "wall_s": round(self.wall_s, 1), "fps": round(self.frames_processed / self.wall_s, 2) if self.wall_s else 0,
                "tracks": len(self.records), "by_status": st, "devices": self.devices,
                "confirmed": [{"plate": r["plate"], "confidence": r["confidence"], "frames_fused": r.get("frames_fused", 0),
                               "first_seen_s": round(r["first_seen_pts_ms"] / 1000, 1), "last_seen_s": round(r["last_seen_pts_ms"] / 1000, 1),
                               "evidence": r.get("best_crop_uri")} for r in self.confirmed],
                "top_candidates": sorted([{"plate": r["plate"], "confidence": round(r["confidence"], 3), "reason": r.get("reason", "")}
                                          for r in self.candidates if r.get("plate")], key=lambda t: -t["confidence"])[:10]}


class ANPR:
    """Detect -> enhance -> read, exactly as evaluated. One instance per camera stream."""

    def __init__(self, device: str = "auto", frame_stride: int = FROZEN["frame_stride"],
                 plate_weights: str = FROZEN["plate_weights"], vehicle_weights: str = FROZEN["vehicle_weights"],
                 thresholds: str = FROZEN["thresholds"], roi: str = FROZEN["roi"], root: Path = ROOT,
                 verify_with_claude: bool = False, state: Optional[str] = None):
        # grammar tie-break state: "GJ" for the Sentinel estate (config default), "DL" in Delhi,
        # "" for no preference; None = config/thresholds.yaml reading.preferred_state
        self.state = state
        # Optional second opinion from Claude (claude-opus-5, needs ANTHROPIC_API_KEY): a CANDIDATE
        # becomes CONFIRMED only when Claude reads EXACTLY the same string with certainty; a
        # CONFIRMED read is downgraded when Claude reads a different valid plate with certainty.
        # Claude alone never confirms anything. Off by default (offline, deterministic).
        self.verify_with_claude = verify_with_claude
        self.root = Path(root)
        self.device = device
        self.frame_stride = frame_stride
        self.plate_weights = str(self.root / plate_weights)
        self.vehicle_weights = str(self.root / vehicle_weights)
        self.thresholds = str(self.root / thresholds)
        self.roi = str(self.root / roi)

    # ------------------------------------------------------------------
    def _source(self, uri: str, camera: str, hls_config: Optional[str], seconds: Optional[float]):
        if is_camera_id(uri):
            uri = camera_url(uri)  # "cam06" -> the grid's RTSP gateway, as Vigentra's edge worker opens it
        low = uri.lower()
        if low.startswith(("rtsp://", "rtsps://")):
            # credentials from SENTINEL_GRID_EMAIL / SENTINEL_GRID_PASSWORD; the ffmpeg pipe needs
            # ffmpeg + ffprobe on PATH, otherwise OpenCV's bundled FFmpeg decodes (TCP forced)
            backend = "ffmpeg" if shutil.which("ffmpeg") and shutil.which("ffprobe") else "opencv"
            return RTSPSource(with_credentials(uri), camera, backend=backend, max_reconnects=3)
        if low.endswith(".m3u8") or low.startswith(("http://", "https://")):
            cookie = None
            if hls_config and Path(hls_config).exists():
                cookie = yaml.safe_load(open(hls_config, encoding="utf-8")).get("cookie")
            if not cookie and os.getenv("SENTINEL_GRID_PASSWORD"):
                # the grid's HLS sits behind the access password (Integrator Guide, access model):
                # sign in the way Vigentra's edge worker does and consume the stream live
                from anpr.sources.grid import session_cookie
                cookie, why = session_cookie()
                if not cookie:
                    raise ConnectionError(f"grid sign-in refused ({why}); update SENTINEL_GRID_PASSWORD / _EMAIL")
            return HLSSource(uri, camera, cookie=cookie)
        return FileSource(uri, camera)

    def process(self, source: str, camera: Optional[str] = None, out_dir: Optional[str | Path] = None,
                annotate: bool = False, max_frames: Optional[int] = None, seconds: Optional[float] = None,
                hls_config: Optional[str] = "config/sandbox_hls.local.yaml", write_candidates: bool = True,
                progress: bool = False, bank_dump_dir: Optional[str | Path] = None) -> Result:
        camera = camera or Path(source.split("?")[0]).stem.replace(".", "_")
        out = Path(out_dir) if out_dir else None
        if out:
            out.mkdir(parents=True, exist_ok=True)
        # bank_dump_dir: pickle every closed track's crop bank so reading / decision changes can be
        # replayed offline (eval/replay_banks.py) without re-running detection
        pipe = ANPRPipeline(camera, thresholds=self.thresholds, roi_cfg=self.roi, vehicle_weights=self.vehicle_weights,
                            plate_weights=self.plate_weights, device=self.device, frame_stride=self.frame_stride,
                            evidence_dir=(out / "evidence") if out else None, write_candidates=write_candidates,
                            vehicle_backend=FROZEN["vehicle_backend"], preferred_state=self.state,
                            bank_dump_dir=bank_dump_dir)
        src = self._source(source, camera, hls_config, seconds)
        # files are annotated in a second pass (_render_file) with every vehicle labelled by its
        # final read from its first frame; live sources cannot be re-read and keep the streaming overlay
        live = bool(getattr(src, "is_live", False))
        track_log: list = []
        writer = None
        last_box: dict[str, tuple] = {}
        shown: dict[str, tuple[dict, int]] = {}
        panel: list[dict] = []
        seen = 0
        n_done = 0
        devices: dict = {}
        t0 = time.perf_counter()
        clock = _StreamClock() if seconds is not None else None
        ann_clock = _StreamClock(max_step_ms=5000.0)  # PTS span of the annotated frames -> real-time playback rate
        n_ann = 0
        try:
            for fr in src:
                if max_frames is not None and n_done >= max_frames:
                    break
                if clock is not None and clock.advance(fr.pts_ms) > seconds * 1000:
                    break
                pipe.process_frame(fr)
                if fr.frame_idx % self.frame_stride != 0:
                    continue
                n_done += 1
                if not devices:
                    devices = _device_report(pipe)
                if progress and n_done % 100 == 0:
                    print(f"[{camera}] frames {n_done} tracks {len(pipe.records)} confirmed "
                          f"{sum(r['status'] == 'CONFIRMED' for r in pipe.records)}", flush=True)
                if not annotate or out is None:
                    continue
                if not live:
                    track_log.append((fr.frame_idx,
                                      [(tid, tuple(float(v) for v in box), cls) for tid, box, cls, _ in pipe.last_vehicles],
                                      [(tid, tuple(float(v) for v in box)) for tid, box, _, _ in pipe.last_plates]))
                    continue
                canvas = _draw(fr, pipe, last_box, shown, panel, seen, devices)
                seen = len(pipe.records)
                ann_clock.advance(fr.pts_ms)
                n_ann += 1
                if writer is None:
                    writer = cv2.VideoWriter(str(out / f"annotated_{camera}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                             max(1.0, 25.0 / self.frame_stride), (canvas.shape[1], canvas.shape[0]))
                writer.write(canvas)
        finally:
            pipe.flush()
            if writer is not None:
                writer.release()
            src.close()
        wall = time.perf_counter() - t0
        if writer is not None:
            fps = (n_ann - 1) * 1000.0 / ann_clock.elapsed_ms if ann_clock.elapsed_ms > 0 else 25.0 / self.frame_stride
            _to_h264(out / f"annotated_{camera}.mp4", fps)
        if self.verify_with_claude and out:
            self._verify(pipe.records, out / "evidence")
        if out:
            # evidence paths on every record that has a pack (written by the pipeline per status)
            for r in pipe.records:
                sub = out / "evidence" / r["status"].lower()
                for key, suffix in (("best_crop_uri", "_best.png"), ("fused_crop_uri", "_fused.png"),
                                    ("enhanced_crop_uri", "_enhanced.png"), ("frame_uri", "_frame.jpg")):
                    f = sub / f"{r['track_id']}{suffix}"
                    if f.exists():
                        r[key] = str(f.as_posix())
        if annotate and out is not None and not live and track_log:
            # per-frame boxes, so tools/render_annotated.py can re-render without re-running detection
            (out / "tracks.json").write_text(json.dumps({"camera": camera, "source": str(source), "stride": self.frame_stride,
                                                         "track_log": track_log}), encoding="utf-8")
            _render_file(source, out / f"annotated_{camera}.mp4", track_log, pipe.records, camera, self.frame_stride)
        res = Result(camera, safe_url(source), pipe.records, out, n_done, wall, devices)
        if out:
            pipe.dump(out / "pipeline.json")
            (out / "records.json").write_text(json.dumps(pipe.records, indent=1), encoding="utf-8")
            (out / "summary.json").write_text(json.dumps(res.summary(), indent=1), encoding="utf-8")
            with open(out / "confirmed.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["plate", "confidence", "first_seen_s", "last_seen_s", "frames_fused", "vehicle_type", "evidence_best_crop"])
                for r in res.confirmed:
                    w.writerow([r["plate"], f"{r['confidence']:.3f}", f"{r['first_seen_pts_ms'] / 1000:.1f}", f"{r['last_seen_pts_ms'] / 1000:.1f}",
                                r.get("frames_fused", 0), r.get("vehicle_type", ""), r.get("best_crop_uri", "")])
        return res


    # ------------------------------------------------------------------
    def _verify(self, records: list[dict], ev: Path, min_conf: float = 0.35, min_w: float = 40.0) -> None:
        from anpr.read.claude_reader import ClaudeReader
        reader = ClaudeReader()
        for r in records:
            if not r.get("plate") or r["status"] == "UNREADABLE":
                continue
            w = (r.get("quality") or {}).get("width_px", 0)
            if r["status"] == "CANDIDATE" and (r["confidence"] < min_conf or w < min_w or r.get("frames_fused", 0) < 3):
                continue
            sub = ev / r["status"].lower()
            best = cv2.imread(str(sub / f"{r['track_id']}_best.png"))
            fused = cv2.imread(str(sub / f"{r['track_id']}_fused.png"))
            if best is None:
                continue
            try:
                t = reader.read(best, fused)
            except Exception as e:
                r["verifier"] = {"error": type(e).__name__}
                continue
            r["verifier"] = {"model": reader.model, "plate": t.plate, "certainty": t.certainty, "valid": t.valid, "note": t.note}
            agree = bool(t.plate) and t.valid and t.certainty == "certain" and t.plate == r["plate"]
            disagree = bool(t.plate) and t.valid and t.certainty == "certain" and t.plate != r["plate"]
            if r["status"] == "CANDIDATE" and agree:
                r["status"], r["reason"] = "CONFIRMED", "verified_by_claude"
            elif r["status"] == "CONFIRMED" and disagree:
                r["status"], r["reason"] = "CANDIDATE", "verifier_disagreed"
                r["alternates"] = [{"plate": t.plate, "confidence": 0.0, "source": "claude"}] + (r.get("alternates") or [])
        self.verifier_calls = reader.n_calls
        self.verifier_tokens = (reader.input_tokens, reader.output_tokens)


# ----------------------------------------------------------------------
class _StreamClock:
    """Milliseconds of stream consumed, summed from per-frame PTS steps clipped to
    [0, max_step_ms]. Raw `pts - first_pts` never ends a --seconds run once a looping
    feed's PTS jumps back at the seam, and one broken-timestamp spike ends it early."""

    def __init__(self, max_step_ms: float = 1000.0):
        self.max_step_ms = max_step_ms
        self.elapsed_ms = 0.0
        self._prev: Optional[float] = None

    def advance(self, pts_ms: float) -> float:
        if self._prev is not None:
            self.elapsed_ms += min(max(pts_ms - self._prev, 0.0), self.max_step_ms)
        self._prev = pts_ms
        return self.elapsed_ms


def _device_report(pipe: ANPRPipeline) -> dict:
    try:
        import torch
    except Exception:  # pragma: no cover
        return {}
    rep = {"torch_cuda_available": bool(torch.cuda.is_available())}
    rep["plate_detector"] = str(getattr(pipe.plates, "device", "?"))
    rd = pipe.ensemble.readers[0] if pipe.ensemble.readers else None
    if rd is not None:
        rep["reader"] = (f"onnx:{rd.sess.get_providers()[0]}" if getattr(rd, "sess", None) is not None
                         else f"torch:{getattr(rd, 'dev', '?')}")
    if torch.cuda.is_available():
        rep["gpu"] = torch.cuda.get_device_name(0)
    return rep


def _draw(fr, pipe: ANPRPipeline, last_box: dict, shown: dict, panel: list, seen: int, devices: dict,
          hold_frames: int = 45) -> np.ndarray:
    vis = fr.image.copy()
    H, W = vis.shape[:2]
    # sizes are designed for 1080p and scaled with the frame, so a 4K annotation stays readable
    # once it is shrunk to a screen (and to the 1920-px H.264 deliverable)
    s = max(1.0, H / 1080.0)
    th = max(2, round(2 * s))
    font = cv2.FONT_HERSHEY_SIMPLEX
    for tid, box, cls, conf in pipe.last_vehicles:
        x1, y1, x2, y2 = [int(v) for v in box]
        last_box[tid] = (x1, y1, x2, y2)
        cv2.rectangle(vis, (x1, y1), (x2, y2), (255, 160, 0), th)
        cv2.putText(vis, f"{tid.split('_')[-1]} {cls}", (x1, max(int(14 * s), y1 - int(6 * s))), font, 0.55 * s, (255, 160, 0), th)
    for tid, box, srcname, conf in pipe.last_plates:
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0) if srcname == "cnn" else (0, 165, 255), th)
    for rec in pipe.records[seen:]:
        tid = rec["track_id"].split("_", 1)[1] if "_" in rec["track_id"] else rec["track_id"]
        shown[tid] = (rec, fr.frame_idx)
        if rec["status"] != "UNREADABLE":
            panel.append(rec)
    for tid, (rec, f0) in list(shown.items()):
        if fr.frame_idx - f0 > hold_frames or tid not in last_box:
            continue
        x1, y1, x2, y2 = last_box[tid]
        col = COL[rec["status"]]
        text = f"{rec['plate']} {rec['confidence']:.2f}" if rec.get("plate") else rec["status"]
        (tw, tht), _ = cv2.getTextSize(text, font, 0.6 * s, th)
        cv2.rectangle(vis, (x1, y2 + 2), (x1 + tw + int(8 * s), y2 + tht + int(14 * s)), col, -1)
        cv2.putText(vis, text, (x1 + int(4 * s), y2 + tht + int(7 * s)), font, 0.6 * s, (0, 0, 0), th)
    pw = int(420 * s)
    canvas = np.zeros((H, W + pw, 3), np.uint8)
    canvas[:, :W] = vis
    thin = max(1, round(s))
    cv2.putText(canvas, f"ANPR | frame {fr.frame_idx} | {devices.get('gpu') or devices.get('plate_detector', 'cpu')}", (W + int(10 * s), int(28 * s)),
                font, 0.55 * s, (255, 255, 255), thin)
    y = int(60 * s)
    for rec in panel[-18:][::-1]:
        col = COL[rec["status"]]
        cv2.putText(canvas, f"{rec['status'][:4]} {rec.get('plate') or '-':12s} {rec['confidence']:.2f}  f{rec.get('frames_fused', 0)}",
                    (W + int(10 * s), y), font, 0.55 * s, col, thin)
        y += int(26 * s)
    return canvas


def _plausible(r: Optional[dict]) -> bool:
    """A read worth showing on the video: CONFIRMED, or a CANDIDATE that is a valid Indian plate
    format and that at least one single frame read by itself (not only the fused vote). Anything
    else (fragments like 'ZB065', stitched strings) is shown as 'no clear read'."""
    if not r or not r.get("plate"):
        return False
    if r["status"] == "CONFIRMED":
        return True
    from anpr.plate_grammar import score_string
    gs = score_string(r["plate"], "")
    # a real registration: a known state code (the grammar only down-weights unknown ones, so
    # 'ZB065' is formally "valid") and a full-length number, >= 8 characters (GJ01A123 .. GJ01AB1234)
    return (r.get("frames_agreeing", 0) >= 1 and gs.valid and len(r["plate"]) >= 8
            and not any(x.startswith("unknown_state") for x in gs.reasons))


def _render_file(path: str, dst: Path, track_log: list, records: list[dict], camera: str, stride: int,
                 out_width: int = 1920, panel_frac: float = 0.22) -> bool:
    """Second pass over a video FILE for the annotated deliverable: every source frame (real
    frame rate), every vehicle and plate box (interpolated between the processed frames), and
    each vehicle labelled with its FINAL read from the moment it appears - the streaming overlay
    can only label a vehicle after its track has closed. Green = CONFIRMED, amber '?' = read but
    not verified (CANDIDATE), grey = plate seen but too small / blurred to read."""
    import bisect
    pref = camera + "_"
    rec = {(r["track_id"][len(pref):] if r["track_id"].startswith(pref) else r["track_id"]): r for r in records}
    vkeys: dict[str, list] = {}
    pkeys: dict[str, list] = {}
    for fidx, vs, ps in track_log:
        for tid, box, _cls in vs:
            vkeys.setdefault(tid, []).append((fidx, box))
        for tid, box in ps:
            pkeys.setdefault(tid, []).append((fidx, box))
    vidx = {t: [k[0] for k in ks] for t, ks in vkeys.items()}
    pidx = {t: [k[0] for k in ks] for t, ks in pkeys.items()}
    first_seen = sorted((ks[0][0], t) for t, ks in vkeys.items())

    def at(keys, idx, f):
        i = bisect.bisect_right(idx, f) - 1
        if i < 0:
            return None
        f0, b0 = keys[i]
        if f0 == f:
            return b0
        if i + 1 < len(keys) and keys[i + 1][0] - f0 <= 3 * stride:
            f1, b1 = keys[i + 1]
            t = (f - f0) / (f1 - f0)
            return tuple(a + (b - a) * t for a, b in zip(b0, b1))
        return b0 if f - f0 <= stride else None

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return False
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0   # playback rate of the rendered file only
    font = cv2.FONT_HERSHEY_SIMPLEX
    pw = int(out_width * panel_frac)
    fw = out_width - pw
    writer = None
    f = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        H, W = img.shape[:2]
        s = fw / W
        fh = max(2, int(round(H * s)) // 2 * 2)
        vis = cv2.resize(img, (fw, fh), interpolation=cv2.INTER_AREA)
        k = fh / 800.0
        fs, th = 0.62 * k, max(1, round(2 * k))
        for t, ks in vkeys.items():
            vb = at(ks, vidx[t], f)
            if vb is None:
                continue
            r = rec.get(t)
            st = r["status"] if r else "UNREADABLE"
            ok_read = _plausible(r)
            col = COL[st] if (ok_read or st == "UNREADABLE") else COL["UNREADABLE"]
            x1, y1, x2, y2 = [int(v * s) for v in vb]
            cv2.rectangle(vis, (x1, y1), (x2, y2), col, 1)
            pb = at(pkeys[t], pidx[t], f) if t in pkeys else None
            if pb is not None:
                px1, py1, px2, py2 = [int(v * s) for v in pb]
                cv2.rectangle(vis, (px1, py1), (px2, py2), col, th)
            if ok_read:
                text = r["plate"] if st == "CONFIRMED" else r["plate"] + "?"
            elif pb is not None or (r and r.get("plate")):
                text = "unreadable" if st == "UNREADABLE" else "no clear read"
            else:
                continue
            (tw, tht), _ = cv2.getTextSize(text, font, fs, th)
            ax, ay = (px1, py1) if pb is not None else (x1, y2)
            ax = min(max(0, ax), fw - tw - 8)
            top = max(0, ay - tht - int(10 * k))
            cv2.rectangle(vis, (ax, top), (ax + tw + int(8 * k), top + tht + int(10 * k)), col, -1)
            cv2.putText(vis, text, (ax + int(4 * k), top + tht + int(4 * k)), font, fs, (0, 0, 0), th)
        canvas = np.zeros((fh, out_width, 3), np.uint8)
        canvas[:, :fw] = vis
        seen = [t for f0, t in first_seen if f0 <= f]
        reads = [rec[t] for t in seen if _plausible(rec.get(t))]
        n_conf = sum(1 for r in reads if r["status"] == "CONFIRMED")
        x0, y = fw + int(12 * k), int(34 * k)
        for line, c in ((f"ANPR  {camera}   t={f / fps:5.1f}s", (255, 255, 255)),
                        (f"vehicles {len(seen)}   plates read {len(reads)}", (255, 255, 255)),
                        (f"confirmed {n_conf}", COL["CONFIRMED"]),
                        ("green = confirmed, amber ? = unverified", (170, 170, 170))):
            cv2.putText(canvas, line, (x0, y), font, 0.55 * k, c, max(1, round(k)))
            y += int(28 * k)
        y += int(8 * k)
        ordered = [r for r in reads if r["status"] == "CONFIRMED"][::-1] + [r for r in reads if r["status"] != "CONFIRMED"][::-1]
        for r in ordered:
            if y > fh - int(12 * k):
                break
            label = f"{r['plate']:11s} {r['confidence']:.2f}" if r["status"] == "CONFIRMED" else f"{r['plate']}?"
            cv2.putText(canvas, label, (x0, y), font, 0.6 * k, COL[r["status"]], max(1, round(1.5 * k)))
            y += int(26 * k)
        if writer is None:
            writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_width, fh))
        writer.write(canvas)
        f += 1
    cap.release()
    if writer is None:
        return False
    writer.release()
    return _to_h264(dst, fps, max_width=out_width)


def _to_h264(path: Path, fps: float, max_width: int = 1920) -> bool:
    """Re-encode the OpenCV mp4v annotation to H.264 (yuv420p, faststart) at the real playback
    rate and at most `max_width` wide, so it plays in QuickTime, browsers and phones. The mp4v
    file is kept when no ffmpeg is available or the encode fails."""
    from anpr.sources.grid import ffmpeg_exe
    ffmpeg = ffmpeg_exe()
    if ffmpeg is None or not path.exists():
        return False
    tmp = path.with_suffix(".h264.mp4")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
           "-vf", f"setpts=N/({fps:.4f}*TB),scale='min({max_width},iw)':-2,setsar=1", "-r", f"{fps:.4f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
    try:
        ok = subprocess.run(cmd, capture_output=True, timeout=1800).returncode == 0 and tmp.exists() and tmp.stat().st_size > 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if ok:
        tmp.replace(path)
    elif tmp.exists():
        tmp.unlink()
    return ok
