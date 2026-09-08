"""The ANPR pipeline: detection -> restoration -> OCR -> consensus.

The interesting part of this module is the **OCR scheduler**.  Detection is
cheap and scales fine with vehicle count; OCR does not.  Reading every plate
in every frame would collapse the moment a junction fills up, so instead each
frame gets a fixed OCR budget that is spent on the plates that will benefit
most:

* tracks already locked to a confident answer are skipped entirely, which
  frees the budget for vehicles that just entered the scene,
* a crop clearly worse than the best already seen for that track is skipped,
* whatever remains is sorted by need x crop quality and the top N are read.

The result degrades gracefully: with more vehicles than budget, every vehicle
still gets read, just over more frames.
"""
from __future__ import annotations

import os

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import enhance
from . import layout as lay
from .config import Config
from .consensus import ConsensusStore, TrackConsensus, supersedes
from .detect import Box, Detector, PlateDetection
from .metrics import Metrics
from .osd import OsdSuppressor
from .ocr import OcrEnsemble
from .readability import ReadabilityLedger

log = logging.getLogger(__name__)


@dataclass
class PlateEvent:
    """A plate reading surfaced to the UI."""
    track_id: int
    text: str
    pretty: str
    score: float
    confidence: float
    valid: bool
    fmt: str | None
    state: str | None
    confirmed: bool
    observations: int
    box: dict
    quality: dict
    frame: int
    timestamp: float
    method: str = ""
    is_new: bool = False
    trace: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "track_id": self.track_id, "text": self.text, "pretty": self.pretty,
            "score": round(self.score, 4), "confidence": round(self.confidence, 4),
            "valid": self.valid, "format": self.fmt, "state": self.state,
            "confirmed": self.confirmed, "observations": self.observations,
            "box": self.box, "quality": self.quality, "frame": self.frame,
            "timestamp": self.timestamp, "method": self.method,
            "is_new": self.is_new, "trace": self.trace,
        }


#: How much to discount a reading taken from a plate touching the frame edge.
EDGE_QUALITY_PENALTY = 0.45
#: Pixels from the frame edge still counted as "touching".
EDGE_MARGIN = 3
#: Frames of separation within which two same-plate tracks are treated as one
#: fragmented track rather than two passes of the same vehicle.
#:
#: Deliberately counted in *video frames*, not wall-clock seconds: offline
#: processing runs slower than real time, so two fragments of one vehicle can
#: be many wall seconds apart while being only a frame or two apart in the
#: footage.  Using wall time here silently disables the merge.
TRACK_MERGE_GAP = 60


def _touches_border(box: Box, width: int, height: int) -> bool:
    """True when the plate box runs into the edge of the frame."""
    return (box.x1 <= EDGE_MARGIN or box.y1 <= EDGE_MARGIN
            or box.x2 >= width - EDGE_MARGIN or box.y2 >= height - EDGE_MARGIN)


@dataclass
class FrameResult:
    frame_idx: int
    vehicles: list[Box]
    plates: list[PlateDetection]
    events: list[PlateEvent]
    stats: dict
    #: best crop per track seen this frame, for the UI thumbnail strip
    crops: dict[int, np.ndarray] = field(default_factory=dict)


