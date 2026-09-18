"""Vigentra's adapter over the vendored `anpr` engine: detect -> enhance -> read.

The engine tracks each vehicle, reads its plate across every frame the vehicle
appears in, repairs each reading against the Indian plate grammar and fuses the
hypotheses per track (ROVER). That is what turns a one-in-sixty-seven yield on
wide street footage into a usable one.

Three things about this boundary are worth knowing, because they are what the
integration has to get right:

**Frames are pushed in, never pulled.** The engine can open its own RTSP or HLS
source, and deliberately is not allowed to here: the worker already decodes the
camera once for object detection, and a second capture would be a second client
on the gateway for the same picture - exactly the load the integrator's guide
asks callers not to generate. `process()` hands it the frame we already have.

**A plate belongs to a track, not to a frame.** A vehicle read forty times is
one sighting, and the verdict only exists once the track's bank is closed. So
`process()` returns the vehicles it saw and *no* plates; `finish()` closes the
open tracks at the end of a pass and returns the ones that settled. A caller
that never calls `finish()` reads no plates at all.

**Nothing is written to disk.** `evidence_dir=None` turns off the engine's crop
store: the grid is consumed live, and keeping cropped plates on the worker
would be a copy of footage as well as a pile of personal data.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .detectors import COCO_TO_CANONICAL, Detection, DetectorError

logger = logging.getLogger("vigentra.edge.anpr")

#: The vendored engine sits beside `app/`, and so do its weights and config.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: The engine reports a class *name*; the registry stores COCO's id alongside
#: it. Anything outside the canonical vocabulary is dropped rather than guessed
#: at - a "traffic light" must never arrive as a vehicle just because it was in
#: frame.
_CANONICAL_TO_COCO: dict[str, int] = {name: cid for cid, name in COCO_TO_CANONICAL.items()}

#: Off by default. An unconfirmed reading is one the track has seen too few
#: agreeing frames of; surfacing it as a sighting invites acting on it.
EMIT_UNCONFIRMED = os.getenv("ANPR_EMIT_UNCONFIRMED", "false").lower() == "true"

#: Below the review floor there is not enough agreement to be worth a human's
#: time, so the reading is dropped rather than stored.
REVIEW_SCORE = float(os.getenv("ANPR_REVIEW_SCORE", "0.35"))

#: The engine scores every settled reading against the Indian plate grammar and
#: records the result as `grammar_prior` (anpr/pipeline.py). A low prior means
#: the string parses as *some* format but names a district or state that does
#: not exist - DL20, DL22 and the state "SS" all scored under 0.01, against
#: 1.000 for GJ11CK1044. Those are the readings that look like registrations
#: and are not, which is the one output this system must never produce, so they
#: are dropped even when EMIT_UNCONFIRMED is on. Set to 0 to keep everything.
MIN_GRAMMAR_PRIOR = float(os.getenv("ANPR_MIN_GRAMMAR_PRIOR", "0.12"))

#: Model device. "auto" means CUDA if present, else CPU - never the Apple GPU,
#: so a host run on a Mac needs ANPR_DEVICE=mps (Docker cannot reach it).
ANPR_DEVICE = os.getenv("ANPR_DEVICE", "auto")

#: How many per-camera engines to keep loaded at once. A memory dial, not a
#: speed dial: each engine owns a vehicle detector, a plate detector and a
#: reader.
ENGINE_CACHE_SIZE = int(os.getenv("ANPR_ENGINE_CACHE", "4"))

MODELS_DIR = Path(os.getenv("ANPR_MODELS_DIR", str(_PACKAGE_ROOT / "models")))
CONFIG_DIR = Path(os.getenv("ANPR_CONFIG_DIR", str(_PACKAGE_ROOT / "config")))

VEHICLE_WEIGHTS = os.getenv("ANPR_VEHICLE_WEIGHTS", "yolo11s.pt")
PLATE_WEIGHTS = os.getenv("ANPR_PLATE_WEIGHTS", "plate_det_mix_n.pt")
READER_WEIGHTS = os.getenv("ANPR_READER_WEIGHTS", "reader_crnn.onnx")

#: Records accumulate on the pipeline across passes, and this worker runs for
#: ever. Keep the recent tail so the engine can still merge fragments of one
#: vehicle seen either side of a pass boundary, and drop the rest.
_RECORD_TAIL = int(os.getenv("ANPR_RECORD_TAIL", "500"))


class AnprUnavailable(DetectorError):
    """The engine could not be built.

    Raised for a missing package, a missing weight or a missing config file -
    anything where reading plates is impossible but counting vehicles is not.
    """


@dataclass
class PlateSighting:
    """One vehicle's settled plate, ready to become a detection payload."""

    track_id: int
    text: str
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
    quality: dict = field(default_factory=dict)


