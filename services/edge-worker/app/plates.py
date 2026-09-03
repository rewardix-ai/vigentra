"""Number-plate reading (ANPR), at the edge.

A registration number identifies a vehicle and, through the registry, a person.
That makes this the most sensitive thing the platform produces, and the design
reflects it:

  * **It is off by default.** `ANPR_ENABLE` must be set deliberately.
  * **Only vehicles are examined.** A person detection is never cropped or read.
  * **Garbage is discarded, not stored.** A read that does not match a plausible
    Indian registration format is dropped at the edge. Half-read text is worse
    than no text: it looks like evidence and is not.
  * **The frame never leaves the device.** OCR runs here; the central API
    receives characters and a box, never an image.

The reader is behind an interface for the same reason the detector is: the OCR
engine is a deployment choice, and a stored plate must record which build
produced it.
"""
from __future__ import annotations

import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("vigentra.edge.plates")

#: Only these classes are examined. Reading text off a person is not a thing
#: this system does.
PLATE_BEARING_CLASSES = frozenset({"car", "motorcycle", "bus", "truck", "auto-rickshaw"})

#: Indian registration formats, loosely: state code, district digits, an
#: optional series, then the number. Covers `GJ01AB1234`, `GJ1A1234`,
#: `GJ01A1234`. Deliberately strict - see the module docstring.
PLATE_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{3,4}$")

#: Characters OCR habitually confuses. Applied per SEGMENT, never blanket: a
#: plate is letters-digits-letters-digits, and "fixing" the whole string turns
#: the district number 01 into the letters OI.
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"})
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                           "S": "5", "B": "8", "Z": "2", "G": "6"})

#: Every Indian state and union-territory code. A two-letter string that is not
#: one of these is not a registration number, however plate-shaped it looks -
#: this is what stops OCR's "OJ" or "6J" being stored as if it were real.
STATE_CODES = frozenset("""
AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ
NL OD OR PB PY RJ SK TN TR TS UK UP UT WB
""".split())

@dataclass
class PlateRead:
    """One plate, read off one vehicle."""

    text: str
    confidence: float
    #: xyxy in the coordinates of the FULL frame, not the vehicle crop.
    bbox_xyxy: list[float]
    reader_name: str
    reader_version: str
    latency_ms: float | None = None


class PlateReadUnavailable(RuntimeError):
    """The OCR engine or its model is not installed. Actionable, not silent."""


class PlateReader(ABC):
    """Reads a registration number from a vehicle crop, or returns nothing."""

    name = "plate_reader"
    version = "0"

    @abstractmethod
    def read(self, frame: Any, vehicle_bbox: list[float]) -> PlateRead | None:
        """Return a plate read for this vehicle, or None if nothing legible."""

    def describe(self) -> dict[str, Any]:
        return {"reader": self.name, "version": self.version}

    def warmup(self) -> None:  # pragma: no cover - optional hook
        return None


def _coerce(segment: str, table: dict) -> tuple[str, int]:
    """Apply a confusion table, reporting how many characters it had to change."""
    fixed = segment.translate(table)
    edits = sum(1 for a, b in zip(segment, fixed) if a != b)
    return fixed, edits