class AnprPipeline:
    """Stateful, single-stream ANPR.  One instance per video or camera."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.detector = Detector(cfg.detect)
        self.ocr = OcrEnsemble(cfg.ocr)
        self.sr = enhance.SuperResolver(cfg.enhance.sr_backend, cfg.enhance.sr_scale)
        # Multi-frame upscaler: fuses the best few crops of a track into one
        # sharper plate before a second read. Optional; None when absent.
        self.mfsr = None
        if cfg.enhance.mfsr_model and cfg.enhance.sr_backend != "off":
            try:
                from .config import resolve_model
                from .sr import MultiFrameUpscaler
                path = resolve_model(cfg.enhance.mfsr_model)
                if os.path.exists(path):
                    self.mfsr = MultiFrameUpscaler(path, "cpu")
                    log.info("multi-frame super-resolution: %s", self.mfsr.name)
                else:
                    log.info("no %s in models/; multi-frame SR off", cfg.enhance.mfsr_model)
            except Exception as exc:                        # noqa: BLE001 - optional
                log.warning("multi-frame SR unavailable: %s", exc)
        self.tracks = ConsensusStore(cfg.consensus)
        # Learns each camera's burned-in overlays (clocks, captions) live and
        # keeps them out of the plate vote - see anpr/osd.py.
        self.osd = OsdSuppressor()
        # What the plates on this feed physically looked like, so the
        # camera can say "unreadable" instead of saying nothing.
        self.readability = ReadabilityLedger(floor_px=float(self.cfg.ocr.min_plate_width))
        # Per-stage timings as percentiles - see anpr/metrics.py for why
        # an average is the wrong summary here.
        self.metrics = Metrics()

        self.frame_idx = 0
        #: track -> (text, confirmed) last pushed to the UI
        self._announced: dict[int, tuple[str, bool]] = {}
        self._best_crop: dict[int, tuple[float, np.ndarray]] = {}
        self._fuse_crops: dict[int, list[tuple[float, np.ndarray]]] = {}
        self._times: deque[float] = deque(maxlen=60)
        self._ocr_calls = 0
        self._skipped = 0

    # -- lifecycle -------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.detector.ready

    def reset(self) -> None:
        self.detector.reset()
        self.tracks.reset()
        self.osd.reset()
        self.readability.reset()
        self.metrics.reset()
        self.frame_idx = 0
        self._announced.clear()
        self._best_crop.clear()
        self._fuse_crops.clear()
        self._times.clear()
        self._ocr_calls = self._skipped = 0

    def on_discontinuity(self) -> None:
        """Recover from an abrupt scene cut.

        A looping recording restarts with a completely different scene, which
        is indistinguishable from a camera reboot.  Every track id, motion
        model and accumulated verdict from before the cut describes vehicles
        that are no longer there - carried across, they attach old plates to
        new cars and invent journeys that never happened.  Confirmed results
        are already recorded downstream, so only the live state is dropped.
        """
        log.info("scene discontinuity - resetting track state")
        self.detector.reset()
        self.tracks.reset()
        self.readability.drop_tracks()
        self._announced.clear()
        self._best_crop.clear()
        self._fuse_crops.clear()

    def warmup(self, size: tuple[int, int] = (720, 1280)) -> None:
        """Run one throwaway frame so CUDA kernels are compiled up front.

        Without this the first real frame takes several seconds and the UI
        looks broken on startup.
        """
        blank = np.zeros((size[0], size[1], 3), np.uint8)
        try:
            self.detector.process(blank, -1)
        except Exception as exc:                # noqa: BLE001
            log.debug("warmup failed harmlessly: %s", exc)
        self.detector.reset()

    # -- per frame -------------------------------------------------------
    def process_frame(self, frame: np.ndarray,
                      timestamp: float | None = None) -> FrameResult:
        """Process one frame.

        *timestamp* is the wall-clock time the frame was **captured**, taken
        from the stream's PTS.  Pass it whenever the source can supply it:
        route reconstruction across cameras is only as good as this number,
        and processing time is not a substitute for capture time.
        """
        started = time.perf_counter()
        captured = time.time() if timestamp is None else timestamp
        idx = self.frame_idx
        self.frame_idx += 1

        # Vehicles whose plate is already settled do not need the ROI pass.
        # Confirmed is a low enough bar here: re-detecting a plate we have
        # already agreed on across several frames buys nothing, and on a busy
        # 4K junction the ROI pass is the single largest cost in the frame.
        settled = {tid for tid, tc in self.tracks.tracks.items()
                   if tc.verdict.confirmed}
        with self.metrics.time("detect"):
            vehicles, plates = self.detector.process(frame, idx, settled)

        # --- measure every crop, then decide where to spend OCR ----------
        height, width = frame.shape[:2]
        scored: list[tuple[float, PlateDetection, enhance.Quality, TrackConsensus]] = []
        for det in plates:
            if det.crop is None or det.crop.size == 0:
                continue
            box = (det.box.x1, det.box.y1, det.box.x2, det.box.y2)
            # Every candidate teaches the overlay detector where the detector
            # fires; a plate already CONFIRMED on this track rescues its
            # position from ever being masked.
            self.osd.observe(box, confirmed=self.tracks.get(det.track_id).verdict.confirmed)
            # A candidate sitting in a learned overlay region is the camera's
            # own clock or caption, not a plate. Dropped before it can cost OCR
            # or enter the vote.
            if self.osd.is_overlay(box):
                self._skipped += 1
                continue
            # Is there enough resolution here for a reading to mean
            # anything? Below the floor the characters are fewer than four
            # pixels wide and every read measured on this estate came back
            # invented rather than wrong - see anpr/readability.py.
            if not self.readability.observe(det.track_id, det.box.w):
                self._skipped += 1
                continue
            with self.metrics.time("assess"):
                q = enhance.assess(det.crop)
            # A plate touching the frame edge is probably only partly in
            # shot, and a half-visible plate reads as a *shorter* plate that
            # can still satisfy a legal format.  Discounting these keeps the
            # vote dominated by frames where the whole plate was visible.
            if _touches_border(det.box, width, height):
                q.score *= EDGE_QUALITY_PENALTY
            tc = self.tracks.get(det.track_id)
            self._remember_crop(det.track_id, q.score, det.crop)
            if not tc.wants_ocr(idx, q.score, self.cfg.scheduler):
                self._skipped += 1
                continue
            scored.append((tc.priority(q.score), det, q, tc))

        self.osd.advance()

        scored.sort(key=lambda t: t[0], reverse=True)
        budget = max(1, self.cfg.scheduler.max_ocr_per_frame)

        events: list[PlateEvent] = []
        fallbacks_left = self.cfg.ocr.max_fallback_per_frame
        for _prio, det, q, tc in scored[:budget]:
            with self.metrics.time("enhance"):
                variants = enhance.build_variants(det.crop, self.cfg.enhance,
                                                  self.sr, q)
            if not variants:
                continue
            # The detection escalation is the most expensive thing the OCR
            # layer can do.  It is worth it for a stacked plate with real
            # pixels, and worth nothing for a distant smear - so spend it
            # only on crops big enough to rescue, and only a few per frame.
            allow_fallback = (fallbacks_left > 0
                              and q.width >= self.cfg.ocr.fallback_min_width)
            with self.metrics.time("ocr"):
                result = self.ocr.read(variants, allow_fallback=allow_fallback)
            self.readability.note_read(det.track_id)
            if allow_fallback:
                fallbacks_left -= 1
            self._ocr_calls += 1
            if not result.candidates:
                continue
            candidates = list(result.candidates)
            fused = self._fused_reading(det.track_id)
            if fused is not None:
                candidates.append(fused)
            candidates.extend(self._multiframe_readings(det.track_id))
            # Classified here rather than inside the OCR layer so the track
            # gets the verdict even when every reading was rejected: a crop
            # that produced no legal plate still told us the plate's shape,
            # and that is a vote worth keeping for the frames that follow.
            tc.observe(candidates, q.score, idx, layout=lay.classify(det.crop))
            tc.last_seen = captured
            tc.last_capture = captured
            if tc.first_capture is None:
                tc.first_capture = captured

            v = tc.verdict
            if not v.text:
                continue
            # Surface a track when its answer first appears, when the text
            # changes, or when it crosses into confirmed.  A steady reading
            # must not spam the feed every frame - but the pending->confirmed
            # transition has to get through even though the text is identical,
            # otherwise the UI never turns the plate green.
            previous = self._announced.get(det.track_id)
            current = (v.text, v.confirmed)
            if previous == current:
                continue
            # Do not confirm a reading that is a truncation of one another
            # track already confirmed - that is one vehicle whose track
            # fragmented, not two vehicles.
            if v.confirmed and self._is_duplicate(det.track_id, v.text):
                continue
            self._announced[det.track_id] = current
            events.append(PlateEvent(
                track_id=det.track_id, text=v.text, pretty=v.pretty,
                score=v.score, confidence=v.confidence, valid=v.valid,
                fmt=v.fmt, state=v.state, confirmed=v.confirmed,
                observations=v.observations, box=det.box.as_dict(),
                quality=q.as_dict(), frame=idx, timestamp=captured,
                method=v.method, is_new=previous is None,
                trace=result.trace[:8],
            ))

        # --- retire tracks that have left ---------------------------------
        alive = {d.track_id for d in plates} | {
            v.track_id for v in vehicles if v.track_id is not None}
        for tc in self.tracks.retire_missing(alive, idx, now=captured):
            v = tc.verdict
            # Every departing vehicle gets a reason, including the ones
            # that produced no plate at all. Silence is what this replaces.
            self.readability.retire(tc.track_id, confirmed=bool(v.text and v.confirmed))
            if v.text and v.confirmed and self._announced.get(tc.track_id) != (v.text, True):
                self._announced[tc.track_id] = (v.text, True)
                events.append(PlateEvent(
                    track_id=tc.track_id, text=v.text, pretty=v.pretty,
                    score=v.score, confidence=v.confidence, valid=v.valid,
                    fmt=v.fmt, state=v.state, confirmed=True,
                    observations=v.observations, box={}, quality={},
                    frame=idx, timestamp=captured, method=v.method))

        # `retire_missing` only hands back tracks that produced a reading, so
        # a vehicle whose plate was never even attempted would never reach the
        # loop above - and those are exactly the UNREADABLE ones this ledger
        # exists to count. Sweep for anything the consensus store has dropped.
        self.readability.settle_absent(set(self.tracks.tracks))

        elapsed = time.perf_counter() - started
        self._times.append(elapsed)
        self.metrics.record("frame", elapsed * 1000.0)
        return FrameResult(idx, vehicles, plates, events, self.stats(),
                           {tid: c for tid, (_, c) in self._best_crop.items()})

    def _is_duplicate(self, track_id: int, text: str) -> bool:
        """True when another track has already confirmed a fuller reading."""
        for other, (seen, confirmed) in self._announced.items():
            if other != track_id and confirmed and supersedes(seen, text):
                return True
        return False

    def _remember_crop(self, track_id: int, quality: float, crop: np.ndarray) -> None:
        prev = self._best_crop.get(track_id)
        if prev is None or quality > prev[0]:
            self._best_crop[track_id] = (quality, crop.copy())

        # Logit fusion needs several looks at the same plate, so the best few
        # are kept rather than only the winner. Sorted by quality and capped,
        # because a track that lingers for a minute must not accumulate a
        # hundred crops - and the worst of them would only dilute the sum.
        pool = self._fuse_crops.setdefault(track_id, [])
        pool.append((quality, crop.copy()))
        if len(pool) > self.cfg.ocr.fuse_frames:
            pool.sort(key=lambda entry: entry[0], reverse=True)
            del pool[self.cfg.ocr.fuse_frames:]

    def _fused_reading(self, track_id: int) -> "pr.PlateCandidate | None":
        """One decode over every kept frame of a track, or None.

        Runs only when the track has enough independent looks to be worth it,
        and never replaces the per-frame observations - it is added alongside
        them, so a fused reading has to win the same consensus every other
        reading does rather than being trusted because of how it was produced.
        """
        if not self.cfg.ocr.fuse_track_logits:
            return None
        pool = self._fuse_crops.get(track_id) or []
        if len(pool) < self.cfg.ocr.fuse_min_frames:
            return None
        engine = getattr(self.ocr, "fusion_engine", None)
        if engine is None:
            return None
        crops = [crop for _quality, crop in sorted(pool, key=lambda e: e[0], reverse=True)]
        reading = engine.read_track_fused(crops)
        if reading is None:
            return None
        text, confidence = reading
        return pr.normalise(text, confidence, engine=f"{engine.name}-fused", variant="track")

    def _multiframe_readings(self, track_id: int) -> "list[pr.PlateCandidate]":
        """Fuse the track's kept crops into one upscaled plate and read it.

        The single-frame path reads each crop as it comes; this path waits
        until the track has a few looks, registers them, and lets the
        multi-frame upscaler combine them - noise and blocking average out,
        sub-pixel shifts between frames add detail no single frame has. The
        result goes through the same variants, engines and grammar as any
        crop, tagged so the consensus can tell where it came from, and it
        wins nothing by construction: it is one more vote.
        """
        if self.mfsr is None:
            return []
        pool = self._fuse_crops.get(track_id) or []
        if len(pool) < self.cfg.ocr.fuse_min_frames:
            return []
        crops = [crop for _q, crop in sorted(pool, key=lambda e: e[0], reverse=True)]
        try:
            fused = self.mfsr.upscale(crops[: self.cfg.ocr.fuse_frames])
        except Exception as exc:                            # noqa: BLE001
            log.debug("multi-frame SR failed: %s", exc)
            return []
        if fused is None or fused.size == 0:
            return []
        # Already upscaled: build the variants without a second SR pass.
        variants = enhance.build_variants(fused, self.cfg.enhance, sr=None)
        if not variants:
            return []
        result = self.ocr.read(variants, allow_fallback=False)
        out = []
        for cand in result.candidates:
            cand.engine = f"{cand.engine}-mfsr"
            cand.variant = f"track-{cand.variant}"
            out.append(cand)
        return out

    def best_crop(self, track_id: int) -> np.ndarray | None:
        entry = self._best_crop.get(track_id)
        return entry[1] if entry else None

    # -- reporting -------------------------------------------------------
    def stats(self) -> dict:
        avg = sum(self._times) / len(self._times) if self._times else 0.0
        confirmed = sum(1 for t in self.tracks.all_tracks() if t.verdict.confirmed)
        return {
            "frame": self.frame_idx,
            "fps": round(1.0 / avg, 1) if avg > 0 else 0.0,
            "latency_ms": round(avg * 1000, 1),
            "active_tracks": len(self.tracks.tracks),
            "total_tracks": len(self.tracks.all_tracks()),
            "confirmed": confirmed,
            "ocr_calls": self._ocr_calls,
            "ocr_skipped": self._skipped,
            "engines": self.ocr.engine_names,
        }

    def report(self) -> dict:
        """The full picture, for a periodic log line or an operator report.

        Separate from `stats()` because `stats()` rides on every FrameResult
        and this does not: computing percentiles sorts each stage's window, so
        doing it per frame would put the measurement inside the thing being
        measured.
        """
        return {
            **self.stats(),
            # Why this camera produced what it produced. An empty plate list
            # with `unreadable` high is a camera placement finding, not a
            # pipeline failure, and the two must not look alike.
            "readability": self.readability.describe(),
            "timings": self.metrics.describe(),
        }

    def results(self) -> list[dict]:
        """Every plate seen so far, best first - used for CSV/JSON export.

        Readings that are a one-character truncation of a stronger reading are
        dropped: they are the same vehicle seen through a fragmented track,
        not a second vehicle.
        """
        out = []
        for tc in self.tracks.all_tracks():
            v = tc.verdict
            if not v.text:
                continue
            out.append({
                "track_id": tc.track_id, **v.as_dict(),
                "first_seen": tc.first_seen, "last_seen": tc.last_seen,
                "first_frame": tc.first_frame, "last_frame": tc.last_frame,
            })
        out.sort(key=lambda r: (r["confirmed"], r["score"]), reverse=True)

        # Compare against every row, not just the ones already kept: a
        # truncation can outscore the full reading it came from, in which case
        # an order-dependent pass would keep the truncation and drop nothing.
        texts = [r["text"] for r in out]
        out = [row for i, row in enumerate(out)
               if not any(supersedes(t, row["text"])
                          for j, t in enumerate(texts) if j != i)]

        # Two tracks reading the same plate while both were on screen are one
        # vehicle whose track fragmented, not two vehicles.  Overlapping
        # lifetimes are the test - the same vehicle genuinely passing twice
        # later in a long session must still be reported twice.
        merged: list[dict] = []
        for row in out:
            twin = next(
                (m for m in merged
                 if m["text"] == row["text"]
                 and row["first_frame"] <= m["last_frame"] + TRACK_MERGE_GAP
                 and m["first_frame"] <= row["last_frame"] + TRACK_MERGE_GAP),
                None)
            if twin is None:
                merged.append(row)
                continue
            twin["observations"] += row["observations"]
            twin["first_seen"] = min(twin["first_seen"], row["first_seen"])
            twin["last_seen"] = max(twin["last_seen"], row["last_seen"])
            twin["first_frame"] = min(twin["first_frame"], row["first_frame"])
            twin["last_frame"] = max(twin["last_frame"], row["last_frame"])
        return merged


# --------------------------------------------------------------------------
# Annotation
# --------------------------------------------------------------------------

COLOR_CONFIRMED = (86, 219, 127)     # green
COLOR_PENDING = (60, 190, 245)       # amber
COLOR_UNREAD = (140, 140, 140)       # grey
COLOR_VEHICLE = (95, 80, 70)         # muted blue-grey


def annotate(frame: np.ndarray, result: FrameResult,
             pipeline: AnprPipeline, show_vehicles: bool = True) -> np.ndarray:
    """Draw boxes and current verdicts onto a copy of the frame."""
    out = frame.copy()

    if show_vehicles:
        for v in result.vehicles:
            x1, y1, x2, y2 = v.as_int()
            cv2.rectangle(out, (x1, y1), (x2, y2), COLOR_VEHICLE, 1)
            if v.track_id is not None:
                cv2.putText(out, f"#{v.track_id}", (x1 + 2, max(12, y1 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, COLOR_VEHICLE, 1,
                            cv2.LINE_AA)

    for det in result.plates:
        tc = pipeline.tracks.tracks.get(det.track_id)
        verdict = tc.verdict if tc else None
        if verdict and verdict.confirmed:
            color, label = COLOR_CONFIRMED, verdict.pretty or verdict.text
        elif verdict and verdict.text:
            color, label = COLOR_PENDING, f"{verdict.pretty or verdict.text}?"
        else:
            color, label = COLOR_UNREAD, "reading..."

        x1, y1, x2, y2 = det.box.as_int()
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        # Label sits above the plate, or below it when there is no room.
        scale = 0.62
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        ty = y1 - 8 if y1 - th - 12 > 0 else y2 + th + 10
        bx1, by1 = x1, ty - th - 6
        cv2.rectangle(out, (bx1, by1), (bx1 + tw + 12, ty + 6), color, -1)
        cv2.putText(out, label, (bx1 + 6, ty), cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (18, 18, 18), 2, cv2.LINE_AA)

        if verdict and verdict.observations:
            meta = f"{int(verdict.score*100)}% x{verdict.observations}"
            cv2.putText(out, meta, (x1, y2 + 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, color, 1, cv2.LINE_AA)

    return out
