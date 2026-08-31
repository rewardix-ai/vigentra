"""The consensus ANPR engine, wired into the edge worker.

The worker's original plate path read OCR off one crop from one frame and kept
the answer if it parsed. On wide street footage that yielded roughly one plate
per sixty-seven vehicles, and the plates it did produce were single-frame
guesses — a format-valid misread is indistinguishable from a correct read when
you have only seen the vehicle once.

This module replaces that path with the `anpr` package vendored alongside it,
which is stateful per camera and answers a different question. Instead of "what
does this crop say?", it asks "what does this *vehicle* say, across every frame
it appeared in?" — tracking each vehicle, reading its plate many times through
several restoration variants, repairing each reading against the Indian plate
grammar, and taking a per-track vote.

The two layers doing the work are the grammar engine and the consensus vote,
not the OCR model. That matters for what this module emits: a plate arrives
here already voted on, with the number of frames that agreed, so the central
API can be told how strong the reading is instead of being handed a bare string.

Three consequences for the caller:

**Frames must arrive in order, from one camera, into one engine.** Consensus is
per track and a track is per stream. Sharing an engine between cameras would
merge two junctions into one vehicle history.

**Timestamps must be PTS, not arrival time.** The engine takes the capture
instant as an argument and threads it onto every event. Route reconstruction
across cameras is only as good as that number, and the grid's integrator guide
is explicit that arrival time is not a substitute.

**Plates are emitted once per track, not once per frame.** A vehicle read forty
times is one sighting the network can act on, not forty.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .detectors import COCO_TO_CANONICAL, Detection, DetectorError

logger = logging.getLogger("sentinel.edge.anpr")

#: The vendored engine sits beside `app/`, not inside it.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

#: Vehicle classes the engine reads plates from. A person is never cropped.
PLATE_BEARING_CLASSES = frozenset({"car", "motorcycle", "bus", "truck", "auto-rickshaw"})

#: Below this, a voted reading is not sent at all.
#:
#: Higher than the single-frame reader's floor on purpose. A consensus score is
#: not the same quantity as an OCR score - it already accounts for agreement
#: across frames, so a low one means the frames genuinely disagreed, which is
#: the case most likely to be a wrong plate that happens to parse.
DEFAULT_MIN_SCORE = float(os.getenv("ANPR_MIN_SCORE", "0.55"))

#: Whether to emit readings the consensus layer has not yet confirmed.
#:
#: Off by default. An unconfirmed reading is one the track has seen too few
#: times to vote on, and shipping those is how the old single-frame behaviour
#: comes back in through the side door.
EMIT_UNCONFIRMED = os.getenv("ANPR_EMIT_UNCONFIRMED", "false").lower() == "true"

#: Below this consensus score a reading is emitted for a human to check rather
#: than acted on.
#:
#: A single threshold throws away everything under it, which for an estate this
#: size is the wrong trade twice over: an automatic action on a wrong plate is
#: expensive, and silently discarding a nearly-right one loses the vehicle.
#: Two thresholds give a third outcome - "probably this, please confirm" - and
#: that is what a forensic queue actually wants.
#:
#: The ICPR 2026 LRLPR organisers made the same point with their tiebreaker:
#: the 3rd-placed system recognised 80.17% with a 2.38% confidence gap, while
#: systems scoring slightly LOWER had gaps of 14.86% and 20.47%. A model whose
#: confidence separates its right answers from its wrong ones is worth more
#: operationally than one that scores higher and cannot tell you which is
#: which, because only the first can triage its own output.
REVIEW_SCORE = float(os.getenv("ANPR_REVIEW_SCORE", "0.35"))


class AnprUnavailable(DetectorError):
    """The engine could not be built.

    Raised with actionable text rather than a stack trace: the weights and the
    OCR wheels are a documented setup step (docs/anpr.md), never something that
    happens silently on first frame.
    """


@dataclass
class PlateSighting:
    """One vehicle's settled plate, ready to become a detection payload."""

    track_id: int
    text: str
    score: float
    confidence: float
    observations: int
    confirmed: bool
    state: str | None
    plate_format: str | None
    #: xyxy of the plate itself, in the frame it was best read from.
    plate_bbox: list[float]
    #: xyxy of the vehicle carrying it.
    vehicle_bbox: list[float]
    #: PTS of the frame the reading settled on. Never arrival time.
    captured_at: float
    frame_index: int
    method: str = ""
    #: True when the reading cleared the review floor but not the acting
    #: threshold: show it to a human, do not act on it automatically.
    needs_review: bool = False
    quality: dict = field(default_factory=dict)