def normalise_plate(raw: str) -> str | None:
    """Clean an OCR string into a registration number, or reject it.

    Returns None rather than a best guess. The whole point of the strictness is
    that a plate in this system is either right or absent - an operator acting
    on `GJ01AB1Z34` because OCR misread a digit is the failure mode worth
    engineering against.

    An Indian plate is state / district / series / number, but where one
    segment ends and the next begins is genuinely ambiguous in a bare string.
    `GJ01AB1234` wants a two-digit district; `DL1LCE5987` wants one, and reading
    it greedily turns the series letter L into a digit and yields the wrong
    registration. So every legal split is tried and the one needing the FEWEST
    character corrections wins - the reading closest to what OCR actually saw.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if not 6 <= len(cleaned) <= 11:
        return None

    best: tuple[int, str] | None = None
    for district_len in (1, 2):
        for series_len in (0, 1, 2, 3):
            for number_len in (4, 3):
                if 2 + district_len + series_len + number_len != len(cleaned):
                    continue
                cursor = 0
                state_raw = cleaned[cursor:cursor + 2]; cursor += 2
                district_raw = cleaned[cursor:cursor + district_len]; cursor += district_len
                series_raw = cleaned[cursor:cursor + series_len]; cursor += series_len
                number_raw = cleaned[cursor:]

                state, e1 = _coerce(state_raw, _TO_LETTER)
                district, e2 = _coerce(district_raw, _TO_DIGIT)
                series, e3 = _coerce(series_raw, _TO_LETTER)
                number, e4 = _coerce(number_raw, _TO_DIGIT)

                candidate = f"{state}{district}{series}{number}"
                if not PLATE_PATTERN.match(candidate):
                    continue
                if state not in STATE_CODES:
                    continue
                edits = e1 + e2 + e3 + e4
                if best is None or edits < best[0]:
                    best = (edits, candidate)

    return best[1] if best else None


def plate_region(frame: Any, vehicle_bbox: list[float]) -> tuple[Any, tuple[int, int]] | None:
    """Crop where a plate lives on a vehicle: low, and horizontally central.

    Returns the crop and its (x, y) offset in the full frame so any box can be
    reported in frame coordinates rather than crop coordinates.
    """
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in vehicle_bbox)
    vehicle_height = y2 - y1

    top = int(max(0, y1 + vehicle_height * 0.55))
    bottom = int(min(height, y2))
    left = int(max(0, x1))
    right = int(min(width, x2))

    if bottom - top < 8 or right - left < 20:
        # Too few pixels to hold a readable plate. Skipping is honest; upscaling
        # a 12-pixel strip just manufactures confident nonsense.
        return None
    return frame[top:bottom, left:right], (left, top)


#: Crop width bounds for OCR. A plate is roughly a fifth of the vehicle's
#: width, so a crop narrower than this leaves too few pixels per character;
#: wider than the ceiling only costs time. Forcing every crop to a single
#: width was worse than either: downscaling an 880 px crop to 480 lost plates
#: the engine had been reading fine.
OCR_MIN_WIDTH = 320
OCR_MAX_WIDTH = 1280


def assemble_lines(results: list[tuple]) -> list[tuple[str, float, list[float]]]:
    """Join OCR fragments that sit on the same line of text.

    EasyOCR happily returns a single plate as several boxes - a real read of
    this footage came back as 'DL' and '1LCE5987'. Judged one at a time neither
    is a registration number, so the plate is lost. Grouping by vertical
    overlap and concatenating left-to-right recovers it.

    Returns (text, confidence, bbox) per line. Confidence is the WEAKEST
    fragment's: a line is only as trustworthy as its worst-read character.
    """
    boxes = []
    for box, text, confidence in results:
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        boxes.append({
            "text": text,
            "confidence": float(confidence),
            "x1": min(xs), "x2": max(xs),
            "y1": min(ys), "y2": max(ys),
            "cy": (min(ys) + max(ys)) / 2.0,
            "height": max(ys) - min(ys),
        })

    lines: list[list[dict]] = []
    for item in sorted(boxes, key=lambda b: b["cy"]):
        placed = False
        for line in lines:
            # Same line if the centre sits within half a character height of it.
            reference = line[0]
            tolerance = max(reference["height"], item["height"]) * 0.6
            if abs(item["cy"] - reference["cy"]) <= tolerance:
                line.append(item)
                placed = True
                break
        if not placed:
            lines.append([item])

    assembled = []
    for line in lines:
        line.sort(key=lambda b: b["x1"])
        text = "".join(part["text"] for part in line)
        confidence = min(part["confidence"] for part in line)
        bbox = [
            min(p["x1"] for p in line), min(p["y1"] for p in line),
            max(p["x2"] for p in line), max(p["y2"] for p in line),
        ]
        assembled.append((text, confidence, bbox))
    return assembled


class EasyOcrPlateReader(PlateReader):
    """EasyOCR over the plate region of a detected vehicle.

    Chosen because it runs on CPU and needs no system package. Its models are
    downloaded once on first use; in an air-gapped or proxied environment place
    `craft_mlt_25k.pth` and `english_g2.pth` in `~/.EasyOCR/model/` yourself.
    """

    name = "easyocr-plate"

    def __init__(
        self,
        *,
        min_confidence: float | None = None,
        languages: list[str] | None = None,
        gpu: bool = False,
        min_plate_width: int = 40,
    ) -> None:
        # 0.55, not 0.40. Measured on real 4K street footage: a 0.77 read gave
        # the correct plate, while a 0.49 read of the SAME car one second later
        # returned DL11CES9871 for DL1LCE5987 - plate-shaped, state code valid,
        # and wrong. Format validation cannot catch that; only the score can.
        # A missing plate costs a lookup, a wrong one costs somebody a knock on
        # the door, so the floor sits above where misreads were observed.
        self.min_confidence = (
            float(os.getenv("ANPR_MIN_CONFIDENCE", "0.55"))
            if min_confidence is None
            else min_confidence
        )
        self.languages = languages or os.getenv("ANPR_LANGS", "en").split(",")
        self.gpu = gpu
        self.min_plate_width = min_plate_width
        self._reader: Any = None
        self._version = "unknown"

    def load(self) -> None:
        if self._reader is not None:
            return
        try:
            import easyocr
        except ImportError as exc:
            raise PlateReadUnavailable(
                "EasyOCR is not installed. Install the ANPR extra:\n"
                "    pip install -r services/edge-worker/requirements-anpr.txt\n"
                "or run with ANPR_ENABLE=false."
            ) from exc

        self._version = getattr(easyocr, "__version__", "unknown")
        try:
            self._reader = easyocr.Reader(self.languages, gpu=self.gpu, verbose=False)
        except Exception as exc:
            raise PlateReadUnavailable(
                "EasyOCR could not load its recognition models. On a network "
                "that intercepts TLS the automatic download fails; place "
                "craft_mlt_25k.pth and english_g2.pth in ~/.EasyOCR/model/ "
                f"and retry. Underlying error: {exc}"
            ) from exc
        logger.info("plate reader ready: easyocr %s (cpu=%s)", self._version, not self.gpu)

    def warmup(self) -> None:
        self.load()

    @property
    def version(self) -> str:  # type: ignore[override]
        return f"easyocr-{self._version}"

    def read(self, frame: Any, vehicle_bbox: list[float]) -> PlateRead | None:
        self.load()
        region = plate_region(frame, vehicle_bbox)
        if region is None:
            return None
        crop, (offset_x, offset_y) = region

        import cv2

        # Resize only when the crop is outside the useful band: upscale a
        # distant plate so its characters resolve, shrink a 4K close-up so OCR
        # is not chewing megapixels for nothing. A crop already in the band is
        # left exactly as the camera saw it.
        width = max(1, crop.shape[1])
        scale = 1.0
        if width < OCR_MIN_WIDTH:
            scale = min(4.0, OCR_MIN_WIDTH / width)
        elif width > OCR_MAX_WIDTH:
            scale = OCR_MAX_WIDTH / width
        if abs(scale - 1.0) > 0.01:
            interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=interpolation)

        started = time.perf_counter()
        try:
            found = self._reader.readtext(crop, detail=1, paragraph=False)
        except Exception as exc:  # pragma: no cover - engine-level failure
            logger.warning("plate OCR failed on one crop: %s", exc)
            return None
        latency_ms = (time.perf_counter() - started) * 1000

        # Each fragment on its own, plus each assembled line. A plate split
        # across boxes only parses once its pieces are joined.
        candidates: list[tuple[str, float, list[float]]] = [
            (text, float(confidence), [
                min(p[0] for p in box), min(p[1] for p in box),
                max(p[0] for p in box), max(p[1] for p in box),
            ])
            for box, text, confidence in found
        ]
        candidates.extend(assemble_lines(found))

        best: PlateRead | None = None
        for text, confidence, box in candidates:
            if confidence < self.min_confidence:
                continue
            plate = normalise_plate(text)
            if plate is None:
                continue
            candidate = PlateRead(
                text=plate,
                confidence=round(confidence, 4),
                bbox_xyxy=[
                    round(box[0] / scale + offset_x, 2),
                    round(box[1] / scale + offset_y, 2),
                    round(box[2] / scale + offset_x, 2),
                    round(box[3] / scale + offset_y, 2),
                ],
                reader_name=self.name,
                reader_version=self.version,
                latency_ms=round(latency_ms, 2),
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    def describe(self) -> dict[str, Any]:
        return {
            "reader": self.name,
            "version": self.version,
            "min_confidence": self.min_confidence,
            "languages": self.languages,
            "examines": sorted(PLATE_BEARING_CLASSES),
            "note": (
                "Reads only vehicle crops. Text that does not match an Indian "
                "registration format is discarded at the edge and never sent."
            ),
        }


class DisabledPlateReader(PlateReader):
    """ANPR switched off. Present so callers need no None checks."""

    name = "disabled"
    version = "0"

    def read(self, frame: Any, vehicle_bbox: list[float]) -> PlateRead | None:
        return None

    def describe(self) -> dict[str, Any]:
        return {"reader": self.name, "enabled": False, "note": "ANPR_ENABLE is not set."}


def build_plate_reader() -> PlateReader:
    """Reader chosen by configuration. Off unless explicitly enabled."""
    if os.getenv("ANPR_ENABLE", "false").strip().lower() not in {"1", "true", "yes"}:
        return DisabledPlateReader()
    return EasyOcrPlateReader()