def _track_number(raw: Any) -> int:
    """A tracker id as an int, whatever shape it arrived in.

    YOLO hands back ints; the engine keys a track "<camera>_s<segment>_t<n>" -
    cam06_s0_t132 - so the number is the digits at the end.

    Never raises. A track id that cannot be parsed must not take down the
    camera's whole pass: that is exactly what int() on "cam06_s0_t1" did, and it
    cost every detection and every plate of the cycle, not just the one track.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        tail = re.search(r"\d+$", str(raw))
        return int(tail.group()) if tail else abs(hash(str(raw))) % 1_000_000


class AnprEngine:
    """One camera's engine: the pipeline, plus the mapping to Vigentra's types.

    One engine per camera, because a vote is per track and a track is per
    stream. Sharing an engine between cameras would let one camera's vehicles
    vote on another's plates.
    """

    name = "vigentra-anpr"

    def __init__(self, *, camera_id: str = "camera", profile_key: str | None = None) -> None:
        self.camera_id = camera_id
        self._frames = 0
        self._started = time.perf_counter()
        #: Track keys already emitted, so a settled plate is sent once even
        #: though its record stays on the pipeline.
        self._emitted: set[str] = set()
        #: Plates that settled before the caller asked for them - a scene cut
        #: mid-pass closes tracks, and those readings must survive to the next
        #: finish() rather than being dropped on the floor.
        self._pending: list[PlateSighting] = []

        try:
            import yaml
            from anpr.camera import profile_for
            from anpr.pipeline import ANPRPipeline
            from anpr.sources.frame_source import Frame
        except ImportError as exc:  # pragma: no cover - depends on the image
            raise AnprUnavailable(
                "The ANPR engine needs the analytics extras:\n"
                "    pip install -r services/edge-worker/requirements-anpr.txt\n"
                f"({exc})"
            ) from exc

        self._Frame = Frame
        #: this camera's block of config/camera_profiles.yaml (defaults when it has none); the worker reads
        #: its sampling and frame-quality settings, the pipeline its threshold overrides
        self.profile = profile_for(CONFIG_DIR, camera_id, profile_key)
        thresholds = CONFIG_DIR / "thresholds.yaml"
        roi_cfg = CONFIG_DIR / "roi.yaml"
        vehicle = MODELS_DIR / VEHICLE_WEIGHTS
        plate = MODELS_DIR / PLATE_WEIGHTS
        reader = MODELS_DIR / READER_WEIGHTS

        missing = [str(p) for p in (thresholds, roi_cfg, vehicle, plate, reader) if not p.exists()]
        if missing:
            raise AnprUnavailable(
                "The ANPR engine is missing files it cannot run without:\n  "
                + "\n  ".join(missing)
                + "\nPut the weights in ANPR_MODELS_DIR and the YAML in "
                "ANPR_CONFIG_DIR, or set ANPR_ENABLE=false to run without plates."
            )

        # thresholds.yaml can name a second CRNN (reading.extra_crnn_weights) that
        # supplies a shown, never-confirmed read on tracks the primary cannot
        # decide. Passing reader_weights skips the engine's own lookup of it, so
        # it is resolved here, in ANPR_MODELS_DIR, and used when present.
        from anpr.camera import deep_merge
        cfg = deep_merge(yaml.safe_load(thresholds.read_text(encoding="utf-8")) or {}, self.profile.thresholds)
        reading = cfg.get("reading") or {}
        extra = [MODELS_DIR / Path(w).name for w in reading.get("extra_crnn_weights") or []]
        self._readers = [str(reader)] + [str(p) for p in extra if p.exists()]

        try:
            self._pipeline = ANPRPipeline(
                camera_id=camera_id,
                thresholds=thresholds,
                roi_cfg=roi_cfg,
                vehicle_weights=str(vehicle),
                plate_weights=str(plate),
                reader_weights=self._readers,
                device=ANPR_DEVICE,
                # Live-only: no crop store on disk. See the module docstring.
                evidence_dir=None,
                keep_frames=False,
                # The worker already samples; the engine must look at every
                # frame it is given, not thin them again.
                frame_stride=1,
                camera_profile=self.profile,
            )
        except Exception as exc:  # pragma: no cover - depends on the weights
            raise AnprUnavailable(f"The ANPR engine failed to load: {exc}") from exc

    # -- lifecycle ---------------------------------------------------------

    def reset(self) -> None:
        """Drop per-track state after a scene cut.

        The grid's feeds loop; at the loop point the scene cuts. Carrying track
        ids across that cut would splice two different vehicles into one plate
        history, which is exactly the error a route must never contain.
        """
        # Settle first: the tracks being torn down here may have voted, and a
        # reading is not discarded just because the scene cut after it.
        self._settle()
        vehicles = getattr(self._pipeline, "vehicles", None)
        if vehicles is not None and hasattr(vehicles, "reset"):
            vehicles.reset()

    def new_stream(self) -> None:
        """A new capture of the same camera.

        The track ids mean nothing across a reconnect, so the tracker goes -
        but the camera has not moved, so what it has learned about where its
        burned-in clock sits is kept.
        """
        self.reset()

    # -- per frame ---------------------------------------------------------

    def process(
        self, frame, *, captured_at: float, discontinuity: bool = False
    ) -> tuple[list[Detection], list[PlateSighting]]:
        """Run one frame.

        Returns the vehicles seen in this frame. The plate list is always empty:
        a plate settles per track, and tracks are closed by `finish()`.
        """
        if discontinuity:
            self.reset()

        started = time.perf_counter()
        self._pipeline.process_frame(
            self._Frame(
                image=frame,
                pts_ms=float(captured_at) * 1000.0,
                frame_idx=self._frames,
                camera_id=self.camera_id,
                discontinuity=discontinuity,
            )
        )
        self._frames += 1
        latency_ms = (time.perf_counter() - started) * 1000.0

        detections: list[Detection] = []
        for track_id, box, cls_name, conf in getattr(self._pipeline, "last_vehicles", []):
            canonical = str(cls_name).strip().lower()
            class_id = _CANONICAL_TO_COCO.get(canonical)
            if class_id is None:
                continue
            detections.append(
                Detection(
                    class_name=canonical,
                    class_id=class_id,
                    confidence=round(float(conf), 4),
                    bbox_xyxy=[round(float(v), 1) for v in box],
                    model_name=self.name,
                    model_version=self.version,
                    inference_latency_ms=round(latency_ms, 2),
                    extra={"track_id": _track_number(track_id)},
                )
            )
        return detections, []

    # -- end of pass -------------------------------------------------------

    def finish(self) -> list[PlateSighting]:
        """Close the open tracks and return every plate that settled this pass."""
        self._settle()
        out, self._pending = self._pending, []
        return out

    def _settle(self) -> None:
        """Close open tracks and move newly settled readings into `_pending`."""
        try:
            self._pipeline.flush()
        except Exception as exc:  # pragma: no cover - engine fault
            logger.warning("ANPR flush failed for %s: %s", self.camera_id, exc)
            return

        out = self._pending
        for rec in list(getattr(self._pipeline, "records", [])):
            key = str(rec.get("track_id"))
            if key in self._emitted:
                continue
            text = rec.get("plate")
            if not text:
                continue
            confirmed = rec.get("status") == "CONFIRMED"
            score = float(rec.get("confidence") or 0.0)
            if not confirmed and not EMIT_UNCONFIRMED:
                continue
            # Below the review floor there is not enough agreement to be worth
            # a human's time.
            if score < REVIEW_SCORE:
                continue
            # A reading whose grammar prior is negligible is not a plate we
            # failed to confirm - it is a string shaped like one. Emitting it
            # would put an invented registration on an operator's screen.
            prior = rec.get("grammar_prior")
            if prior is not None and float(prior) < MIN_GRAMMAR_PRIOR:
                # INFO, not DEBUG: a pass that read four plates and emitted
                # none must not look identical to a pass that read nothing.
                # That silence is what made "0 plates" impossible to diagnose.
                logger.info(
                    "dropped %s: grammar prior %.3f below %.2f (impossible "
                    "state or district)", text, float(prior), MIN_GRAMMAR_PRIOR,
                )
                continue

            self._emitted.add(key)
            # _merge_fragments gives every fragment of one vehicle the same reading; emitting each of
            # them reported one vehicle several times. The group's first track speaks for it.
            group = rec.get("merged_from") or []
            if group and str(min(group)) != str(rec.get("track_id")):
                continue
            out.append(
                PlateSighting(
                    track_id=_track_number(key),
                    text=str(text),
                    confidence=score,
                    observations=int(rec.get("frames_fused") or rec.get("n_plate_hits") or 1),
                    confirmed=confirmed,
                    # The engine fuses under a grammar rather than reporting a
                    # state of its own; leaving this None beats inferring one
                    # from the first two characters of a fused reading.
                    state=None,
                    plate_format=rec.get("plate_class") or None,
                    plate_bbox=[float(v) for v in (rec.get("bbox") or [])],
                    vehicle_bbox=[float(v) for v in (rec.get("vehicle_box") or [])],
                    captured_at=float(rec.get("last_seen_pts_ms") or 0.0) / 1000.0,
                    frame_index=int(rec.get("best_frame") or rec.get("last_frame") or 0),
                    method=str(rec.get("reason") or ""),
                    quality=dict(rec.get("quality") or {}),
                )
            )

        self._trim_records()

    def _trim_records(self) -> None:
        records = getattr(self._pipeline, "records", None)
        if records is None or len(records) <= _RECORD_TAIL:
            return
        keep = records[-_RECORD_TAIL:]
        kept = {str(r.get("track_id")) for r in keep}
        self._emitted &= kept
        records[:] = keep

    # -- reporting ---------------------------------------------------------

    @property
    def version(self) -> str:
        return f"{Path(PLATE_WEIGHTS).stem}/{Path(READER_WEIGHTS).stem}"

    def describe(self) -> dict[str, Any]:
        return {
            "detector": self.name,
            "version": self.version,
            "camera": self.camera_id,
            "vehicle_model": VEHICLE_WEIGHTS,
            "plate_model": PLATE_WEIGHTS,
            "reader_model": READER_WEIGHTS,
            "readers": [Path(r).name for r in getattr(self, "_readers", [READER_WEIGHTS])],
            "device": ANPR_DEVICE,
            "emit_unconfirmed": EMIT_UNCONFIRMED,
            "camera_profile": getattr(getattr(self, "profile", None), "key", None),
        }

    def stats(self) -> dict:
        elapsed = time.perf_counter() - self._started
        records = list(getattr(self._pipeline, "records", []))
        gate = list(getattr(self._pipeline, "gate_log", []))
        floor = float(((getattr(self._pipeline, "cfg", None) or {}).get("confidence")
                       or {}).get("confirm_min_width_px", 40))
        widths = [float(g.get("w") or 0.0) for g in gate]
        return {
            "frames": self._frames,
            "fps": round(self._frames / elapsed, 2) if elapsed else 0.0,
            "tracks": len(records),
            "confirmed": sum(1 for r in records if r.get("status") == "CONFIRMED"),
            "candidates": sum(1 for r in records if r.get("status") == "CANDIDATE"),
            "unreadable": sum(1 for r in records if r.get("status") == "UNREADABLE"),
            "emitted": len(self._emitted),
            # why a camera produced no plate: how many closed tracks even had a plate wide enough to read
            "readability": {
                "tracks_with_a_plate": len(widths),
                "tracks_at_readable_width": sum(1 for w in widths if w >= floor),
                "tracks_below_floor": sum(1 for w in widths if 0 < w < floor),
                "readable_width_px": floor,
                "widest_plate_px": round(max(widths), 1) if widths else 0.0,
            },
            "timings_ms": {k: round(v, 1) for k, v in
                           (getattr(self._pipeline, "timings", None).as_dict() if
                            getattr(self._pipeline, "timings", None) else {}).items()
                           if k.endswith("_ms_per_frame") or k.endswith("_ms_per_track")},
        }


def load_camera_profile(camera_id: str, profile_key: str | None = None):
    """This camera's block of config/camera_profiles.yaml, or None without the anpr package."""
    try:
        from anpr.camera import profile_for
    except ImportError:  # pragma: no cover - minimal install
        return None
    return profile_for(CONFIG_DIR, camera_id, profile_key)