class AnprEngine:
    """One camera's ANPR pipeline.

    Owns a vendored `AnprPipeline`, which owns the vehicle tracker, the plate
    detector, the restoration stack, the OCR ensemble and the per-track vote.
    Build one per camera and feed it that camera's frames in order.
    """

    name = "sentinel-anpr-consensus"

    def __init__(self, *, min_score: float = DEFAULT_MIN_SCORE) -> None:
        try:
            from anpr import config as anpr_config
            from anpr.pipeline import AnprPipeline
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise AnprUnavailable(
                "The ANPR engine needs the analytics extras. Install "
                "services/edge-worker/requirements-anpr.txt, then fetch the "
                "weights (see docs/anpr.md). Detections will continue without "
                "plates until then."
            ) from exc

        self.min_score = min_score
        self.cfg = anpr_config.load()
        try:
            self._pipeline = AnprPipeline(self.cfg)
        except Exception as exc:  # pragma: no cover - weights/driver faults
            raise AnprUnavailable(
                f"ANPR engine could not start: {exc}. Check the model weights "
                f"under {anpr_config.MODELS_DIR} (see docs/anpr.md)."
            ) from exc

        # Emitted once per track. A vehicle read forty times is one sighting.
        self._emitted: dict[int, str] = {}
        self._frames = 0
        self._started = time.perf_counter()

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """Drop all per-track state.

        Called on a stream discontinuity — the sandbox feeds loop, and at the
        loop point the scene cuts. Carrying track ids across that cut would
        splice two different vehicles into one plate history, which is exactly
        the error a route must never contain.
        """
        self._pipeline.reset()
        self._emitted.clear()
        logger.info("ANPR state reset on stream discontinuity")

    def describe(self) -> dict[str, Any]:
        return {
            "detector": self.name,
            "model_version": self.version,
            "plate_model": self.cfg.detect.plate_model,
            "vehicle_model": self.cfg.detect.vehicle_model,
            "ocr_engines": list(self.cfg.ocr.engines),
            "min_score": self.min_score,
            "review_score": REVIEW_SCORE,
            "emit_unconfirmed": EMIT_UNCONFIRMED,
            "preferred_states": list(self.cfg.region.preferred_states),
        }

    @property
    def version(self) -> str:
        plate_model = Path(str(self.cfg.detect.plate_model)).stem
        engines = "+".join(self.cfg.ocr.engines) or "none"
        return f"{plate_model}/{engines}"

    # -- inference ---------------------------------------------------------

    def process(
        self, frame, *, captured_at: float, discontinuity: bool = False
    ) -> tuple[list[Detection], list[PlateSighting]]:
        """Run one frame.

        Returns the vehicle detections seen in this frame, and any plates that
        settled on it. The two lists are independent: most frames produce
        vehicles and no plates, which is correct — a plate is only reported
        once its track has voted.
        """
        if discontinuity:
            self.reset()

        self._frames += 1
        started = time.perf_counter()
        result = self._pipeline.process_frame(frame, timestamp=captured_at)
        latency_ms = (time.perf_counter() - started) * 1000.0

        detections = []
        for box in result.vehicles:
            # The vehicle detector is COCO-pretrained, so its class ids are
            # COCO's. Anything outside the canonical vocabulary is dropped
            # rather than guessed at - a "traffic light" must never arrive as
            # a vehicle just because it was in frame.
            canonical = COCO_TO_CANONICAL.get(int(box.cls))
            if canonical is None:
                continue
            detections.append(
                Detection(
                    class_name=canonical,
                    class_id=int(box.cls),
                    confidence=round(float(box.conf), 4),
                    bbox_xyxy=_xyxy(box),
                    model_name=self.name,
                    model_version=self.version,
                    inference_latency_ms=round(latency_ms, 2),
                    extra={"track_id": box.track_id},
                )
            )

        return detections, list(self._settled(result))

    def _settled(self, result) -> Iterator[PlateSighting]:
        """Plates that reached a verdict on this frame and have not been sent.

        A track is re-emitted when its voted text *changes* — consensus can
        revise a reading as more frames arrive, and the later answer is the
        better one. The central API is idempotent on detection id, so a revised
        reading lands as a new sighting rather than silently overwriting the
        first; both are visible to an operator reviewing a route, which is the
        honest presentation of a reading that moved.
        """
        for event in result.events:
            if not event.text:
                continue
            if not event.confirmed and not EMIT_UNCONFIRMED:
                continue
            # Between the review floor and the acting threshold a reading is
            # still worth surfacing - flagged, not acted on. Below the review
            # floor there is not enough agreement to be worth a human's time.
            needs_review = event.score < self.min_score
            if event.score < REVIEW_SCORE:
                continue
            if self._emitted.get(event.track_id) == event.text:
                continue

            self._emitted[event.track_id] = event.text
            box = event.box or {}
            # The event carries the plate box; the vehicle carrying it is
            # matched back by track id, so a sighting can point at the whole
            # vehicle as well as the plate.
            vehicle = next(
                (v for v in result.vehicles if v.track_id == event.track_id), None
            )
            yield PlateSighting(
                track_id=event.track_id,
                text=event.text,
                score=float(event.score),
                confidence=float(event.confidence),
                observations=int(event.observations or 1),
                confirmed=bool(event.confirmed),
                needs_review=needs_review,
                state=event.state,
                plate_format=event.fmt,
                plate_bbox=_box_to_xyxy(box),
                vehicle_bbox=_xyxy(vehicle) if vehicle is not None else [],
                captured_at=float(event.timestamp),
                frame_index=int(event.frame),
                method=event.method or "",
                quality=dict(event.quality or {}),
            )

    def stats(self) -> dict:
        elapsed = time.perf_counter() - self._started
        engine = self._pipeline.stats()
        return {
            "frames": self._frames,
            "fps": round(self._frames / elapsed, 2) if elapsed else 0.0,
            "plates_emitted": len(self._emitted),
            **engine,
        }


