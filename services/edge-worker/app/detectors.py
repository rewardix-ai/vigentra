"""Object detection abstraction for the Vigentra edge analytics worker.

Runs where the authorized video already is — inside the department's own
environment — and sends only detection METADATA to the central API. Raw frames
never leave the edge.

Scope for this phase: generic object classes only.

    person · car · motorcycle · bus · truck · auto-rickshaw · bicycle

Deliberately NOT here, and not a small extension of it: ANPR/OCR, plate
reading, face recognition, vehicle make/model/colour, vehicle re-ID,
registration lookup, cross-camera identity. Those are later phases with their
own review and their own data protections.

Honest note on classes: a stock COCO-pretrained YOLO knows person, bicycle,
car, motorcycle, bus and truck. It does **not** know `auto-rickshaw` — COCO has
no such class, and a stock model will never emit one. It stays in the canonical
vocabulary because Indian traffic needs it, but producing it requires a
fine-tuned model. `UltralyticsYoloDetector.describe()` reports which of the
canonical classes the loaded weights can actually produce.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("vigentra.edge.detector")

#: The canonical class vocabulary the central API accepts.
DETECTION_CLASSES: tuple[str, ...] = (
    "person",
    "car",
    "motorcycle",
    "bus",
    "truck",
    "auto-rickshaw",
    "bicycle",
)

#: COCO class id -> our canonical name. Anything not listed is discarded rather
#: than guessed at, so a "traffic light" never becomes a vehicle.
COCO_TO_CANONICAL: dict[int, str] = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


@dataclass
class Detection:
    """One detected object in one frame."""

    class_name: str
    class_id: int
    confidence: float
    #: xyxy in pixels, clamped to the frame.
    bbox_xyxy: list[float]
    model_name: str
    model_version: str
    inference_latency_ms: float | None = None
    frame_quality: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def detection_id(self, camera_id: str, timestamp_iso: str, index: int) -> str:
        """Deterministic ID so a replayed frame re-ingests idempotently.

        Built from the camera, the frame instant, the class and the box — the
        same detection submitted twice collapses to one row rather than
        inflating counts.
        """
        seed = f"{camera_id}|{timestamp_iso}|{self.class_name}|{[round(v, 1) for v in self.bbox_xyxy]}|{index}"
        return f"det_{hashlib.sha1(seed.encode()).hexdigest()[:20]}"

    def to_payload(self, camera_id: str, timestamp_iso: str, index: int, **overrides) -> dict:
        payload = {
            "detection_id": self.detection_id(camera_id, timestamp_iso, index),
            "camera_id": camera_id,
            "timestamp_utc": timestamp_iso,
            "class_name": self.class_name,
            "class_id": self.class_id,
            "confidence": round(float(self.confidence), 4),
            "bbox_xyxy": [round(float(v), 2) for v in self.bbox_xyxy],
            "model_name": self.model_name,
            "model_version": self.model_version,
            "inference_latency_ms": self.inference_latency_ms,
            "frame_quality": self.frame_quality,
        }
        payload.update(overrides)
        return payload


class DetectorError(RuntimeError):
    """Detector could not be prepared or could not run."""


class WeightsUnavailable(DetectorError):
    """Model weights are missing and were not downloaded.

    Raised with actionable text rather than a stack trace: weight downloads are
    a deliberate, documented setup step (see docs/yolo-setup.md), never
    something that happens silently on first request.
    """


class ObjectDetector(ABC):
    """Contract every detector satisfies."""

    name: str = "base_detector"
    version: str = "0.0.0"

    @abstractmethod
    def detect(self, frame) -> list[Detection]:
        """Return detections for one frame, already threshold-filtered."""

    def describe(self) -> dict[str, Any]:
        return {"detector": self.name, "model_version": self.version}

    def warmup(self) -> None:  # pragma: no cover - optional hook
        return None


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------

class MockDetector(ObjectDetector):
    """Deterministic synthetic detections. No CV dependencies at all.

    Exists so the whole ingestion path — schema validation, idempotency,
    provenance, dashboards — can be exercised in CI and in an offline demo on a
    machine with no GPU, no torch and no model weights.

    Output is a pure function of the frame index, so a given run always
    produces the same boxes and a test can assert on them.
    """

    name = "mock_detector"
    version = "1.0.0"

    #: (class, class_id, base confidence, bbox as fractions of the frame)
    _TEMPLATE = [
        ("car", 2, 0.94, (0.42, 0.55, 0.72, 0.86)),
        ("person", 0, 0.88, (0.11, 0.48, 0.18, 0.79)),
        ("motorcycle", 3, 0.71, (0.63, 0.60, 0.78, 0.83)),
        ("bus", 5, 0.66, (0.02, 0.30, 0.29, 0.62)),
        ("truck", 7, 0.41, (0.80, 0.33, 0.99, 0.60)),  # below default threshold
    ]

    def __init__(
        self,
        confidence_threshold: float = 0.45,
        frame_size: tuple[int, int] = (960, 540),
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.frame_width, self.frame_height = frame_size
        self._frame_index = 0

    def detect(self, frame) -> list[Detection]:
        started = time.perf_counter()
        index = self._frame_index
        self._frame_index += 1

        width, height = _frame_size(frame, (self.frame_width, self.frame_height))

        detections: list[Detection] = []
        for position, (class_name, class_id, base, box) in enumerate(self._TEMPLATE):
            # A small deterministic wobble, so successive frames are not
            # byte-identical and dedupe logic gets a realistic workout.
            drift = ((index + position) % 5) * 0.01
            confidence = max(0.0, min(1.0, base - drift))
            if confidence < self.confidence_threshold:
                continue
            shift = ((index % 10) - 5) * 0.004
            x1 = max(0.0, min(1.0, box[0] + shift))
            x2 = max(0.0, min(1.0, box[2] + shift))
            detections.append(
                Detection(
                    class_name=class_name,
                    class_id=class_id,
                    confidence=confidence,
                    bbox_xyxy=[x1 * width, box[1] * height, x2 * width, box[3] * height],
                    model_name=self.name,
                    model_version=self.version,
                    inference_latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            )
        return detections

    def describe(self) -> dict[str, Any]:
        return {
            "detector": self.name,
            "model_name": self.name,
            "model_version": self.version,
            "device": "cpu",
            "weights_available": True,
            "classes": list(DETECTION_CLASSES),
            "confidence_threshold": self.confidence_threshold,
            "note": "Synthetic detections for testing and offline demos.",
        }


# ---------------------------------------------------------------------------
# Ultralytics
# ---------------------------------------------------------------------------

def _installed_ultralytics_version() -> str:
    """The Ultralytics build in this environment, or "unknown" if absent."""
    try:
        import ultralytics

        return str(getattr(ultralytics, "__version__", "unknown"))
    except ImportError:
        return "unknown"


class UltralyticsYoloDetector(ObjectDetector):
    """Real inference via Ultralytics YOLO.

    The Ultralytics version and the weights file are both pinned by
    configuration and reported on every detection, so a result can always be
    traced back to the exact build that produced it.
    """

    name = "ultralytics-yolo"

    def __init__(
        self,
        model_name: str | None = None,
        confidence_threshold: float = 0.45,
        device: str = "auto",
        weights_dir: str | None = None,
        allow_download: bool | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv("YOLO_MODEL_NAME", "yolo11n.pt")
        self.confidence_threshold = confidence_threshold
        self.requested_device = device
        self.weights_dir = weights_dir or os.getenv("YOLO_WEIGHTS_DIR", "/app/weights")
        # Downloading weights is an explicit, documented setup step. Off by
        # default so a container never silently pulls ~6 MB (or ~130 MB for a
        # larger model) from the internet on first request.
        self.allow_download = (
            allow_download
            if allow_download is not None
            else os.getenv("YOLO_ALLOW_DOWNLOAD", "false").strip().lower() in ("1", "true", "yes")
        )
        self._model = None
        self._ultralytics_version = "unknown"
        self._resolved_device = "cpu"
        self._model_classes: dict[int, str] = {}

    # -- setup ------------------------------------------------------------

    def _weights_path(self) -> str:
        """Prefer a bundled weights file; fall back to the bare name."""
        candidate = os.path.join(self.weights_dir, os.path.basename(self.model_name))
        if os.path.isfile(candidate):
            return candidate
        if os.path.isfile(self.model_name):
            return self.model_name
        return ""

    def _resolve_device(self) -> str:
        if self.requested_device and self.requested_device != "auto":
            return self.requested_device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda:0"
            if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                return "mps"
        except Exception:  # torch absent or misconfigured -> CPU is correct
            pass
        return "cpu"

    def load(self) -> None:
        """Import Ultralytics and load weights. Raises actionable errors."""
        if self._model is not None:
            return

        try:
            import ultralytics
            from ultralytics import YOLO
        except ImportError as exc:
            raise DetectorError(
                "Ultralytics is not installed in this environment. Install the "
                "edge-worker analytics extras:\n"
                "    pip install -r services/edge-worker/requirements-yolo.txt\n"
                "or run with YOLO_ENABLE=false to use the mock detector."
            ) from exc

        self._ultralytics_version = _installed_ultralytics_version()

        path = self._weights_path()
        if not path and not self.allow_download:
            raise WeightsUnavailable(
                f"Model weights '{self.model_name}' were not found in "
                f"'{self.weights_dir}' and automatic download is disabled.\n"
                "Either:\n"
                f"  1. place {os.path.basename(self.model_name)} in {self.weights_dir}, or\n"
                "  2. set YOLO_ALLOW_DOWNLOAD=true to let Ultralytics fetch it, or\n"
                "  3. set YOLO_ENABLE=false to run the mock detector.\n"
                "See docs/yolo-setup.md."
            )

        self._resolved_device = self._resolve_device()
        try:
            self._model = YOLO(path or self.model_name)
            self._model.to(self._resolved_device)
        except Exception as exc:
            raise DetectorError(
                f"Could not load YOLO weights '{path or self.model_name}' on device "
                f"'{self._resolved_device}': {exc}"
            ) from exc

        names = getattr(self._model, "names", {}) or {}
        self._model_classes = {int(k): str(v) for k, v in names.items()}
        logger.info(
            "YOLO ready: model=%s ultralytics=%s device=%s classes=%d",
            self.model_name, self._ultralytics_version, self._resolved_device,
            len(self._model_classes),
        )

    def warmup(self) -> None:
        self.load()

    @property
    def version(self) -> str:  # type: ignore[override]
        """Pinned identity: weights file + the Ultralytics build that ran it."""
        return f"{os.path.basename(self.model_name)}/ultralytics-{self._ultralytics_version}"

    # -- inference --------------------------------------------------------

    def detect(self, frame) -> list[Detection]:
        self.load()
        started = time.perf_counter()

        results = self._model.predict(
            source=frame,
            conf=self.confidence_threshold,
            device=self._resolved_device,
            verbose=False,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        detections: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                canonical = COCO_TO_CANONICAL.get(class_id)
                if canonical is None:
                    # Not in our vocabulary for this phase - dropped, not
                    # coerced into the nearest-looking class.
                    continue
                if confidence < self.confidence_threshold:
                    continue
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                detections.append(
                    Detection(
                        class_name=canonical,
                        class_id=class_id,
                        confidence=confidence,
                        bbox_xyxy=[x1, y1, x2, y2],
                        model_name=self.name,
                        model_version=self.version,
                        inference_latency_ms=latency_ms,
                        extra={"source_class": self._model_classes.get(class_id)},
                    )
                )
        return detections

    def describe(self) -> dict[str, Any]:
        weights = self._weights_path()
        producible = sorted(set(COCO_TO_CANONICAL.values()))
        # Read the installed build even before the model is loaded. describe()
        # is what the worker logs at startup, and reporting "unknown" for a
        # version that is sitting in site-packages makes the provenance line
        # useless exactly when someone is checking it.
        if self._model is None:
            self._ultralytics_version = _installed_ultralytics_version()
        return {
            "detector": self.name,
            "model_name": self.model_name,
            "model_version": self.version,
            "ultralytics_version": self._ultralytics_version,
            "device": self._resolved_device if self._model else self._resolve_device(),
            "weights_available": bool(weights) or self.allow_download,
            "weights_path": weights or None,
            "classes": list(DETECTION_CLASSES),
            # Stated explicitly: the vocabulary is wider than a stock COCO model.
            "producible_classes": producible,
            "unproducible_classes": sorted(set(DETECTION_CLASSES) - set(producible)),
            "confidence_threshold": self.confidence_threshold,
            "note": (
                "Stock COCO weights cannot emit 'auto-rickshaw'; that class needs "
                "a fine-tuned model. See docs/yolo-setup.md."
            ),
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_detector(
    *,
    enabled: bool | None = None,
    model_name: str | None = None,
    confidence_threshold: float | None = None,
    device: str | None = None,
) -> ObjectDetector:
    """Pick a detector from configuration.

    Falls back to the mock whenever YOLO is disabled, so a misconfigured
    analytics box degrades to "clearly synthetic output" rather than to a crash
    loop or, worse, to silence that looks like "no vehicles detected".
    """
    if enabled is None:
        enabled = os.getenv("YOLO_ENABLE", "false").strip().lower() in ("1", "true", "yes")
    if confidence_threshold is None:
        confidence_threshold = float(os.getenv("YOLO_CONFIDENCE_THRESHOLD", "0.45"))
    if device is None:
        device = os.getenv("YOLO_DEVICE", "auto")

    if not enabled:
        return MockDetector(confidence_threshold=confidence_threshold)
    return UltralyticsYoloDetector(
        model_name=model_name,
        confidence_threshold=confidence_threshold,
        device=device,
    )


def _frame_size(frame, default: tuple[int, int]) -> tuple[int, int]:
    """Best-effort (width, height) for numpy arrays, PIL images or None."""
    shape = getattr(frame, "shape", None)
    if shape is not None and len(shape) >= 2:
        return int(shape[1]), int(shape[0])
    size = getattr(frame, "size", None)
    if isinstance(size, tuple) and len(size) == 2:
        return int(size[0]), int(size[1])
    return default
