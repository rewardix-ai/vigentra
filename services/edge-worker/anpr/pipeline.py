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

import logging
import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import enhance
from .config import Config
from .consensus import ConsensusStore, TrackConsensus, supersedes
from .detect import Box, Detector, PlateDetection
from .ocr import OcrEnsemble

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
        self.tracks = ConsensusStore(cfg.consensus)

        self.frame_idx = 0
        #: track -> (text, confirmed) last pushed to the UI
        self._announced: dict[int, tuple[str, bool]] = {}
        self._best_crop: dict[int, tuple[float, np.ndarray]] = {}
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
        self.frame_idx = 0
        self._announced.clear()
        self._best_crop.clear()
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
        self._announced.clear()
        self._best_crop.clear()

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
        vehicles, plates = self.detector.process(frame, idx, settled)

        # --- measure every crop, then decide where to spend OCR ----------
        height, width = frame.shape[:2]
        scored: list[tuple[float, PlateDetection, enhance.Quality, TrackConsensus]] = []
        for det in plates:
            if det.crop is None or det.crop.size == 0:
                continue
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

        scored.sort(key=lambda t: t[0], reverse=True)
        budget = max(1, self.cfg.scheduler.max_ocr_per_frame)

        events: list[PlateEvent] = []
        fallbacks_left = self.cfg.ocr.max_fallback_per_frame
        for _prio, det, q, tc in scored[:budget]:
            variants = enhance.build_variants(det.crop, self.cfg.enhance, self.sr, q)
            if not variants:
                continue
            # The detection escalation is the most expensive thing the OCR
            # layer can do.  It is worth it for a stacked plate with real
            # pixels, and worth nothing for a distant smear - so spend it
            # only on crops big enough to rescue, and only a few per frame.
            allow_fallback = (fallbacks_left > 0
                              and q.width >= self.cfg.ocr.fallback_min_width)
            result = self.ocr.read(variants, allow_fallback=allow_fallback)
            if allow_fallback:
                fallbacks_left -= 1
            self._ocr_calls += 1
            if not result.candidates:
                continue
            tc.observe(result.candidates, q.score, idx)
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
            if v.text and v.confirmed and self._announced.get(tc.track_id) != (v.text, True):
                self._announced[tc.track_id] = (v.text, True)
                events.append(PlateEvent(
                    track_id=tc.track_id, text=v.text, pretty=v.pretty,
                    score=v.score, confidence=v.confidence, valid=v.valid,
                    fmt=v.fmt, state=v.state, confirmed=True,
                    observations=v.observations, box={}, quality={},
                    frame=idx, timestamp=captured, method=v.method))

        self._times.append(time.perf_counter() - started)
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