def _xyxy(box) -> list[float]:
    return [
        round(float(box.x1), 2),
        round(float(box.y1), 2),
        round(float(box.x2), 2),
        round(float(box.y2), 2),
    ]


def _box_to_xyxy(box: dict) -> list[float]:
    """The engine reports boxes as dicts; the ingest API wants xyxy."""
    if not box:
        return []
    try:
        return [
            round(float(box["x1"]), 2),
            round(float(box["y1"]), 2),
            round(float(box["x2"]), 2),
            round(float(box["y2"]), 2),
        ]
    except (KeyError, TypeError, ValueError):
        return []


def build_engine(*, min_score: float = DEFAULT_MIN_SCORE) -> AnprEngine | None:
    """Build the engine, or return None when ANPR is off or unavailable.

    Returns None rather than raising when the extras are missing, because ANPR
    failing is not a reason to stop counting vehicles. The caller logs one
    actionable line and carries on without plates.
    """
    if os.getenv("ANPR_ENABLE", "false").lower() != "true":
        logger.info("ANPR disabled (set ANPR_ENABLE=true to turn it on)")
        return None
    try:
        engine = AnprEngine(min_score=min_score)
    except AnprUnavailable as exc:
        logger.error("%s", exc)
        return None
    logger.info("ANPR engine ready: %s", engine.describe())
    return engine
