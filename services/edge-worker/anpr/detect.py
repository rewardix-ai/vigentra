"""Vehicle and plate detection, plus track identity.

Two detectors cooperate:

* a COCO detector tracks *vehicles*, which gives every car a stable id across
  frames.  That id is the key the consensus layer aggregates on.
* a fine-tuned detector finds *plates*.  It runs once over the whole frame and
  again inside each vehicle box, upscaled.  The second pass is what makes
  distant plates findable at all - a plate 20 px wide in a 1080p frame is
  ~150 px wide once its vehicle crop is blown up to the detector's input size.

Plates that belong to no detected vehicle still get an identity from a small
IoU tracker, so a motorbike the COCO model missed is not thrown away.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .config import DetectConfig, resolve_model

log = logging.getLogger(__name__)

#: Track ids for plates with no parent vehicle start here, so they can never
#: collide with the tracker's own vehicle ids.
ORPHAN_ID_BASE = 1_000_000


@dataclass
class Box:
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 0.0
    cls: int = 0
    track_id: int | None = None

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    def as_int(self) -> tuple[int, int, int, int]:
        return int(self.x1), int(self.y1), int(self.x2), int(self.y2)

    def clipped(self, w: int, h: int) -> "Box":
        return Box(max(0.0, min(self.x1, w - 1)), max(0.0, min(self.y1, h - 1)),
                   max(0.0, min(self.x2, w)), max(0.0, min(self.y2, h)),
                   self.conf, self.cls, self.track_id)

    def expand(self, fx: float, fy: float, w: int, h: int) -> "Box":
        """Grow the box by a fraction of its size, clipped to the frame."""
        dx, dy = self.w * fx, self.h * fy
        return Box(self.x1 - dx, self.y1 - dy, self.x2 + dx, self.y2 + dy,
                   self.conf, self.cls, self.track_id).clipped(w, h)

    def scaled(self, factor: float) -> "Box":
        """Same box in a coordinate space *factor* times larger."""
        if factor == 1.0:
            return self
        return Box(self.x1 * factor, self.y1 * factor, self.x2 * factor,
                   self.y2 * factor, self.conf, self.cls, self.track_id)

    def as_dict(self) -> dict:
        return {"x1": round(self.x1), "y1": round(self.y1),
                "x2": round(self.x2), "y2": round(self.y2),
                "conf": round(self.conf, 3), "track_id": self.track_id}


@dataclass
class PlateDetection:
    box: Box
    #: id of the vehicle this plate sits on, or an orphan id
    track_id: int
    vehicle: Box | None = None
    #: plate crop in BGR, taken with a small margin
    crop: np.ndarray | None = field(default=None, repr=False)
    source: str = "frame"        # "frame" | "roi"


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def containment(inner: Box, outer: Box) -> float:
    """Fraction of *inner* that lies inside *outer*."""
    ix1, iy1 = max(inner.x1, outer.x1), max(inner.y1, outer.y1)
    ix2, iy2 = min(inner.x2, outer.x2), min(inner.y2, outer.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return inter / inner.area if inner.area > 0 else 0.0


def nms(boxes: list[Box], thresh: float = 0.45) -> list[Box]:
    """Standard greedy NMS, used to merge the full-frame and ROI passes."""
    out: list[Box] = []
    for b in sorted(boxes, key=lambda x: x.conf, reverse=True):
        if all(iou(b, kept) < thresh for kept in out):
            out.append(b)
    return out


class IouTracker:
    """Fallback tracker for plates with no parent vehicle.

    Matches on IoU first and falls back to centroid proximity.  The fallback
    matters: a plate crossing the frame can move further than its own width
    between frames, which drives IoU to zero even though the association is
    obvious.  Without it a single vehicle fragments into several short tracks,
    none of which gathers enough evidence to confirm - and each of which is
    free to confirm a *different* wrong plate.
    """

    def __init__(self, iou_threshold: float = 0.2, max_age: int = 20,
                 max_centroid_dist: float = 2.2) -> None:
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        #: centroid distance limit, in multiples of the box's diagonal
        self.max_centroid_dist = max_centroid_dist
        self._tracks: dict[int, tuple[Box, int]] = {}     # id -> (box, last_frame)
        self._next = ORPHAN_ID_BASE

    def update(self, boxes: list[Box], frame: int) -> list[int]:
        # Drop tracks that have not been seen for a while.
        for tid in [t for t, (_, seen) in self._tracks.items()
                    if frame - seen > self.max_age]:
            del self._tracks[tid]

        assigned: list[int] = []
        taken: set[int] = set()
        for box in boxes:
            best_id = self._match(box, taken)
            if best_id is None:
                best_id = self._next
                self._next += 1
            self._tracks[best_id] = (box, frame)
            taken.add(best_id)
            assigned.append(best_id)
        return assigned

    def _match(self, box: Box, taken: set[int]) -> int | None:
        best_id, best_iou = None, self.iou_threshold
        for tid, (prev, _) in self._tracks.items():
            if tid in taken:
                continue
            score = iou(box, prev)
            if score > best_iou:
                best_id, best_iou = tid, score
        if best_id is not None:
            return best_id

        # No overlap: fall back to the nearest plausible centroid of a
        # similarly-sized box.
        diag = max((box.w ** 2 + box.h ** 2) ** 0.5, 1.0)
        best_dist = self.max_centroid_dist * diag
        for tid, (prev, _) in self._tracks.items():
            if tid in taken:
                continue
            # A genuine match keeps roughly the same apparent size.
            ratio = box.area / prev.area if prev.area > 0 else 0.0
            if not (0.4 <= ratio <= 2.5):
                continue
            dist = ((box.cx - prev.cx) ** 2 + (box.cy - prev.cy) ** 2) ** 0.5
            if dist < best_dist:
                best_id, best_dist = tid, dist
        return best_id

    def reset(self) -> None:
        self._tracks.clear()
        self._next = ORPHAN_ID_BASE


def _precision_kwargs(half: bool, device: str) -> dict:
    """Ask for fp16 using whichever argument this ultralytics understands.

    ``half`` was deprecated in favour of ``quantize=16``; passing the old name
    still works but warns on every single call.
    """
    if device == "cpu" or not half:
        return {}
    try:
        from ultralytics.cfg import DEFAULT_CFG_DICT
        if "quantize" in DEFAULT_CFG_DICT:
            return {"quantize": 16}
    except ImportError:
        pass
    return {"half": True}


class Detector:
    """Vehicle tracking + plate detection for one video stream."""

    #: Total pixels allowed in one inference batch.  Sized for a 4 GB card:
    #: eight 640x640 crops.  Larger source frames therefore batch fewer at a
    #: time rather than overrunning memory.
    ROI_PIXEL_BUDGET = 8 * 640 * 640

    def __init__(self, cfg: DetectConfig) -> None:
        self.cfg = cfg
        self.device = self._pick_device(cfg.device)
        self.vehicle_model = None
        self.plate_model = None
        self.orphan_tracker = IouTracker()
        self._precision = _precision_kwargs(cfg.half, self.device)
        self._load()

    @staticmethod
    def _pick_device(pref: str) -> str:
        if pref and pref != "auto":
            return pref
        try:
            import torch
            return "0" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load(self) -> None:
        from ultralytics import YOLO

        vpath = resolve_model(self.cfg.vehicle_model)
        try:
            self.vehicle_model = YOLO(vpath)
            log.info("vehicle detector: %s on %s", vpath, self.device)
        except Exception as exc:                # noqa: BLE001
            log.error("could not load vehicle detector %s: %s", vpath, exc)

        ppath = resolve_model(self.cfg.plate_model)
        try:
            self.plate_model = YOLO(ppath)
            log.info("plate detector: %s on %s", ppath, self.device)
        except Exception as exc:                # noqa: BLE001
            log.error("could not load plate detector %s: %s - run "
                      "scripts/get_models.py", ppath, exc)

    @property
    def ready(self) -> bool:
        return self.plate_model is not None

    def reset(self) -> None:
        """Forget all track state - call between videos."""
        self.orphan_tracker.reset()
        if self.vehicle_model is not None:
            try:
                self.vehicle_model.predictor = None     # drops ByteTrack state
            except AttributeError:
                pass

    # -- vehicles --------------------------------------------------------
    def track_vehicles(self, frame: np.ndarray) -> list[Box]:
        if self.vehicle_model is None:
            return []
        try:
            results = self.vehicle_model.track(
                frame, persist=True, verbose=False, tracker=self.cfg.tracker,
                conf=self.cfg.vehicle_conf, imgsz=self.cfg.vehicle_imgsz,
                classes=list(self.cfg.vehicle_classes), device=self.device,
                **self._precision,
            )
        except Exception as exc:                # noqa: BLE001 - never kill a frame
            log.warning("vehicle tracking failed: %s", exc)
            return []

        out: list[Box] = []
        for r in results:
            if r.boxes is None:
                continue
            xyxy = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            clss = r.boxes.cls.cpu().numpy().astype(int)
            ids = (r.boxes.id.cpu().numpy().astype(int)
                   if r.boxes.id is not None else [None] * len(xyxy))
            for (x1, y1, x2, y2), c, k, tid in zip(xyxy, confs, clss, ids):
                out.append(Box(float(x1), float(y1), float(x2), float(y2),
                               float(c), int(k),
                               int(tid) if tid is not None else None))
        return out

    # -- plates ----------------------------------------------------------
    def _detect_plates(self, image: np.ndarray) -> list[Box]:
        batched = self._detect_plates_batch([image])
        return batched[0] if batched else []

    def _detect_plates_batch(self, images: list[np.ndarray],
                             imgsz: int | None = None) -> list[list[Box]]:
        """Detect plates in several images with a single inference call.

        Ultralytics accepts a list and runs it as one batch.  Calling it once
        per vehicle instead turns a crowded frame into a dozen separate GPU
        round-trips, which is what dominates the frame budget.
        """
        if self.plate_model is None or not images:
            return [[] for _ in images]

        # Chunk the batch, sized to the crops actually in hand.  Cameras on a
        # mixed grid differ in resolution, so a batch count tuned for 720p
        # crops will exhaust a small card when the same code meets 4K ones.
        # Budget by pixels instead of by count, and never go below one.
        chunk = max(1, self.cfg.roi_batch)
        if images:
            biggest = max(im.shape[0] * im.shape[1] for im in images)
            if biggest > 0:
                by_pixels = int(self.ROI_PIXEL_BUDGET / biggest)
                chunk = max(1, min(chunk, by_pixels))
        results = []
        for start in range(0, len(images), chunk):
            part = images[start:start + chunk]
            try:
                results.extend(self.plate_model.predict(
                    part, verbose=False, conf=self.cfg.plate_conf,
                    imgsz=imgsz or self.cfg.plate_imgsz, device=self.device,
                    **self._precision,
                ))
            except Exception as exc:            # noqa: BLE001
                log.warning("plate detection failed on %d crops: %s", len(part), exc)
                results.extend([None] * len(part))

        out: list[list[Box]] = []
        for r in results:
            if r is None:
                out.append([])
                continue
            boxes: list[Box] = []
            if r.boxes is not None:
                xyxy = r.boxes.xyxy.cpu().numpy()
                confs = r.boxes.conf.cpu().numpy()
                for (x1, y1, x2, y2), c in zip(xyxy, confs):
                    boxes.append(Box(float(x1), float(y1), float(x2), float(y2),
                                     float(c)))
            out.append(boxes)
        while len(out) < len(images):
            out.append([])
        return out

    def _roi_plates(self, frame: np.ndarray, vehicles: list[Box],
                    skip: set[int] | None = None) -> list[Box]:
        """Second plate pass inside each vehicle box, upscaled.

        This is what recovers plates that are only a handful of pixels wide
        in the full frame.  All the crops go through the detector as one
        batch, and vehicles whose plate is already settled are skipped.
        """
        import cv2

        skip = skip or set()
        h, w = frame.shape[:2]
        crops: list[np.ndarray] = []
        origins: list[tuple[int, int, float]] = []

        for v in vehicles:
            if v.track_id is not None and v.track_id in skip:
                continue
            # Plates sit low on a vehicle, but a little margin costs nothing
            # and protects against a tight or slightly-off vehicle box.
            roi = v.expand(0.04, 0.04, w, h)
            x1, y1, x2, y2 = roi.as_int()
            if x2 - x1 < 24 or y2 - y1 < 24:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            scale = 1.0
            longest = max(crop.shape[0], crop.shape[1])
            if longest < self.cfg.roi_min_size:
                scale = self.cfg.roi_min_size / float(longest)
                crop = cv2.resize(crop, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_CUBIC)
            crops.append(crop)
            origins.append((x1, y1, scale))

        found: list[Box] = []
        roi_results = self._detect_plates_batch(crops, imgsz=self.cfg.roi_imgsz)
        for boxes, (x1, y1, scale) in zip(roi_results, origins):
            for b in boxes:
                found.append(Box(
                    x1 + b.x1 / scale, y1 + b.y1 / scale,
                    x1 + b.x2 / scale, y1 + b.y2 / scale, b.conf))
        return found

    # -- the whole frame -------------------------------------------------
    def process(self, frame: np.ndarray, frame_idx: int,
                settled: set[int] | None = None
                ) -> tuple[list[Box], list[PlateDetection]]:
        """Detect and identify every plate in one frame.

        *settled* holds track ids whose plate is already locked in; their
        vehicles are skipped by the (expensive) ROI pass.
        """
        import cv2

        h, w = frame.shape[:2]

        # Detect on a downscaled copy, then map the boxes back.  The ROI pass
        # and every crop still work on the original frame, so downscaling
        # costs no plate detail - only detector input that would have been
        # thrown away by the internal resize to imgsz anyway.
        proc, inv = frame, 1.0
        if self.cfg.process_width and w > self.cfg.process_width:
            factor = self.cfg.process_width / float(w)
            proc = cv2.resize(frame, (int(w * factor), int(h * factor)),
                              interpolation=cv2.INTER_AREA)
            inv = 1.0 / factor

        vehicles = [v.scaled(inv).clipped(w, h) for v in self.track_vehicles(proc)]
        plates = [p.scaled(inv) for p in self._detect_plates(proc)]
        if self.cfg.roi_pass and vehicles:
            plates.extend(self._roi_plates(frame, vehicles, skip=settled))
        plates = nms([p.clipped(w, h) for p in plates])

        # Attach each plate to the vehicle that best contains it.
        detections: list[PlateDetection] = []
        orphans: list[Box] = []
        orphan_idx: list[int] = []
        for p in plates:
            parent, best = None, 0.55
            for v in vehicles:
                if v.track_id is None:
                    continue
                score = containment(p, v)
                if score > best:
                    parent, best = v, score
            if parent is not None:
                detections.append(PlateDetection(
                    box=p, track_id=parent.track_id, vehicle=parent,
                    crop=self._crop(frame, p)))
            else:
                orphan_idx.append(len(detections))
                detections.append(PlateDetection(box=p, track_id=-1,
                                                 crop=self._crop(frame, p)))
                orphans.append(p)

        if orphans:
            for slot, tid in zip(orphan_idx,
                                 self.orphan_tracker.update(orphans, frame_idx)):
                detections[slot].track_id = tid

        return vehicles, detections

    @staticmethod
    def _crop(frame: np.ndarray, box: Box, margin: float = 0.10) -> np.ndarray:
        """Cut the plate out with a margin, so rectification has context."""
        h, w = frame.shape[:2]
        b = box.expand(margin, margin * 1.6, w, h)
        x1, y1, x2, y2 = b.as_int()
        if x2 <= x1 or y2 <= y1:
            return np.empty((0, 0, 3), dtype=np.uint8)
        return frame[y1:y2, x1:x2].copy()