def pass_budget(profile, max_frames: int) -> int:
    """Processed frames this camera gets per pass: its profile's `sampling.max_frames`, or the worker's.

    A plate needs many frames of the same vehicle: on the Delhi clip 25 processed frames confirmed 2
    plates and 150 confirmed 4, while 100 spread thinly over the same footage still confirmed 2
    (docs/anpr-optimisation.md). Cameras whose plates are too small to read keep the cheap budget.
    """
    value = (getattr(profile, "sampling", None) or {}).get("max_frames")
    return max(1, int(value)) if value else max_frames


def sampler_settings(profile, stride: int) -> dict:
    """AdaptiveSampler arguments: the profile's `sampling` block over the worker's stride."""
    s = dict(getattr(profile, "sampling", None) or {})
    out = {"stride": max(1, int(s.get("stride") or stride))}
    if s.get("burst_frames") is not None:
        out["burst_frames"] = int(s["burst_frames"])
    if s.get("burst_plate_px") is not None:
        out["burst_plate_px"] = float(s["burst_plate_px"])
    return out


def router_settings(profile) -> dict:
    """FrameQualityRouter arguments from the profile's `frame_quality` block.

    `night_mode: off` never enhances a dark frame, `on` enhances every frame, `auto` (the default)
    enhances frames darker than `low_light_luma`. Any other key is passed through.
    """
    fq = dict(getattr(profile, "frame_quality", None) or {})
    mode = str(fq.pop("night_mode", "auto")).lower()
    if mode == "off":
        fq["enhance_low_light"] = False
    elif mode == "on":
        fq["low_light_luma"] = 256.0
    return fq


