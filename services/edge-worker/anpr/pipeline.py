"""Pipeline orchestration: source -> overlay mask -> vehicle track -> plate
detect -> crop bank -> (on track close) legibility gate -> enhance -> read ->
fuse -> output record + evidence pack.

Every Stage B module is a toggle in `ablate` so eval can switch it off.
Per-stage timings are accumulated for the report.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np
import yaml

from anpr.detect.char_evidence import track_glyphs
from anpr.detect.corners import estimate_corners
from anpr.detect.overlay_mask import OverlayMasker, ROIConfig
from anpr.detect.plate import PlateDetector
from anpr.detect.static_text import StaticTextMap, _iou
from anpr.detect.vehicle import VehicleTracker
from anpr.enhance.deblur import Deblurrer
from anpr.enhance.denoise import Denoiser
from anpr.enhance.fuse import fuse
from anpr.enhance.glare import suppress_glare
from anpr.enhance.rectify import rectify, deskew_residual
from anpr.enhance.sr import SuperResolver
from anpr.evidence import write_evidence
from anpr.fuse.rover import ReadHypothesis, rover
from anpr.plate_grammar import looks_like_overlay
from anpr.read.crnn import CRNNReader
from anpr.read.ensemble import ReaderEnsemble, Variant
from anpr.read.parseq import PARSeqReader
from anpr.sources import Frame, FrameSource
from anpr.track.crop_bank import CropBankStore, TrackBank

log = logging.getLogger("anpr.pipeline")


@dataclass
class _LayoutRead:
    """One row-layout reading of a track (see ANPRPipeline._read_layout)."""
    two_row: bool
    fused: object            # FusedRead
    hyps: list
    n_used: int
    reg_cc: list
    fres_img: np.ndarray
    enh: np.ndarray
    n_agree: int


def _agreeing_frames(hyps: list, plate: str) -> int:
    """Distinct single-crop variants whose own top read is within one character of `plate`."""
    if not plate:
        return 0
    import editdistance
    return len({h.source.split("/")[0].split("~")[0] for h in hyps
                if h.source.startswith("single") and getattr(h, "rank", 0) == 0 and editdistance.eval(h.text, plate) <= 1})


def _readers_supporting(hyps: list, plate: Optional[str]) -> set:
    """Reader names that produced exactly `plate` as a top string (ReadHypothesis objects or the
    (text, weight, prob, source, is_sr) tuples kept for fragment merging). Sources look like
    'single0/crnn', 'fused_gray/crnn_reader_crnn_v4' or 'vote/crnn+crnn_reader_crnn_v4'."""
    out: set = set()
    if not plate:
        return out
    for h in hyps:
        text, src, rank = (h.text, h.source, getattr(h, "rank", 0)) if hasattr(h, "text") else (h[0], h[3], 0)
        if text == plate and rank == 0 and "/" in src:
            out.update(src.split("/", 1)[1].split("+"))
    return out


def _photometric_variants(g: np.ndarray, ops: list) -> list[tuple[str, np.ndarray]]:
    """Adaptive re-renderings of one gray plate crop for the reader. 'gamma' only fires on
    under- (mean < 90) or over-exposed (mean > 170) crops, so normal crops are not altered."""
    out = []
    for op in ops:
        if op == "clahe":
            out.append((op, cv2.createCLAHE(clipLimit=2.0, tileGridSize=(2, 8)).apply(g)))
        elif op == "gamma":
            m = float(g.mean())
            gamma = 0.6 if m < 90 else 1.6 if m > 170 else None
            if gamma is not None:
                lut = (np.power(np.arange(256) / 255.0, gamma) * 255).astype(np.uint8)
                out.append((op, cv2.LUT(g, lut)))
        elif op == "sharpen":
            out.append((op, cv2.addWeighted(g, 1.6, cv2.GaussianBlur(g, (0, 0), 1.2), -0.6, 0)))
    return out


def _top_valid_texts(hyps: list, k: int = 3) -> list[str]:
    """The k heaviest hypothesis strings of a record that are full registrations of a known state."""
    from anpr.plate_grammar import score_string
    w: dict[str, float] = {}
    for t, wt, *_ in hyps:
        w[t] = w.get(t, 0.0) + float(wt)
    out = []
    for t in sorted(w, key=w.get, reverse=True):
        gs = score_string(t, "")
        if len(t) >= 8 and gs.valid and not any(x.startswith("unknown_state") for x in gs.reasons):
            out.append(t)
            if len(out) >= k:
                break
    return out


def _one_track_per_plate(cands: list[tuple]) -> list[tuple]:
    """One physical plate goes to one track per frame.

    The plate detector runs once per vehicle box, and vehicle boxes overlap: a car inside a bus's
    box, a lorry boxed twice as cab and trailer, a tracker duplicate. Each of them proposed the
    same plate pixels, so one registration was banked into several tracks and each of them
    voted on a mixture (delhi_1080p: 64 same-frame duplicate crops across 33 track pairs; t221
    held DL1LT1087 next to the neighbour's DL3CCN5712, t560 three different plates). Of two
    proposals for the same box (IoU >= 0.5) the higher post-prior score wins - the geometry prior
    already marks a plate that is tiny for its vehicle box - and at equal score the tighter
    vehicle box, the one the plate more plausibly belongs to.
    """
    def area(v) -> float:
        return max(0.0, v.box[2] - v.box[0]) * max(0.0, v.box[3] - v.box[1])

    kept: list[tuple] = []
    for v, p in sorted(cands, key=lambda vp: (-vp[1].conf, area(vp[0]))):
        if all(k[0].track_id == v.track_id or _iou(k[1].box, p.box) < 0.5 for k in kept):
            kept.append((v, p))
    return kept


@dataclass
class Timings:
    frames: int = 0
    mask_ms: float = 0.0
    vehicle_ms: float = 0.0
    plate_ms: float = 0.0
    enhance_ms: float = 0.0
    read_ms: float = 0.0
    tracks_closed: int = 0

    def as_dict(self) -> dict:
        n = max(self.frames, 1)
        t = max(self.tracks_closed, 1)
        return {"frames": self.frames, "tracks_closed": self.tracks_closed,
                "mask_ms_per_frame": self.mask_ms / n, "vehicle_ms_per_frame": self.vehicle_ms / n,
                "plate_ms_per_frame": self.plate_ms / n, "enhance_ms_per_track": self.enhance_ms / t,
                "read_ms_per_track": self.read_ms / t,
                "total_ms_per_frame": (self.mask_ms + self.vehicle_ms + self.plate_ms) / n}


class ANPRPipeline:
    def __init__(self, camera_id: str, thresholds: str | Path = "config/thresholds.yaml",
                 roi_cfg: str | Path = "config/roi.yaml", vehicle_weights: str = "models/yolo11s.pt",
                 plate_weights: Optional[str] = "models/plate_det.pt", device: str = "auto",
                 ablate: Iterable[str] = (), evidence_dir: Optional[str | Path] = "evidence",
                 frame_stride: int = 1, write_candidates: bool = True, keep_frames: bool = True,
                 reader_weights: Optional[list[str]] = None, vehicle_backend: str = "yolo",
                 preferred_state: Optional[str] = None, bank_dump_dir: Optional[str | Path] = None):
        with open(thresholds, "r", encoding="utf-8") as fh:
            self.cfg = yaml.safe_load(fh)
        # reading / decision options; an absent key keeps the frozen 2026-09-10 behaviour
        self.rcfg = self.cfg.get("reading") or {}
        # grammar tie-break state (a plate from this state gets no 0.7 prior penalty). Was
        # hard-coded "GJ"; a Delhi deployment scores every DL plate x0.7 with it. "" = none.
        self.state = preferred_state if preferred_state is not None else self.rcfg.get("preferred_state", "GJ")
        # offline replay (eval/replay_banks.py): every finalised track bank is pickled here
        self.bank_dump_dir = Path(bank_dump_dir) if bank_dump_dir else None
        self._n_dumped = 0
        if self.bank_dump_dir:
            self.bank_dump_dir.mkdir(parents=True, exist_ok=True)
        self.camera_id = camera_id
        self.ablate = set(ablate)
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.frame_stride = max(1, frame_stride)
        self.write_candidates = write_candidates
        self.keep_frames = keep_frames
        self.frame_cache_size = 30
        d = self.cfg["detector"]
        # a vehicle narrower than this carries a plate too small to read (< 40 px: 0% exact even
        # fused), so its plate search is skipped until it comes closer. 0 = search every vehicle
        self.min_vehicle_px = float(d.get("plate_search_min_vehicle_px", 0))
        self.vehicle_backend = vehicle_backend
        if vehicle_backend == "rtdetr":
            # IISc UVH-26 RT-DETRv2-S (Indian classes incl. auto/two-wheeler), fixed 640 input
            from anpr.detect.rtdetr_vehicle import RTDETRVehicleTracker
            self.vehicles = RTDETRVehicleTracker(device=device, conf=max(d["vehicle_conf"], 0.3), imgsz=640)
        else:
            self.vehicles = VehicleTracker(vehicle_weights, device, d["vehicle_imgsz"], d["vehicle_conf"],
                                           tuple(d["vehicle_classes"]))
        self.plates = PlateDetector(plate_weights, device, d["plate_imgsz"], d["plate_conf"],
                                    d["vehicle_crop_upscale_min_px"], tile=d.get("tile_size", 0),
                                    overlap=d.get("tile_overlap", 0.2), use_retro=d.get("retro_proposer", True),
                                    min_conf=float(d.get("plate_min_conf_after_prior", 0.0)))
        self.masker: Optional[OverlayMasker] = None
        self.roi_cfg = ROIConfig.load(roi_cfg, camera_id)
        self.bank = CropBankStore(camera_id, self.cfg["crop_bank"]["max_bank"])
        if reader_weights is None:
            # one CRNN (ONNX preferred, .pt fallback) + PARSeq if exported. Loading both
            # .onnx and .pt of the same CRNN doubled every hypothesis (42/track measured).
            rw = ["models/reader_crnn.onnx" if Path("models/reader_crnn.onnx").exists() else "models/reader_crnn.pt",
                  "models/reader_parseq.onnx"]
            if self.rcfg.get("crnn_weights"):          # e.g. a retrained reader under evaluation
                rw[0] = self.rcfg["crnn_weights"]
            # further CRNNs vote alongside (e.g. v3b per-row + v4 side-by-side): a confirm then needs
            # their evidence to agree, and one reader's confident misread splits the vote
            rw[1:1] = list(self.rcfg.get("extra_crnn_weights") or [])
        else:
            rw = list(reader_weights)
        readers = []
        for i, w in enumerate(rw):
            if "parseq" in w:
                readers.append(PARSeqReader(w))
            else:
                r = CRNNReader(w, device)
                if i > 0:                       # extra CRNNs get their own name so their votes stay distinguishable
                    r.name = f"crnn_{Path(w).stem}"
                readers.append(r)
        if "awiros" in (self.rcfg.get("readers") or []):
            # Awiros-ANPR-OCR (PP-OCRv5 fine-tuned on 558k Indian plates, two-row in one pass)
            from anpr.read.awiros import AwirosReader
            aw = AwirosReader()
            if aw.ok:
                readers.append(aw)
            else:
                log.warning("awiros reader requested but not available: %s", aw.err)
        self.ensemble = ReaderEnsemble(readers, preferred_state=self.state,
                                       reader_weight=self.rcfg.get("reader_weight") or {},
                                       text_variants=int(self.rcfg.get("awiros_crops", 0)))
        # fast CRNN-only ensemble for the per-crop read filter (reading.read_filter)
        fast = [r for r in self.ensemble.readers if not hasattr(r, "read_batch")]
        self.filter_ensemble = ReaderEnsemble(fast, preferred_state=self.state) if fast else None
        self.denoiser = Denoiser()
        self.deblurrer = Deblurrer()
        self.sr = SuperResolver()
        self.timings = Timings()
        self.records: list[dict] = []
        self.last_vehicles: list = []   # (track_id, box, cls_name, conf) for the last processed frame
        self.last_plates: list = []     # (track_id, box, source, conf)
        self._warm_dets: list = []      # full-frame plate detections during mask warm-up (static-text finder)
        # hoardings / sign boards / painted road names: same position + same pixels under different vehicles
        self.static_text = StaticTextMap(**(self.cfg.get("static_text") or {}))
        self._best_imgs: dict[str, tuple] = {}   # track_id -> (best box, best crop) for the retroactive check at flush
        self.frame_cache: dict[int, np.ndarray] = {}   # frame_idx -> image (for evidence)
        self._last_frame: Optional[np.ndarray] = None
        self.gate_log: list[dict] = []

    # ------------------------------------------------------------------
    def run(self, source: FrameSource, max_frames: Optional[int] = None) -> list[dict]:
        n = 0
        for frame in source:
            if max_frames is not None and n >= max_frames:
                break
            self.process_frame(frame)
            n += 1
        self.flush()
        return self.records

    def process_frame(self, frame: Frame) -> None:
        if self.masker is None:
            self.masker = OverlayMasker(self.roi_cfg, frame.shape)
        if frame.discontinuity and frame.frame_idx > 0:
            self.vehicles.reset()
            for b in self.bank.close_all():
                self._finalise_track(b)
        t0 = time.perf_counter()
        if not self.masker.ready:
            self.masker.observe(frame.image)
            # overlay-by-motion: full-frame plate-like detections that sit still across the
            # warm-up are burned-in OSD (timestamps, camera captions); collect them here
            if self.plates.model is not None and len(self._warm_dets) < 12 and frame.frame_idx % 3 == 0:
                try:
                    b, s = self.plates._infer(frame.image)
                    self._warm_dets.append([tuple(float(v) for v in bb) for bb, ss in zip(b, s) if ss >= 0.15])
                except Exception:
                    pass
            if self.masker.ready and self._warm_dets:
                n = self.masker.add_static_boxes(self._warm_dets)
                if n:
                    log.info("overlay-by-motion: masked %d static text boxes on %s", n, self.camera_id)
                self._warm_dets = []
        self.timings.mask_ms += (time.perf_counter() - t0) * 1000
        if frame.frame_idx % self.frame_stride != 0:
            return
        self.timings.frames += 1
        self._last_frame = frame.image
        if self.keep_frames:
            # evidence frames: keep a small window (each 1080p frame is 6 MB; the dev box has 8 GB RAM)
            self.frame_cache[frame.frame_idx] = frame.image
            if len(self.frame_cache) > self.frame_cache_size:
                for k in sorted(self.frame_cache)[:-self.frame_cache_size]:
                    del self.frame_cache[k]
        t1 = time.perf_counter()
        vdets = self.vehicles.update(frame.image, self.masker.mask)
        self.timings.vehicle_ms += (time.perf_counter() - t1) * 1000
        t2 = time.perf_counter()
        # per-frame state for visualisation / annotation tools
        self.last_vehicles = [(v.track_id, v.box, v.cls_name, v.conf) for v in vdets]
        self.last_plates = []
        cands: list[tuple] = []          # (vehicle, plate det) for every vehicle box in the frame
        for v in vdets:
            if not self.masker.box_allowed(*v.box, max_masked_frac=0.5):
                continue
            self.bank.touch(v.track_id, frame.frame_idx, frame.pts_ms, v.box, v.cls_name)
            if v.box[2] - v.box[0] < self.min_vehicle_px:
                continue
            for p in self.plates.detect_in_vehicle(frame.image, v.box, v.cls_name, frame.frame_idx, v.track_id):
                if self.masker.box_allowed(*p.box, self.cfg["detector"]["max_masked_frac"]):
                    cands.append((v, p))
        # plates banked per vehicle per frame: a second, weaker box inside the same vehicle is
        # almost always a bumper edge or the neighbour's plate, and it filled half the top-12
        # on the Delhi clip (68 of 116 tracks), failing registration and skewing the row vote
        per_vehicle = int(self.cfg["detector"].get("plates_per_vehicle", 2))
        n_banked: dict[str, int] = {}
        for v, p in _one_track_per_plate(cands):
            if n_banked.get(v.track_id, 0) >= per_vehicle:
                continue
            self.last_plates.append((v.track_id, p.box, p.source, p.conf))
            x1, y1, x2, y2 = p.box
            w, h = x2 - x1, y2 - y1
            mx, my = 0.12 * w, 0.25 * h
            X1, Y1 = int(max(0, x1 - mx)), int(max(0, y1 - my))
            X2, Y2 = int(min(frame.image.shape[1], x2 + mx)), int(min(frame.image.shape[0], y2 + my))
            crop = frame.image[Y1:Y2, X1:X2]
            if crop.size == 0:
                continue
            # hoardings / sign boards / shop names: same frame position and same pixels
            # under a different vehicle -> static scene text, never a plate
            if self.static_text.check(p.box, crop, v.box, v.track_id, frame.frame_idx):
                self.plates.rejection_log.append({"frame": frame.frame_idx, "track": v.track_id,
                                                  "box": [round(float(t), 1) for t in p.box], "reason": "static_scene_text"})
                continue
            corners, cconf = estimate_corners(crop)
            if cconf < 0.2:
                # trust the box: corners at the un-margined box
                corners = np.array([[x1 - X1, y1 - Y1], [x2 - X1, y1 - Y1], [x2 - X1, y2 - Y1], [x1 - X1, y2 - Y1]],
                                   np.float32)
            self.bank.add_crop(v.track_id, crop, corners, p.box, frame.frame_idx, frame.pts_ms, p.conf, p.two_row)
            n_banked[v.track_id] = n_banked.get(v.track_id, 0) + 1
        self.timings.plate_ms += (time.perf_counter() - t2) * 1000
        for b in self.bank.close_stale(frame.frame_idx, self.cfg["tracker"]["track_buffer"]):
            self._finalise_track(b)

    def flush(self) -> None:
        for b in self.bank.close_all():
            self._finalise_track(b)
        # retroactive: the first vehicle to pass a sign board was read before the board's
        # position was learned from the vehicles that followed; demote those records now
        for r in self.records:
            if r.get("plate") and r["track_id"] in self._best_imgs:
                box, img = self._best_imgs[r["track_id"]]
                if self.static_text.is_static(box, img):
                    r.update(status="UNREADABLE", plate=None, confidence=0.0, alternates=[], reason="static_scene_text")
                    r.pop("_hyps", None)
        self._best_imgs.clear()
        self._merge_fragments()
        if self.bank_dump_dir:
            import pickle
            with open(self.bank_dump_dir / "static_text.pkl", "wb") as fh:
                pickle.dump(self.static_text, fh)

    # ------------------------------------------------------------------
    def _merge_fragments(self) -> None:
        """Plate-string-based track merging (spec 13, 'track fragmentation').
        Fragments of one vehicle (tracker id switches, duplicate boxes) get read
        separately: on the Delhi clip DL14CE5987 was a 0.97 read with only 2
        frames fused on one fragment and a wrong read on the 11-frame fragment.
        Records with near-identical plate strings (edit distance <= 1) whose
        vehicle boxes overlap in space (IoU >= 0.3) or follow each other in
        time (<= 3 s gap, IoU >= 0.2) are re-voted on the union of their
        hypotheses; every member receives the merged read, confidence and
        fused-frame count, and CONFIRMED is re-decided under the same floors."""
        import editdistance
        cand = [r for r in self.records if r.get("plate") and r.get("_hyps")]
        parent = {id(r): id(r) for r in cand}
        by_id = {id(r): r for r in cand}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # reading.merge_by_hypotheses: fragments of one vehicle rarely share their FINAL string (one
        # fragment's read is usually junk: 0 merges on the Delhi clip with 6+ split vehicles), but
        # they share near-identical valid hypotheses; within 3 s that is the same plate
        by_hyp = bool(self.rcfg.get("merge_by_hypotheses", False))
        tops = {id(r): _top_valid_texts(r["_hyps"]) for r in cand} if by_hyp else {}
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                a, b = cand[i], cand[j]
                gap = max(a["first_seen_pts_ms"], b["first_seen_pts_ms"]) - min(a["last_seen_pts_ms"], b["last_seen_pts_ms"])
                if by_hyp and gap <= 3000 and any(editdistance.eval(x, y) <= 1 for x in tops[id(a)] for y in tops[id(b)]):
                    parent[find(id(a))] = find(id(b))
                    continue
                if editdistance.eval(a["plate"], b["plate"]) > 1:
                    continue
                iou = _iou(tuple(a["vehicle_box"]), tuple(b["vehicle_box"]))
                overlap_in_time = gap <= 0
                if (overlap_in_time and iou >= 0.3) or (0 < gap <= 3000 and iou >= 0.2):
                    parent[find(id(a))] = find(id(b))
        groups: dict[int, list[dict]] = {}
        for r in cand:
            groups.setdefault(find(id(r)), []).append(r)
        ccfg, fcfg = self.cfg["confidence"], self.cfg["fusion"]
        from anpr.fuse.rover import ReadHypothesis
        for members in groups.values():
            if len(members) < 2:
                continue
            hyps = [ReadHypothesis(t, [p], w, src, sr) for m in members for (t, w, p, src, sr) in m["_hyps"]]
            fused = rover(hyps, self.state, ccfg["temperature"], ccfg["agreement_power"],
                          supported_only=bool(self.rcfg.get("supported_strings_only", False)))
            if not fused.plate or looks_like_overlay(fused.plate):
                continue
            n_used = sum(m["_n_used"] for m in members)
            n_agree = sum(m.get("_n_agree", 0) for m in members)
            best_w = max(m["_best_w"] for m in members)
            readers = _readers_supporting(hyps, fused.plate)
            if self.rcfg.get("string_vote", False):
                # pool the fragments' per-crop string votes and decide on them with the same rule as a
                # single track; re-voting with ROVER alone discarded that evidence (cam06: every
                # vote-confirmed plate fell back to CANDIDATE once fragments were merged)
                entries = [(w, t, p, set(src.split("/", 1)[1].split("+")), f"{m['track_id']}#{i}")
                           for m in members for i, (t, w, p, src, sr) in enumerate(m["_hyps"]) if src.startswith("vote/")]
                glyphs = max((int(m.get("glyphs", 0)) for m in members), default=0)
                sv = self._vote_pick(entries, fused, n_used, best_w, glyphs)
                if sv is not None:
                    fused, n_agree, readers = sv
            confirm_ok, why = self._decide(fused, n_used, n_agree, best_w, readers,
                                           max((int(m.get("glyphs", 0)) for m in members), default=0))
            ids = [m["track_id"] for m in members]
            for m in members:
                m.update(plate=fused.plate, confidence=round(float(fused.confidence), 4), alternates=fused.alternates,
                         agreement=round(fused.agreement, 3), n_hypotheses=fused.n_hyps, frames_fused=int(n_used),
                         frames_agreeing=int(n_agree), merged_from=ids, status="CONFIRMED" if confirm_ok else "CANDIDATE",
                         reason="" if confirm_ok else f"merged:{why}")
        for r in cand:
            r.pop("_hyps", None)
            r.pop("_best_w", None)
            r.pop("_n_used", None)
            r.pop("_n_agree", None)

    # ------------------------------------------------------------------
    def _gate(self, b: TrackBank) -> tuple[bool, str, dict]:
        g = self.cfg["legibility_gate"]
        best = b.best
        if best is None:
            return False, "no_plate_detected", {}
        q = best.quality
        stats = {"w": q.width_px, "sharp": q.sharpness_lap, "contrast": q.local_contrast, "q": q.quality_score,
                 "n_crops": len(b.crops)}
        if q.width_px < g["w_min_px"]:
            return False, f"width_below_gate:{q.width_px:.0f}<{g['w_min_px']}", stats
        if q.height_px < g.get("h_min_px", 8):
            return False, f"height_below_gate:{q.height_px:.0f}<{g.get('h_min_px', 8)}", stats
        if q.sharpness_lap < g["sharpness_min"]:
            return False, f"sharpness_below_gate:{q.sharpness_lap:.1f}<{g['sharpness_min']}", stats
        if q.local_contrast < g["contrast_min"]:
            return False, f"contrast_below_gate:{q.local_contrast:.1f}<{g['contrast_min']}", stats
        return True, "gated_in", stats

    def _finalise_track(self, b: TrackBank) -> None:
        self.timings.tracks_closed += 1
        if self.bank_dump_dir:
            import pickle
            with open(self.bank_dump_dir / f"{self._n_dumped:05d}_{b.track_id}.pkl", "wb") as fh:
                pickle.dump(b, fh)
            self._n_dumped += 1
        best = b.best
        rec = {
            "track_id": f"{self.camera_id}_{b.track_id}", "camera_id": self.camera_id,
            "first_seen_pts_ms": b.first_pts_ms, "last_seen_pts_ms": b.last_pts_ms,
            "first_frame": b.first_frame, "last_frame": b.last_frame,
            "status": "UNREADABLE", "plate": None, "confidence": 0.0, "alternates": [],
            "plate_class": "unknown", "vehicle_type": b.vehicle_type, "frames_fused": 0,
            "vehicle_box": [round(v, 1) for v in b.vehicle_box], "n_frames_seen": b.n_frames_seen,
            "n_plate_hits": b.n_plate_hits, "bbox": None, "reason": "",
        }
        if best is not None:
            rec["bbox"] = [round(v, 1) for v in best.box_frame]
            rec["best_frame"] = best.frame_idx
            rec["quality"] = best.quality.as_dict()
        ok, reason, stats = self._gate(b)
        # overlay-by-motion at track level: a "plate" that never moves while its
        # vehicle box does is burned-in text (timestamp / camera name), never a plate
        if ok and len(b.crops) >= 5:
            pc = np.array([[(c.box_frame[0] + c.box_frame[2]) / 2, (c.box_frame[1] + c.box_frame[3]) / 2] for c in b.crops])
            plate_motion = float(np.linalg.norm(pc.max(0) - pc.min(0)))
            # parked cars have static plates too: only reject when the VEHICLE moved and the plate did not
            if plate_motion < 3.0 and b.vehicle_motion_px() > 30.0:
                ok, reason = False, "static_overlay_text"
        # hoarding / sign board already learned from earlier vehicles at this frame position
        if ok and best is not None and self.static_text.is_static(best.box_frame, best.image):
            ok, reason = False, "static_scene_text"
        rec["gate"] = stats
        self.gate_log.append({"track": rec["track_id"], "ok": ok, "reason": reason, **stats})
        if not ok:
            rec["status"] = "UNREADABLE"
            rec["reason"] = reason
            self.records.append(rec)
            if self.evidence_dir and best is not None and self.write_candidates:
                write_evidence(self.evidence_dir / "unreadable", rec, best.image, None, None, None)
            return
        if not self.ensemble.readers:
            # ground-truth / detector-only runs: no reader loaded, skip B and C
            rec["status"] = "CANDIDATE"
            rec["reason"] = "no_reader_loaded"
            self.records.append(rec)
            return
        # ---------------- Stage B + C --------------------------------
        K = self.cfg["crop_bank"]["top_k"]
        pool = b.crops
        crop_reads = []
        want_reads = self.rcfg.get("read_filter", False) or self.rcfg.get("string_vote", False)
        if want_reads and self.filter_ensemble is not None:
            crop_reads = self._crop_reads(b.crops)
        if self.rcfg.get("read_filter", False) and crop_reads:
            # keep only crops that read as a registration on their own: the detector also boxes
            # headlamps, mirrors and whole bumper panels (sandbox cam06 auto GJ18X6705), and those
            # rank high on sharpness and out-vote the real plate in the fusion
            seen, ok = set(), []
            for c, *_ in crop_reads:
                if id(c) not in seen:
                    seen.add(id(c))
                    ok.append(c)
            if len(ok) >= min(3, len(b.crops)):
                pool = ok
        groups = [sorted(pool, key=TrackBank.rank_score, reverse=True)[:K]]
        if self.rcfg.get("cluster_by_layout", False):
            # One track's bank can hold boxes of different objects on the vehicle. Sandbox cam06
            # auto GJ18X6705: 46 two-row plate crops (19-55 px) and 8 wide 164-222 px boxes of
            # something else on the auto, which ranked higher on sharpness and filled 9 of the
            # top-12, so the plate was never read. Crops are grouped by box layout and every
            # group of >= 3 crops is read on its own; the strongest read wins.
            by: dict[bool, list] = {}
            for c in pool:
                by.setdefault(bool(c.two_row), []).append(c)
            groups = [sorted(cs, key=TrackBank.rank_score, reverse=True)[:K]
                      for cs in by.values() if len(cs) >= min(3, len(pool))] or groups
        reads = []
        for top in groups:
            aspect_two_row = sum(1 for c in top if c.two_row) > len(top) / 2
            # A squat detector box is no proof of a two-row plate: a tilted single-row plate gives one
            # too (Delhi clip t3: 17.6 deg skew, box aspect 1.76) and the row split then cut through
            # every character. With two_row_both_ways such a track is read both ways; the stronger wins.
            layouts = [aspect_two_row]
            if aspect_two_row and self.rcfg.get("two_row_both_ways", False):
                layouts.append(False)
            gbest = max(top, key=TrackBank.rank_score)
            reads += [(self._read_layout(top, tr, gbest), gbest) for tr in layouts]
        lr, best = max(reads, key=lambda rb: (bool(rb[0].fused.plate), rb[0].fused.confidence))
        # evidence, width floor and static-text check follow the crops that produced the read
        rec["bbox"] = [round(v, 1) for v in best.box_frame]
        rec["best_frame"] = best.frame_idx
        rec["quality"] = best.quality.as_dict()
        fused, hyps, n_used, fres_img, enh = lr.fused, lr.hyps, lr.n_used, lr.fres_img, lr.enh
        if self.rcfg.get("string_vote", False) and crop_reads:
            # temporal evidence: on the Delhi clip DL1LT1087 was read exactly on 20 single crops yet
            # the fused-image variants (weight 1.0 each vs 0.5 x quality for a single crop) won the
            # vote with DL14T1087; every good crop now reads and votes on its own
            # the vote's own decision (_vote_pick) runs the glyph floor too: counted here, once, or
            # that decision failed on every track and the pooled vote replaced the primary's string
            if self.rcfg.get("confirm_min_glyphs") or self.rcfg.get("secondary_confirm_min_glyphs"):
                rec["glyphs"] = track_glyphs(b.top_k(int(self.rcfg.get("glyph_crops", 8))))
            entries = [(TrackBank.rank_score(c) * p, t, p, rs, id(c)) for c, t, p, rs in crop_reads]
            sv = self._vote_pick(entries, fused, n_used, best.quality.width_px, int(rec.get("glyphs", 0)))
            if sv is not None:
                fused, n_vote, vote_readers = sv
                # for a vote read the evidence count is the crops that read EXACTLY the winner
                lr = dataclasses.replace(lr, fused=fused, n_agree=n_vote)
                hyps = list(hyps) + [ReadHypothesis(t, [p] * len(t), TrackBank.rank_score(c) * p, "vote/" + "+".join(sorted(rs)), False)
                                     for c, t, p, rs in crop_reads]
        ccfg = self.cfg["confidence"]
        rec["frames_fused"] = int(n_used)
        rec["frames_agreeing"] = int(lr.n_agree)
        rec["two_row"] = bool(lr.two_row)
        rec["reg_cc"] = [round(float(x), 3) for x in lr.reg_cc]
        rec["det_conf"] = round(float(best.det_conf), 3)
        from anpr.plate_grammar import score_string
        gs = score_string(fused.plate, self.state) if fused.plate else None
        # a full registration of a known state (what the report / video count as a usable read)
        rec["valid_format"] = bool(gs and gs.valid and len(fused.plate) >= 8
                                   and not any(x.startswith("unknown_state") for x in gs.reasons))
        rec["plate"] = fused.plate or None
        rec["confidence"] = round(float(fused.confidence), 4)
        rec["alternates"] = fused.alternates
        rec["agreement"] = round(fused.agreement, 3)
        rec["grammar_prior"] = round(fused.grammar_prior, 3)
        rec["n_hypotheses"] = fused.n_hyps
        rec["sr_disagree"] = fused.sr_disagree
        rec["per_char_conf"] = [round(x, 3) for x in fused.per_char_conf]
        # keep the hypotheses so fragments of the same vehicle can be re-voted together at flush()
        rec["_hyps"] = [(h.text, float(h.weight), float(np.mean(h.char_probs)) if h.char_probs else 0.0, h.source, bool(h.is_sr))
                        for h in hyps]
        rec["_best_w"] = best.quality.width_px if best is not None else 0.0
        rec["_n_used"] = int(n_used)
        rec["_n_agree"] = int(lr.n_agree)
        best_w = best.quality.width_px if best is not None else 0.0
        if "glyphs" not in rec and (self.rcfg.get("confirm_min_glyphs") or self.rcfg.get("secondary_confirm_min_glyphs")):
            rec["glyphs"] = track_glyphs(b.top_k(int(self.rcfg.get("glyph_crops", 8))))
        confirm_ok, why = self._decide(fused, n_used, lr.n_agree, best_w,
                                       _readers_supporting(hyps, fused.plate), rec.get("glyphs", 0))
        if not fused.plate:
            rec["status"] = "CANDIDATE"
            rec["reason"] = fused.reason or "no_valid_hypothesis"
        elif confirm_ok:
            rec["status"] = "CONFIRMED"
        else:
            rec["status"] = "CANDIDATE"
            rec["reason"] = why
        if best is not None:
            self._best_imgs[rec["track_id"]] = (best.box_frame, best.image)
        self.records.append(rec)
        if self.evidence_dir and (rec["status"] == "CONFIRMED" or self.write_candidates):
            sub = self.evidence_dir / rec["status"].lower()
            frame_img = self.frame_cache.get(best.frame_idx) if best is not None else None
            write_evidence(sub, rec, best.image if best else None, fres_img, enh, frame_img,
                           best.box_frame if best else None, fused.per_char_conf)

    # ------------------------------------------------------------------
    def _read_layout(self, top: list, two_row: bool, best) -> "_LayoutRead":
        """Stage B (rectify, register, fuse, enhance) + Stage C (read, ROVER) for one row layout."""
        t0 = time.perf_counter()
        rect, weights = [], []
        for c in top:
            if "rectify" in self.ablate:
                img = cv2.resize(c.image, (384, 92) if not two_row else (256, 128), interpolation=cv2.INTER_CUBIC)
            else:
                img = rectify(c.image, c.corners, two_row)
            rect.append(img)
            weights.append(max(c.quality.quality_score, 0.05))
        fcfg = self.cfg["fusion"]
        reg = "none" if "register" in self.ablate else fcfg["register"]
        if "fuse" in self.ablate:
            fres_img = cv2.cvtColor(rect[0], cv2.COLOR_BGR2GRAY)
            sr_mf = None
            n_used = 1
            reg_cc = []
        else:
            fr = fuse(rect, weights, fcfg["method"], reg, sr_scale=0 if "mfsr" in self.ablate else fcfg["sr_scale"])
            fres_img, sr_mf, n_used, reg_cc = fr.image, fr.sr_image, fr.n_used, fr.reg_cc
        enh = fres_img
        if "glare" not in self.ablate:
            enh = suppress_glare(enh)
        if "denoise" not in self.ablate:
            enh = self.denoiser(enh)
        if "deblur" not in self.ablate and best is not None:
            enh = self.deblurrer(enh, best.quality.blur_extent, best.quality.blur_angle_deg)
        enh, _ = deskew_residual(enh)
        # the fused variants are only as good as the frames registration kept: on handheld footage
        # ECC keeps 2 of 12 and the blurry fusion outvoted sharp single crops that read correctly
        fw = 1.0
        if self.rcfg.get("fused_weight_by_registration", False) and len(rect) > 1:
            fw = min(1.0, 0.3 + 0.7 * n_used / len(rect))
        variants = [Variant("fused_gray", fres_img, fw, two_row=two_row),
                    Variant("enhanced", enh, fw, two_row=two_row)]
        # No binarised variant in the vote: measured top-1 on 34 labelled legible tracks (feeds + street)
        # was 6 % exact / CER 0.72 vs 21-31 % for the other variants (reports/LOOP_LOG.md); it only diluted
        # agreement. Binarisation is still applied to the evidence crop for humans.
        if sr_mf is not None:
            variants.append(Variant("mfsr", sr_mf, 0.9 * fw, is_sr=False, two_row=two_row))
        if "sr" not in self.ablate:
            s = self.sr(enh)
            if s.learned:
                variants.append(Variant("sr", s.image, 0.8 * fw, is_sr=True, two_row=two_row))
        # top single crops as extra evidence (rectified, glare-suppressed)
        n_single = int(self.rcfg.get("single_crops", 3))
        sw = float(self.rcfg.get("single_weight", 0.5))
        # the colour crop goes to the whole-crop text reader only when the plate is wide enough for it
        # to help (Awiros: 3.4 % exact at 40 px, 0 % at 30 px on the far test) - it costs ~0.1 s a crop
        aw_min = float(self.rcfg.get("awiros_min_width", 0))
        for i, (img, w) in enumerate(list(zip(rect, weights))[:n_single]):
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            col = img if top[i].quality.width_px >= aw_min else None
            variants.append(Variant(f"single{i}", suppress_glare(g), sw * w, two_row=two_row, color=col))
            # adaptive photometric variants of the same crop (reading.single_variants); named
            # "single<i>~<op>" so they still count as ONE frame in _agreeing_frames
            vw = sw * w * float(self.rcfg.get("single_variant_weight", 0.5))
            for op, pg in _photometric_variants(g, self.rcfg.get("single_variants") or []):
                variants.append(Variant(f"single{i}~{op}", pg, vw, two_row=two_row))
        self.timings.enhance_ms += (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        hyps = self.ensemble.read(variants)
        ccfg = self.cfg["confidence"]
        fused = rover(hyps, self.state, ccfg["temperature"], ccfg["agreement_power"],
                      supported_only=bool(self.rcfg.get("supported_strings_only", False)))
        self.timings.read_ms += (time.perf_counter() - t1) * 1000
        if fused.plate and looks_like_overlay(fused.plate):
            fused.plate, fused.confidence = "", 0.0
            fused.reason = "overlay_text_rejected"
        return _LayoutRead(two_row, fused, hyps, int(n_used), list(reg_cc), fres_img, enh,
                           _agreeing_frames(hyps, fused.plate))

    @staticmethod
    def _vote_admissible(text: str, state: str, reject: Iterable[str] = ()) -> bool:
        """A crop's string may enter the vote only as a full registration (>= 8 chars) of a known
        state. reading.vote_reject lists further grammar reasons that bar it - e.g. a district
        number the state does not issue (DL81AP4175: Delhi has DL1-DL16; GJ40+: no such RTO)."""
        from anpr.plate_grammar import score_string
        if len(text) < 8:
            return False
        gs = score_string(text, state)
        bar = ("unknown_state", *reject)
        return gs.valid and not any(x.startswith(bar) for x in gs.reasons)

    def _crop_reads(self, crops: list) -> list[tuple]:
        """(crop, text, mean char prob) for every crop whose own quick CRNN read (each fast
        reader, top string; squat boxes tried as two rows and as one row) is a full registration
        of a known state (>= 8 chars) with mean character confidence >= reading.read_filter_min.
        One entry per (crop, reader) read that qualifies, the strongest per crop first."""
        thr = float(self.rcfg.get("read_filter_min", 0.5))
        reject = tuple(self.rcfg.get("vote_reject") or ())
        out = []
        for c in (crops if self.filter_ensemble is not None else []):
            best: dict[str, list] = {}
            for two in ((True, False) if c.two_row else (False,)):
                g = cv2.cvtColor(rectify(c.image, c.corners, two), cv2.COLOR_BGR2GRAY)
                for h in self.filter_ensemble.read([Variant("filter", g, 1.0, two_row=two)]):
                    if h.rank != 0 or len(h.text) < 8 or not h.char_probs:
                        continue
                    p = float(np.mean(h.char_probs))
                    if p >= thr and self._vote_admissible(h.text, self.state, reject):
                        e = best.setdefault(h.text, [0.0, set()])
                        e[0] = max(e[0], p)
                        # sources are 'variant/reader' or 'variant/reader/rows': the reader is the
                        # second part. The last part credited every per-row two-row read to a
                        # reader named 'rows', which the primary-only vote then left out
                        e[1].add(h.source.split("/")[1])
            for t, (p, rs) in sorted(best.items(), key=lambda kv: -kv[1][0]):
                out.append((c, t, p, frozenset(rs)))
        # reading.text_vote_crops: a whole-crop text reader (awiros, PP-OCRv5 on 558k Indian plates)
        # reads the k best crops and votes as a reader of its own, so reading.vote_any_reader can
        # confirm on it. It read DL1CW0942 exactly on 11 of 19 Delhi 4K crops where the primary
        # CRNN read DL1CX0942; k bounds the cost (about 0.1 s per crop on CPU)
        k = int(self.rcfg.get("text_vote_crops", 0))
        text_readers = [r for r in self.ensemble.readers if hasattr(r, "read_batch")]
        if k and text_readers and crops:
            top = sorted(crops, key=TrackBank.rank_score, reverse=True)[:k]
            # a squat box is read both ways here too: Delhi t69 (UP13AY3893) reads right on 5 of 20
            # crops as one row and on none as two rows
            jobs = [(c, two) for c in top for two in ((True, False) if c.two_row else (False,))]
            imgs = [rectify(c.image, c.corners, two) for c, two in jobs]
            for r in text_readers:
                best: dict[tuple, float] = {}
                for (c, _), (t, p) in zip(jobs, r.read_batch(imgs)):
                    if t and p >= thr and self._vote_admissible(t, self.state, reject):
                        best[(id(c), t)] = max(best.get((id(c), t), 0.0), float(p))
                by_id = {id(c): c for c in top}
                out += [(by_id[i], t, p, frozenset({r.name})) for (i, t), p in best.items()]
        return out

    def _readable_crops(self, crops: list) -> list:
        seen, out = set(), []
        for c, *_ in self._crop_reads(crops):
            if id(c) not in seen:
                seen.add(id(c))
                out.append(c)
        return out

    def _string_vote(self, reads: list[tuple], fused):
        """Temporal aggregation over individually read crops (reading.string_vote). Each qualifying
        crop read votes for its string with weight = crop rank score x read confidence; the
        heaviest string wins when >= 2 distinct crops read it. Returns (FusedRead, n_crops_agreeing)
        or None to keep the fused / ROVER read. Confidence = vote share x mean read confidence of
        the winning crops, so a string that is out-voted, or read by few or weak crops, stays low;
        the usual _decide guards (runner-up ratio, agreeing crops, width) still apply."""
        return self._vote_entries([(TrackBank.rank_score(c) * p, t, p, rs, id(c)) for c, t, p, rs in reads], fused)

    def _vote_pick(self, entries: list[tuple], fused, n_used: int, best_w: float, glyphs: int = 0):
        """The string vote's verdict for one track's crop reads or a merged group's pooled reads:
        (fused read, crops reading it exactly, readers), or None.

        reading.second_reader_mode: fallback - the primary reader votes alone and decides every
        confirm; a second reader in the same vote diluted its share (Delhi 4K: 9 -> 5 confirms).
        When the primary cannot decide, reading.vote_any_reader lets another reader's OWN vote carry
        the confirm under the same evidence rule, provided a second reader produced that string on
        some crop and no reader opposes it: the fallback reader's confident misreads (UP14DK7400,
        DL11T1087) were strings no other reader produced. Delhi 4K: the primary reads DL1CW0942 as
        DL1CX0942 on a 129 px plate where v6 reads it exactly on 13 crops. Otherwise the pooled
        vote supplies a displayed, never-confirmed read (string_vote_secondary)."""
        if not entries:
            return None
        if self.rcfg.get("second_reader_mode") != "fallback":
            return self._vote_entries(entries, fused)
        names = sorted({n for e in entries for n in e[3]})
        votes = {n: self._vote_entries([e for e in entries if n in e[3]], fused) for n in names}
        min_crops = int((self.rcfg.get("vote_confirm") or {}).get("min_crops", 4))

        def opposed(n: str, win: str) -> bool:
            # another reader's own vote carries a different string on a confirm's worth of crops and
            # that reader does not itself read `win` on as many: the readers disagree, and a vote
            # share cannot settle it. delhi_1080p t10 (true DL1LAB9684): v6 voted DL11AB9684 on 4
            # crops and read the primary's DL11AB3684 on 2. cam06 t39 (true GJ11CK1044): v6 voted
            # GJ11CE1044 on 7 crops but also read GJ11CK1044 on 4 - corroboration, not opposition
            def support(o: str) -> int:
                return len({e[4] for e in entries if o in e[3] and e[1] == win})

            def dropped_one(s: str) -> bool:
                # the other reader's string is `win` with one glyph missing: the text reader drops
                # characters (cam06 GJ18X6705 -> GJ18X705 on 11 crops, GJ11CK1044 -> GJ11CK044 on
                # 13) and that agrees with `win` on everything it did read
                return len(s) == len(win) - 1 and any(win[:i] + win[i + 1:] == s for i in range(len(win)))
            def rival(s: str) -> bool:
                # a competing reading of the same glyphs, not a reader failing on them: delhi t10's
                # DL11AB9684 against DL11AB3684 (1 edit) opposes; Delhi t448's DL1CW0723 against the
                # text reader's unanimous DL1CW0942 (3 edits, 5 of 46 v6 reads) does not
                import editdistance
                return s != win and editdistance.eval(s, win) <= 2 and not dropped_one(s)
            return any(o != n and votes[o] is not None and votes[o][1] >= min_crops and rival(votes[o][0].plate)
                       and support(o) < min_crops for o in names)

        sv = votes.get("crnn")
        if sv is not None and self._decide(sv[0], n_used, sv[1], best_w, sv[2], glyphs)[0]:
            # reading.vote_unopposed: delhi_1080p t10, a 55 px yellow plate DL1LAB9684, was CONFIRMED
            # as DL11AB3684 - the primary read that on 13 of 24 crops while v6 split 4 / 4 / 2 / 2
            # between four strings, two of them one edit from the truth. A misread the primary makes
            # consistently on a small plate looks exactly like agreement; a second reader that votes
            # for something else is the only evidence against it
            if self.rcfg.get("vote_unopposed") and opposed("crnn", sv[0].plate):
                return (dataclasses.replace(sv[0], reason="string_vote_contested"), sv[1], sv[2])
            return sv
        if self.rcfg.get("vote_any_reader"):
            for n in names:
                v = votes[n]
                if n == "crnn" or v is None or not self._decide(v[0], n_used, v[1], best_w, v[2], glyphs)[0]:
                    continue
                win = v[0].plate
                backed = any(e[1] == win and (e[3] - {n}) for e in entries)
                near = int(self.rcfg.get("vote_near_backing", 0))
                if not backed and near:
                    # reading.vote_near_backing: readers that fail on different characters never
                    # produce each other's exact string. Delhi t448 (DL1CW0942, 69 px): the text
                    # reader reads it on 8 of 8 crops, the primary reads DL1CW0542 - one glyph off,
                    # the 9/5 confusion it makes everywhere. Another reader's string one SUBSTITUTION
                    # away on `near` distinct crops backs the winner. Not an insertion: Delhi 4K t9
                    # (DL11SD3385) was confirmed as the text reader's DL11SD385 - a dropped glyph -
                    # "backed" by v6's DL11SD9385, which only says a glyph is there.
                    def one_sub(s: str) -> bool:
                        return len(s) == len(win) and sum(x != y for x, y in zip(s, win)) == 1
                    backed = len({e[4] for e in entries if (e[3] - {n}) and one_sub(e[1])}) >= near
                if backed and not opposed(n, win):
                    return v
        sv2 = self._vote_entries(entries, fused)
        if sv2 is not None and (sv is None or sv2[0].plate != sv[0].plate):
            return (dataclasses.replace(sv2[0], reason="string_vote_secondary"), sv2[1], sv2[2])
        return sv

    def _vote_entries(self, entries: list[tuple], fused):
        """String vote over (weight, text, read confidence, readers, crop key) entries - one track's
        crop reads, or the pooled reads of merged fragments."""
        import dataclasses
        w: dict[str, float] = {}
        crops_for: dict[str, set] = {}
        conf_for: dict[str, list] = {}
        readers_for: dict[str, set] = {}
        for wt, t, p, rs, key in entries:
            w[t] = w.get(t, 0.0) + wt
            crops_for.setdefault(t, set()).add(key)
            conf_for.setdefault(t, []).append(p)
            readers_for.setdefault(t, set()).update(rs)
        if not w:
            return None
        win = max(w, key=w.get)
        n = len(crops_for[win])
        if n < 2:
            return None
        total = sum(w.values())
        share = w[win] / total
        mean_p = float(np.mean(conf_for[win]))
        # evidence strength: the vote share, scaled by how unlikely n agreeing reads of confidence
        # mean_p all are misreads; alternates on the same scale, so the runner-up ratio compares weights
        strength = 1.0 - (1.0 - mean_p) ** n
        conf = share * strength
        alts = [{"plate": t, "confidence": round(w[t] / total * strength, 4)}
                for t in sorted(w, key=w.get, reverse=True) if t != win][:3]
        return dataclasses.replace(fused, plate=win, confidence=float(conf), alternates=alts, agreement=float(share),
                                   n_hyps=len(entries), per_char_conf=[float(share)] * len(win), supported=True,
                                   reason="string_vote"), n, readers_for[win]

    def _decide(self, fused, n_used: int, n_agree: int, best_w: float,
                readers: Optional[set] = None, glyphs: int = 0) -> tuple[bool, str]:
        """(confirm?, reason if not). One rule set for fresh tracks and merged fragments.

        `glyphs` is how many glyph-shaped components the track's best crop holds
        (anpr/detect/char_evidence.py): evidence from the pixels rather than from the reader."""
        ccfg, fcfg = self.cfg["confidence"], self.cfg["fusion"]
        # with several fast readers, a confirm needs the string from more than one of them: the v4
        # snapshot alone confirmed UP14DK7400 (true UP14DM7400) and DL11T1087 (true DL1LT1087) on the
        # Delhi clip, strings the other CRNN never produced
        n_fast = len(self.filter_ensemble.readers) if self.filter_ensemble is not None else 1
        need = int(self.rcfg.get("confirm_min_reader_agreement", 1))
        if readers is not None and n_fast >= 2 and len(readers) < min(need, n_fast):
            return False, "readers_disagree"
        # reading.confirm_require_primary: a second reader may add reads (it is stronger on two-row
        # and far plates) but a string only IT produced is never confirmed - its misreads were the
        # confident ones (UP14DK7400, DL11T1087); requiring full agreement instead cut the primary
        # reader's own confirms on the Delhi clip from 9 to 5
        if self.rcfg.get("confirm_require_primary") and readers is not None and n_fast >= 2 and "crnn" not in readers:
            return False, "primary_reader_disagrees"
        # a confirmation asserts a registration exists, so the pixels must show a row of glyphs.
        # delhi_1080p s0_t10 was CONFIRMED as DL11AB3684 off a truck windscreen: 12 crops of glass,
        # alternates one character apart, nothing to read (reports/LOOP_LOG.md H16)
        min_glyphs = int(self.rcfg.get("confirm_min_glyphs", 0))
        if min_glyphs and glyphs < min_glyphs:
            return False, f"no_glyph_evidence:{glyphs}"
        if getattr(fused, "reason", "") == "string_vote_contested":
            # reading.vote_unopposed: another reader's own vote went to a different string (_vote_pick)
            return False, "vote_contested"
        if getattr(fused, "reason", "") == "string_vote_secondary":
            # normally never confirmed: the fallback reader's misreads were the confident ones
            # (UP14DK7400, DL11T1087). reading.secondary_confirm_min_glyphs lets a legible crop
            # speak for it - the string still has to pass the same vote evidence rule below.
            need = int(self.rcfg.get("secondary_confirm_min_glyphs", 0))
            if not need or glyphs < need:
                return False, "second_reader_only"
        vc = self.rcfg.get("vote_confirm")
        if vc and getattr(fused, "reason", "") in ("string_vote", "string_vote_secondary"):
            # evidence rule for a temporal string vote (reading.vote_confirm): enough distinct crops
            # read EXACTLY this string, it holds enough of the vote, and the runner-up is well behind
            top_alt = fused.alternates[0]["confidence"] if fused.alternates else 0.0
            for ok, why in ((n_agree >= int(vc.get("min_crops", 4)), "too_few_agreeing_frames"),
                            (fused.agreement >= float(vc.get("min_share", 0.4)), "low_vote_share"),
                            (best_w >= ccfg.get("confirm_min_width_px", 40), "below_confirm_width"),
                            (top_alt < float(self.rcfg.get("confirm_max_alt_ratio", 0.5)) * max(fused.confidence, 1e-9),
                             "close_alternate")):
                if not ok:
                    return False, why
            return True, ""
        # every character must individually win its vote: on sandbox cam06 a plate read with one
        # undecided digit (6 vs 8 at 0.24) reached 0.75 overall and would have been a false confirm
        min_char = min(fused.per_char_conf) if fused.per_char_conf else 0.0
        frames = n_used
        if self.rcfg.get("count_agreeing_frames", False):
            # single frames whose own top read is (within one character) the winner are evidence
            # too, not only the frames registration kept: handheld Delhi t179 fused 2 of 12 frames
            frames = max(n_used, n_agree)
        alt_ratio = self.rcfg.get("confirm_max_alt_ratio")
        top_alt = fused.alternates[0]["confidence"] if fused.alternates else 0.0
        checks = (
            (fused.confidence >= ccfg["confirm_threshold"], "low_confidence"),
            (best_w >= ccfg.get("confirm_min_width_px", 40), "below_confirm_width"),
            (fused.n_hyps >= ccfg.get("confirm_min_hypotheses", 6), "too_few_hypotheses"),
            (min_char >= ccfg.get("confirm_min_char_vote", 0.5), "undecided_character"),
            (frames >= fcfg["min_frames_for_confirm"], "too_few_frames"),
            # a runner-up close behind the winner is a coin toss, not a read: the Delhi-clip
            # false confirm DL11T1087 (0.83) had the true DL1LT1087 right behind it (0.52)
            (alt_ratio is None or top_alt < alt_ratio * fused.confidence, "close_alternate"),
            # independent single frames must back the read on their own: sandbox cam06 GJ11EJ7578
            # (true series 'CJ' by eye) confirmed at 0.92 from the fused variants with 1 agreeing frame
            (n_agree >= int(self.rcfg.get("confirm_min_agreeing_singles", 0)), "too_few_agreeing_frames"),
            (fused.supported or not self.rcfg.get("supported_strings_only", False), "unsupported_string"),
        )
        for ok, why in checks:
            if not ok:
                return False, why
        return True, ""

    # ------------------------------------------------------------------
    def dump(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"camera_id": self.camera_id, "records": self.records, "timings": self.timings.as_dict(),
                       "gate_log": self.gate_log, "rejections": self.plates.rejection_log[:2000],
                       "static_scene_text": {"boxes": [[round(v, 1) for v in b] for b in self.static_text.static_boxes],
                                             "candidates_rejected": self.static_text.n_rejected}}, fh, indent=1)
