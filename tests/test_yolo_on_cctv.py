"""Real YOLO inference against the real CCTV clip this deployment serves.

Every other detection test runs the mock detector: fast, deterministic, and
completely incapable of telling you whether the model works. This file is the
one that would notice if the weights, the Ultralytics pin, the COCO mapping or
the clip itself broke.

It is skipped - not failed - when the analytics extras or the weights are
absent, because the CV stack is a ~2 GB opt-in and the base suite must stay
runnable without it. See docs/yolo-setup.md.

    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install -r services/edge-worker/requirements-yolo.txt
    # place yolo11n.pt in ./weights
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CLIP = REPO / "data" / "videos" / "traffic" / "traffic_live.mp4"
WEIGHTS_DIR = REPO / "weights"
WEIGHTS = WEIGHTS_DIR / "yolo11n.pt"

cv2 = pytest.importorskip("cv2", reason="analytics extras not installed")
pytest.importorskip("ultralytics", reason="analytics extras not installed")

pytestmark = [
    pytest.mark.skipif(not WEIGHTS.is_file(), reason=f"no weights at {WEIGHTS}"),
    pytest.mark.skipif(not CLIP.is_file(), reason=f"no clip at {CLIP}"),
]


def _load_detectors():
    """Import the edge module by path.

    The edge worker and the central API both use the package name `app`, so a
    plain import would pick up whichever landed on sys.path first.
    """
    spec = importlib.util.spec_from_file_location(
        "edge_detectors_real", REPO / "services" / "edge-worker" / "app" / "detectors.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["edge_detectors_real"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def detectors():
    return _load_detectors()


@pytest.fixture(scope="module")
def detector(detectors):
    instance = detectors.UltralyticsYoloDetector(
        model_name="yolo11n.pt",
        weights_dir=str(WEIGHTS_DIR),
        confidence_threshold=0.45,
    )
    instance.load()
    return instance


@pytest.fixture(scope="module")
def sampled(detector):
    """Run the model over the clip, sampling every 15th frame (~0.5s apart)."""
    capture = cv2.VideoCapture(str(CLIP))
    results = []
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % 15 == 0:
                results.append((index, frame.shape, detector.detect(frame)))
            index += 1
    finally:
        capture.release()
    assert results, "the clip decoded no frames"
    return results


def test_the_model_actually_loads(detector):
    """Weights, pin and device resolve - the thing every other test assumes."""
    assert "yolo11n.pt" in detector.version
    assert "ultralytics-8.4.123" in detector.version, (
        "the pin moved; a stored detection records the build that produced it"
    )
    assert len(detector._model_classes) == 80, "expected stock COCO weights"


def test_it_finds_traffic_in_the_traffic_clip(sampled):
    """The substantive check: real objects, in a real street scene."""
    found: dict[str, int] = {}
    for _, _, detections in sampled:
        for item in detections:
            found[item.class_name] = found.get(item.class_name, 0) + 1

    assert found, "the model found nothing at all in a busy street clip"
    # It is a road, so vehicles and people are both expected. Asserting on the
    # union rather than one exact class keeps this from breaking if the clip is
    # ever swapped for different footage of the same kind of scene.
    assert found.keys() & {"car", "motorcycle", "bus", "truck", "bicycle"}, (
        f"no vehicles detected in a traffic clip; got {found}"
    )
    assert "person" in found, f"no people detected in a street scene; got {found}"
    assert sum(found.values()) > 50, f"suspiciously few detections: {found}"


def test_every_class_is_in_the_canonical_vocabulary(detectors, sampled):
    """COCO's 80 classes are filtered to the seven the registry accepts.

    A traffic light must never arrive as a vehicle: unmapped classes are
    dropped at the edge rather than guessed at.
    """
    allowed = set(detectors.DETECTION_CLASSES)
    for _, _, detections in sampled:
        for item in detections:
            assert item.class_name in allowed, f"leaked non-canonical class {item.class_name}"


def test_the_confidence_threshold_is_respected(detector, sampled):
    for _, _, detections in sampled:
        for item in detections:
            assert item.confidence >= detector.confidence_threshold


def test_boxes_stay_inside_the_frame(sampled):
    """A box outside the image would corrupt any downstream crop or overlay."""
    for _, shape, detections in sampled:
        height, width = shape[0], shape[1]
        for item in detections:
            x1, y1, x2, y2 = item.bbox_xyxy
            assert 0 <= x1 < x2 <= width, f"x out of frame: {item.bbox_xyxy} in {width}x{height}"
            assert 0 <= y1 < y2 <= height, f"y out of frame: {item.bbox_xyxy} in {width}x{height}"


def test_detections_carry_their_provenance(sampled):
    """A stored detection has to say which build produced it."""
    for _, _, detections in sampled:
        for item in detections:
            assert item.model_name
            assert "ultralytics-" in item.model_version
            assert item.inference_latency_ms is not None


def test_the_same_frame_gives_the_same_detection_id(detector, sampled):
    """Re-ingesting a replayed frame must collapse, not inflate counts."""
    index, _, detections = next((row for row in sampled if row[2]), (None, None, None))
    assert detections, "expected at least one frame with detections"

    stamp = "2026-08-21T04:00:00Z"
    first = [d.detection_id("VIGENTRA-TRAFFIC-AHM-0001", stamp, i) for i, d in enumerate(detections)]
    second = [d.detection_id("VIGENTRA-TRAFFIC-AHM-0001", stamp, i) for i, d in enumerate(detections)]
    assert first == second
    assert len(set(first)) == len(first), "ids collided within one frame"


def test_stock_weights_cannot_claim_an_auto_rickshaw(detectors, detector, sampled):
    """The honesty check.

    `auto-rickshaw` is in the canonical vocabulary because Gujarat roads are
    full of them, but COCO has no such class and no amount of confidence
    tuning invents one. Claiming otherwise in a demo would be a lie the
    audience cannot check.
    """
    assert "auto-rickshaw" in detectors.DETECTION_CLASSES
    assert "auto-rickshaw" not in set(detectors.COCO_TO_CANONICAL.values())

    for _, _, detections in sampled:
        for item in detections:
            assert item.class_name != "auto-rickshaw"

    described = detector.describe()
    assert "auto-rickshaw" not in described["producible_classes"]


def test_provenance_is_reported_before_the_model_loads(detectors):
    """`describe()` runs at worker startup, before any inference.

    It used to report `ultralytics-unknown` there because the version was only
    captured inside `load()` - so the one line an operator reads while checking
    which build is about to run said nothing useful.
    """
    fresh = detectors.UltralyticsYoloDetector(
        model_name="yolo11n.pt", weights_dir=str(WEIGHTS_DIR)
    )
    described = fresh.describe()
    assert described["ultralytics_version"] == "8.4.123"
    assert "unknown" not in described["model_version"]
    assert described["weights_available"] is True