def build_engine(*, camera_id: str = "camera", profile_key: str | None = None) -> AnprEngine | None:
    """Build the engine, or return None when ANPR is off or unavailable.

    Returns None rather than raising when the extras are missing, because ANPR
    failing is not a reason to stop counting vehicles. The caller logs one
    actionable line and carries on without plates.
    """
    if os.getenv("ANPR_ENABLE", "false").lower() != "true":
        logger.info("ANPR disabled (set ANPR_ENABLE=true to turn it on)")
        return None
    try:
        engine = AnprEngine(camera_id=camera_id, profile_key=profile_key)
    except AnprUnavailable as exc:
        logger.error("%s", exc)
        return None
    logger.info("ANPR engine ready: %s", engine.describe())
    return engine


class EngineCache:
    """One ANPR engine per camera, kept between cycles, bounded.

    A worker cycling its cameras used to build a fresh engine for every camera
    on every pass. That threw away the weights - seconds and hundreds of
    megabytes to load - and everything the camera had learned about itself:
    where its burned-in clock sits, which scene text is not a plate.

    Bounded because engines are heavy. Eviction is least-recently-used, which
    on a round-robin over more cameras than the capacity means every lookup
    misses: the old behaviour, no worse.
    """

    def __init__(self, capacity: int = ENGINE_CACHE_SIZE) -> None:
        self.capacity = max(1, capacity)
        # Insertion-ordered: the first key is the least recently used.
        self._engines: "OrderedDict[str, AnprEngine]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        #: Set once ANPR is found to be off or unavailable, so a worker with no
        #: analytics extras does not retry a failing build on every camera of
        #: every cycle and log the same error a thousand times.
        self._unavailable = False

    def get(self, camera_id: str, profile_key: str | None = None) -> AnprEngine | None:
        """The engine for this camera, built on first use."""
        if self._unavailable:
            return None
        engine = self._engines.get(camera_id)
        if engine is not None:
            self._engines.move_to_end(camera_id)
            self.hits += 1
            # Time has passed and this is a new capture: the vehicles it saw
            # last cycle are long gone, and their track ids must not be
            # inherited by whatever the tracker numbers next.
            engine.new_stream()
            return engine

        self.misses += 1
        engine = build_engine(camera_id=camera_id, profile_key=profile_key)
        if engine is None:
            self._unavailable = True
            return None

        self._engines[camera_id] = engine
        if len(self._engines) > self.capacity:
            evicted, _ = self._engines.popitem(last=False)
            self.evictions += 1
            logger.info(
                "ANPR engine for %s evicted to stay within %d loaded engines",
                evicted, self.capacity,
            )
        return engine

    def describe(self) -> dict:
        return {
            "loaded": len(self._engines),
            "capacity": self.capacity,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "cameras": list(self._engines),
        }
