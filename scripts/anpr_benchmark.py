"""Benchmark the current ANPR path against a modern plate-specific pipeline.

The point of this script is to replace the table in `docs/anpr-modernisation.md`
with numbers measured on YOUR hardware and YOUR footage. Published benchmarks
are run on tight, near-frontal plate crops; `docs/anpr.md` §7 reports 1 plate
from 67 vehicles on wide street footage. Both are true. Only the second kind of
measurement tells you what to deploy.

Three pipelines are compared over the same sampled frames:

  A  current      vehicle box -> lower-45% crop -> EasyOCR -> normalise_plate
  B  fast-alpr    frame -> plate detector -> plate OCR
  C  hybrid       frame -> plate detector -> tight crop -> EasyOCR -> normalise

C is the interesting control. If C is close to B, the win came from *locating
the plate*, and a cheaper change than swapping OCR engines would do. If B beats
C clearly, the recogniser itself is carrying the difference. Do not skip it.

Nothing here writes to the database, calls the central API, or persists a
plate. It reads a local clip and prints a table.

Usage
-----
    python scripts/anpr_benchmark.py --clip data/videos/traffic/traffic_live.mp4

    # more frames, GPU, quieter
    python scripts/anpr_benchmark.py --clip <path> --max-frames 60 --device cuda:0

Install
-------
    pip install fast-alpr[onnx-gpu]     # CUDA
    pip install fast-alpr[onnx]         # CPU fallback

EasyOCR and ultralytics are expected to be present already (they are what the
edge worker uses). Any missing engine is skipped with a note rather than
aborting the run, so a partial comparison still produces numbers.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EDGE_WORKER = REPO_ROOT / "services" / "edge-worker"

# The worker's `app` package only resolves from inside services/edge-worker.
if str(EDGE_WORKER) not in sys.path:
    sys.path.insert(0, str(EDGE_WORKER))

#: COCO classes that can carry a plate. Mirrors PLATE_BEARING_CLASSES, minus
#: auto-rickshaw, which stock COCO weights cannot emit (docs/yolo-setup.md §4).
COCO_VEHICLES = frozenset({"car", "motorcycle", "bus", "truck"})


@dataclass
class Outcome:
    """What one pipeline produced over the whole run."""

    label: str
    available: bool = True
    note: str = ""
    plates: list[tuple[str, float]] = field(default_factory=list)
    frame_times_ms: list[float] = field(default_factory=list)
    units_examined: int = 0  # vehicle crops (A) or frames (B, C)

    @property
    def total_ms(self) -> float:
        return sum(self.frame_times_ms)

    @property
    def mean_frame_ms(self) -> float:
        return statistics.mean(self.frame_times_ms) if self.frame_times_ms else 0.0

    @property
    def fps(self) -> float:
        mean = self.mean_frame_ms
        return 1000.0 / mean if mean > 0 else 0.0

    @property
    def unique(self) -> Counter:
        return Counter(text for text, _ in self.plates)


def load_frames(clip: Path, max_frames: int, interval: int):
    """Yield every `interval`-th frame, up to `max_frames` of them."""
    import cv2

    capture = cv2.VideoCapture(str(clip))
    if not capture.isOpened():
        raise SystemExit(f"could not open clip: {clip}")

    index = 0
    yielded = 0
    try:
        while yielded < max_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if index % interval == 0:
                yield frame
                yielded += 1
            index += 1
    finally:
        capture.release()


def detect_vehicles(model, frame, confidence: float) -> list[list[float]]:
    """Vehicle boxes in one frame, as xyxy floats."""
    results = model.predict(frame, conf=confidence, verbose=False)
    boxes: list[list[float]] = []
    for result in results:
        names = result.names
        for box in result.boxes:
            label = names[int(box.cls.item())]
            if label in COCO_VEHICLES:
                boxes.append([float(v) for v in box.xyxy[0].tolist()])
    return boxes


def _fastalpr_fields(result) -> tuple[str, float, list[float]] | None:
    """Pull (text, confidence, plate_box) out of a fast-alpr result.

    Written defensively on purpose: the result dataclass has changed shape
    across releases, and a benchmark that dies on an attribute rename is worse
    than one that degrades. If this returns None for everything, print one
    result with --debug and adjust.
    """
    ocr = getattr(result, "ocr", None)
    if ocr is None or getattr(ocr, "text", None) is None:
        return None
    text = str(ocr.text)
    confidence = float(getattr(ocr, "confidence", 0.0) or 0.0)

    box: list[float] = []
    detection = getattr(result, "detection", None)
    bounding = getattr(detection, "bounding_box", None) if detection else None
    if bounding is not None:
        box = [
            float(getattr(bounding, "x1", 0.0)),
            float(getattr(bounding, "y1", 0.0)),
            float(getattr(bounding, "x2", 0.0)),
            float(getattr(bounding, "y2", 0.0)),
        ]
    return text, confidence, box


def run_current(frames_and_boxes, min_confidence: float) -> Outcome:
    """Pipeline A — exactly what services/edge-worker/app/plates.py does today."""
    outcome = Outcome("A  current (vehicle crop -> EasyOCR)")
    try:
        from app.plates import EasyOcrPlateReader
    except Exception as exc:
        outcome.available = False
        outcome.note = f"could not import app.plates: {exc}"
        return outcome

    reader = EasyOcrPlateReader(min_confidence=min_confidence)
    try:
        reader.load()
    except Exception as exc:
        outcome.available = False
        outcome.note = str(exc)
        return outcome

    for frame, boxes in frames_and_boxes:
        started = time.perf_counter()
        for box in boxes:
            read = reader.read(frame, box)
            if read is not None:
                outcome.plates.append((read.text, read.confidence))
            outcome.units_examined += 1
        outcome.frame_times_ms.append((time.perf_counter() - started) * 1000)
    return outcome


def run_fastalpr(
    frames_and_boxes,
    detector_model: str,
    ocr_model: str,
    min_confidence: float,
    debug: bool,
) -> tuple[Outcome, Outcome]:
    """Pipelines B and C — both need the plate detector, so they share a load.

    B takes fast-alpr's own OCR. C takes fast-alpr's plate box, crops it, and
    hands that crop to EasyOCR so the two recognisers are compared on identical
    input.
    """
    b = Outcome(f"B  fast-alpr ({ocr_model})")
    c = Outcome("C  hybrid (plate detector -> EasyOCR)")

    try:
        from fast_alpr import ALPR
    except ImportError:
        note = "fast-alpr not installed: pip install fast-alpr[onnx-gpu]"
        b.available = c.available = False
        b.note = c.note = note
        return b, c

    try:
        alpr = ALPR(detector_model=detector_model, ocr_model=ocr_model)
    except Exception as exc:
        b.available = c.available = False
        b.note = c.note = f"could not initialise fast-alpr: {exc}"
        return b, c

    easyocr_reader = None
    try:
        from app.plates import EasyOcrPlateReader, normalise_plate

        easyocr_reader = EasyOcrPlateReader(min_confidence=min_confidence)
        easyocr_reader.load()
    except Exception as exc:
        c.available = False
        c.note = f"EasyOCR unavailable for the hybrid control: {exc}"

    import cv2  # noqa: F401  (imported for parity with the crop path below)

    printed_debug = False
    for frame, _boxes in frames_and_boxes:
        started = time.perf_counter()
        try:
            results = alpr.predict(frame)
        except Exception as exc:
            b.available = False
            b.note = f"predict failed: {exc}"
            return b, c
        b.frame_times_ms.append((time.perf_counter() - started) * 1000)
        b.units_examined += 1

        if debug and not printed_debug and results:
            print(f"[debug] raw fast-alpr result: {results[0]!r}\n")
            printed_debug = True

        plate_boxes: list[list[float]] = []
        for result in results:
            fields = _fastalpr_fields(result)
            if fields is None:
                continue
            text, confidence, box = fields
            if confidence >= min_confidence:
                # Run it through OUR validator so B is judged by the same rule
                # the platform applies. A read fast-alpr is happy with but
                # normalise_plate rejects is not a plate this system stores.
                try:
                    from app.plates import normalise_plate as _normalise

                    normalised = _normalise(text)
                except Exception:
                    normalised = text
                if normalised:
                    b.plates.append((normalised, round(confidence, 4)))
            if box and box[2] > box[0] and box[3] > box[1]:
                plate_boxes.append(box)

        if not c.available or easyocr_reader is None:
            continue

        started = time.perf_counter()
        for box in plate_boxes:
            x1, y1, x2, y2 = (int(v) for v in box)
            height, width = frame.shape[:2]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(width, x2), min(height, y2)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            crop = frame[y1:y2, x1:x2]
            # read() re-crops internally, so hand it the plate as a whole
            # "vehicle" and let its lower-45% rule be a no-op on a tight box.
            read = easyocr_reader.read(crop, [0, 0, float(x2 - x1), float(y2 - y1)])
            if read is not None:
                c.plates.append((read.text, read.confidence))
            c.units_examined += 1
        c.frame_times_ms.append((time.perf_counter() - started) * 1000)

    return b, c


def report(outcomes: list[Outcome], frames: int, vehicles: int) -> None:
    print()
    print("=" * 74)
    print(f"frames sampled: {frames}    vehicle detections: {vehicles}")
    print("=" * 74)

    for outcome in outcomes:
        print()
        print(outcome.label)
        print("-" * len(outcome.label))
        if not outcome.available:
            print(f"  SKIPPED — {outcome.note}")
            continue
        if outcome.note:
            print(f"  note: {outcome.note}")

        print(f"  plates accepted   : {len(outcome.plates)}")
        print(f"  distinct plates   : {len(outcome.unique)}")
        print(f"  mean ms per frame : {outcome.mean_frame_ms:8.1f}")
        print(f"  effective fps     : {outcome.fps:8.2f}")
        print(f"  total wall time   : {outcome.total_ms / 1000:8.2f} s")
        if outcome.units_examined:
            per_unit = outcome.total_ms / outcome.units_examined
            print(f"  ms per crop       : {per_unit:8.1f}  ({outcome.units_examined} crops)")
        if outcome.unique:
            print("  reads:")
            for text, count in outcome.unique.most_common(12):
                scores = [c for t, c in outcome.plates if t == text]
                print(f"    {text:<14} x{count:<3} conf {max(scores):.2f}")

    live = [o for o in outcomes if o.available and o.unique]
    if len(live) >= 2:
        print()
        print("agreement")
        print("---------")
        base = live[0]
        for other in live[1:]:
            shared = set(base.unique) & set(other.unique)
            only_base = set(base.unique) - set(other.unique)
            only_other = set(other.unique) - set(base.unique)
            print(f"  {base.label.split()[0]} vs {other.label.split()[0]}:")
            print(f"    both found : {sorted(shared) or '—'}")
            print(f"    only first : {sorted(only_base) or '—'}")
            print(f"    only second: {sorted(only_other) or '—'}")
        print()
        print("  A plate found by only one pipeline is not automatically a win for")
        print("  that pipeline — check the frame before believing it.")

    print()
    print("Reminder: a plate here is a probabilistic reading, and this script")
    print("stores nothing. Do not quote these numbers as accuracy without")
    print("eyeballing the frames behind them.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--clip",
        default="data/videos/traffic/traffic_live.mp4",
        help="video to benchmark against (default: the bundled traffic clip)",
    )
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--sample-interval", type=int, default=15)
    parser.add_argument("--device", default="auto", help="auto | cpu | cuda:0")
    parser.add_argument("--vehicle-model", default="yolo11n.pt")
    parser.add_argument("--vehicle-confidence", type=float, default=0.45)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--detector-model", default="yolo-v9-t-384-license-plate-end2end")
    parser.add_argument("--ocr-model", default="cct-s-v2-global-model")
    parser.add_argument("--skip-current", action="store_true", help="skip slow pipeline A")
    parser.add_argument("--debug", action="store_true", help="print one raw fast-alpr result")
    args = parser.parse_args()

    clip = Path(args.clip)
    if not clip.is_absolute():
        clip = REPO_ROOT / clip
    if not clip.exists():
        raise SystemExit(f"clip not found: {clip}")

    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit(
            "ultralytics is not installed. This script needs the analytics "
            "extras — see docs/yolo-setup.md."
        )

    print(f"clip   : {clip}")
    print(f"loading {args.vehicle_model} ...")
    model = YOLO(args.vehicle_model)
    if args.device != "auto":
        model.to(args.device)

    print(f"sampling every {args.sample_interval}th frame, {args.max_frames} frames ...")
    frames_and_boxes = []
    total_vehicles = 0
    for frame in load_frames(clip, args.max_frames, args.sample_interval):
        boxes = detect_vehicles(model, frame, args.vehicle_confidence)
        total_vehicles += len(boxes)
        frames_and_boxes.append((frame, boxes))

    if not frames_and_boxes:
        raise SystemExit("no frames decoded — is the clip readable?")

    print(f"{len(frames_and_boxes)} frames, {total_vehicles} vehicle detections")

    outcomes: list[Outcome] = []

    if args.skip_current:
        skipped = Outcome("A  current (vehicle crop -> EasyOCR)")
        skipped.available = False
        skipped.note = "skipped by --skip-current"
        outcomes.append(skipped)
    else:
        print("running A (this is the slow one — ~2.5 s per vehicle crop on CPU) ...")
        outcomes.append(run_current(frames_and_boxes, args.min_confidence))

    print("running B and C ...")
    b, c = run_fastalpr(
        frames_and_boxes,
        args.detector_model,
        args.ocr_model,
        args.min_confidence,
        args.debug,
    )
    outcomes.extend([b, c])

    report(outcomes, len(frames_and_boxes), total_vehicles)


if __name__ == "__main__":
    main()
