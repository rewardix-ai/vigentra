"""Shared machinery for the feed-analysis and dataset tools.

Everything in `tools/` that has to find footage, measure a frame, measure a
plate crop or decide how hard a case is, does it through here. The tools stay
thin, and - more importantly - `analyze_feeds.py`, `mine_hard_cases.py` and
`prepare_dataset.py` cannot silently disagree about what "tiny" means, which
is exactly the class of bug this whole exercise exists to fix.

Three principles the rest of the code depends on:

**Measured, not assumed.** The size bands are derived from the distribution the
cameras actually produce (`derive_bands`), not from constants someone liked.
The physical readability floor is quoted alongside them, because the two answer
different questions: quantiles say what this estate delivers, character-pixel
arithmetic says what any recogniser needs. Both are reported; neither is
allowed to masquerade as the other.

**Detection and readability are separate properties.** A plate that is visible
and 30 px wide is a plate. `Readability` is recorded next to it, never instead
of it. Nothing here may return "no plate" because OCR failed - see
`DifficultyTag` and the classifier below.

**Unknown is a value.** Occlusion, dirt and non-standard plates cannot be
determined from pixels without a person looking. Those tags exist, are never
set automatically, and the frames that plausibly need them are routed to review
instead of being guessed at.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Feed discovery
# ---------------------------------------------------------------------------

#: `cam06_0142.jpg` (flat capture dirs) and `cam06/0142.jpg` (nested) are both
#: in use across the capture tools, so both are understood.
_FLAT = re.compile(r"^(?P<cam>[A-Za-z0-9]+)[_-](?P<seq>\d+)\.(jpg|jpeg|png)$", re.I)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".m4v"}


@dataclass
class Feed:
    """One camera's worth of footage, however it is stored on disk.

    `camera_id` is the physical camera; `feed_id` identifies this particular
    capture of it. They differ when the same camera was captured more than
    once - which is common here, and matters: two sessions of cam06 restart
    their frame numbering, so treating them as one feed would let block N of
    one session and block N of the other land in different splits while
    showing the same traffic.
    """

    camera_id: str
    #: Still frames in capture order. Empty for a video feed.
    frames: list[Path] = field(default_factory=list)
    #: Set instead of `frames` when the feed is a video file.
    video: Path | None = None
    #: Where this came from, recorded so a dataset can name its provenance.
    source: str = ""
    #: Seconds between consecutive frames, when the capture recorded it.
    #: None means unknown - and unknown is not treated as "adjacent".
    frame_interval_s: float | None = None
    #: Unique per capture session. Equal to `camera_id` unless the same camera
    #: was found under more than one root, in which case discover_feeds
    #: disambiguates it.
    feed_id: str = ""

    def __post_init__(self) -> None:
        if not self.feed_id:
            self.feed_id = self.camera_id

    @property
    def kind(self) -> str:
        return "video" if self.video is not None else "frames"

    @property
    def count(self) -> int:
        return 1 if self.video is not None else len(self.frames)


def discover_feeds(roots: Sequence[str | Path]) -> list[Feed]:
    """Find every camera feed under *roots*.

    Handles the three layouts this project has actually produced: a flat
    directory of `camNN_SSSS.jpg`, a directory per camera, and loose video
    files. Anything unrecognised is skipped rather than guessed at - a
    mis-parsed camera id would silently merge two junctions into one feed.
    """
    by_camera: dict[tuple[str, str], Feed] = {}
    videos: list[Feed] = []

    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in VIDEO_SUFFIXES:
                videos.append(Feed(root.stem, video=root, source=str(root)))
            continue

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in VIDEO_SUFFIXES:
                videos.append(Feed(path.stem, video=path, source=str(root)))
                continue
            if suffix not in IMAGE_SUFFIXES:
                continue

            match = _FLAT.match(path.name)
            if match:
                camera = match.group("cam")
            elif path.parent != root:
                # camNN/anything.jpg - the directory names the camera.
                camera = path.parent.name
            else:
                continue
            key = (camera, str(root))
            feed = by_camera.get(key)
            if feed is None:
                feed = by_camera[key] = Feed(camera, source=str(root))
            feed.frames.append(path)

    feeds = sorted(by_camera.values(), key=lambda f: (f.source, f.camera_id))

    # Disambiguate a camera captured under more than one root. The suffix is a
    # short stable hash of the source path rather than an index, so adding a
    # new root later does not renumber - and therefore does not reshuffle -
    # every existing feed's split assignment.
    seen = Counter(f.camera_id for f in feeds)
    for feed in feeds:
        feed.frames.sort()
        if seen[feed.camera_id] > 1:
            tag = hashlib.sha256(feed.source.encode("utf-8")).hexdigest()[:6]
            feed.feed_id = f"{feed.camera_id}.{tag}"

    return feeds + sorted(videos, key=lambda f: f.camera_id)


def sample_frames(feed: Feed, limit: int | None, *, seed: int = 0) -> list[Path]:
    """Take up to *limit* frames spread evenly across the feed.

    Evenly, not randomly: a feed is a timeline, and an even spread covers the
    whole capture window (traffic builds and empties, light changes) where a
    random draw clumps. Deterministic given the same inputs, which is what
    makes a rebuilt dataset reproducible.
    """
    frames = feed.frames
    if limit is None or limit >= len(frames) or limit <= 0:
        return list(frames)
    step = len(frames) / float(limit)
    return [frames[min(len(frames) - 1, int(i * step))] for i in range(limit)]


# ---------------------------------------------------------------------------
# Frame- and crop-level measurement
# ---------------------------------------------------------------------------


@dataclass
class FrameStats:
    """What one whole frame looks like, before anything is detected in it."""

    width: int
    height: int
    #: Variance of the Laplacian. Higher is sharper. Scene-dependent, so it is
    #: only comparable between frames of the SAME camera.
    blur_score: float
    brightness: float          #: mean luma 0..255
    contrast: float            #: std-dev of luma
    #: Fraction of pixels at or above CLIP_LEVEL - blown highlights.
    clipped_high: float
    #: Fraction of near-black pixels.
    clipped_low: float
    lighting: str              #: DAY | NIGHT | DIM - a guess, labelled as one

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


#: Luma at or above this counts as blown out.
CLIP_LEVEL = 250
#: Mean-luma boundaries for the lighting guess. These are a convenience label
#: for grouping, never an input to any accept/reject decision.
NIGHT_LUMA = 70.0
DIM_LUMA = 105.0


def measure_frame(bgr: np.ndarray) -> FrameStats:
    """Measure a whole frame. Cheap enough to run on every sampled frame."""
    height, width = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    luma = float(gray.mean())
    if luma < NIGHT_LUMA:
        lighting = "NIGHT"
    elif luma < DIM_LUMA:
        lighting = "DIM"
    else:
        lighting = "DAY"
    return FrameStats(
        width=width,
        height=height,
        blur_score=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        brightness=luma,
        contrast=float(gray.std()),
        clipped_high=float((gray >= CLIP_LEVEL).mean()),
        clipped_low=float((gray < 40).mean()),
        lighting=lighting,
    )


def estimate_skew_deg(bgr: np.ndarray) -> float | None:
    """Rough plate rotation, in degrees from horizontal, or None.

    Uses the dominant edge structure of the crop: plate glyphs and the plate's
    own border give a strong oriented response that a minimum-area rectangle
    picks up. This is an ESTIMATE and is labelled as one everywhere it is
    used - on a 25 px smear there is not enough structure to be sure, and the
    function says so by returning None rather than a confident zero.
    """
    if bgr is None or bgr.size == 0:
        return None
    height, width = bgr.shape[:2]
    if width < 20 or height < 8:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 180)
    points = cv2.findNonZero(edges)
    if points is None or len(points) < 24:
        return None
    (_c, (rw, rh), angle) = cv2.minAreaRect(points)
    if rw < rh:                       # normalise to the long side
        angle += 90.0
    while angle > 45.0:
        angle -= 90.0
    while angle < -45.0:
        angle += 90.0
    return float(angle)


ANGLE_BANDS = ((5.0, "FRONTAL"), (15.0, "SLIGHT"), (30.0, "OBLIQUE"))


def angle_category(skew_deg: float | None) -> str:
    if skew_deg is None:
        return "UNKNOWN"
    magnitude = abs(skew_deg)
    for limit, name in ANGLE_BANDS:
        if magnitude < limit:
            return name
    return "SEVERE"


# ---------------------------------------------------------------------------
# Plate size: derived bands, and the physical floor they are read against
# ---------------------------------------------------------------------------

#: An Indian single-row registration carries ~10 glyphs across the plate width.
CHARS_PER_PLATE = 10.0
#: Pixels of glyph width below which no recogniser resolves a character. This
#: is arithmetic about the information present, not a tuning knob.
MIN_CHAR_PX = 6.0
#: The floor that follows from those two: ~60 px of plate width.
PHYSICAL_FLOOR_PX = CHARS_PER_PLATE * MIN_CHAR_PX

SIZE_BANDS = ("EXTREMELY_TINY", "VERY_SMALL", "SMALL", "MEDIUM", "LARGE")


@dataclass
class SizeBands:
    """Width thresholds separating the five size bands, in pixels.

    Derived from an observed distribution by `derive_bands`. `provenance`
    records which distribution, so a band edge can never be quoted without the
    footage it came from.
    """

    extremely_tiny: float
    very_small: float
    small: float
    medium: float
    provenance: str = ""
    sample_size: int = 0

    def classify(self, width_px: float) -> str:
        if width_px < self.extremely_tiny:
            return "EXTREMELY_TINY"
        if width_px < self.very_small:
            return "VERY_SMALL"
        if width_px < self.small:
            return "SMALL"
        if width_px < self.medium:
            return "MEDIUM"
        return "LARGE"

    def as_dict(self) -> dict:
        return {
            "edges_px": {
                "EXTREMELY_TINY": f"<{self.extremely_tiny:.0f}",
                "VERY_SMALL": f"{self.extremely_tiny:.0f}-{self.very_small:.0f}",
                "SMALL": f"{self.very_small:.0f}-{self.small:.0f}",
                "MEDIUM": f"{self.small:.0f}-{self.medium:.0f}",
                "LARGE": f">={self.medium:.0f}",
            },
            "physical_readability_floor_px": PHYSICAL_FLOOR_PX,
            "provenance": self.provenance,
            "sample_size": self.sample_size,
        }


#: Quantiles cutting the observed width distribution into the five bands.
#: Chosen so the two smallest bands together hold the bottom 40% - on this
#: estate that is where the problem lives, and a band holding 3 plates tells
#: you nothing.
BAND_QUANTILES = (0.20, 0.40, 0.65, 0.85)


def derive_bands(widths: Sequence[float], *, provenance: str = "") -> SizeBands:
    """Cut the observed width distribution into five bands.

    Quantile-based on purpose. Fixed pixel thresholds encode an assumption
    about how far away the vehicles are, and that assumption is wrong the
    moment the camera changes. Quantiles adapt; the physical floor
    (`PHYSICAL_FLOOR_PX`) is reported alongside and does not move, so the two
    can be compared - which is the interesting comparison on this estate.

    Falls back to thresholds anchored on the physical floor when there are too
    few observations for quantiles to mean anything, and says so in
    `provenance`.
    """
    clean = sorted(float(w) for w in widths if w and w > 0)
    if len(clean) < 20:
        return SizeBands(
            extremely_tiny=PHYSICAL_FLOOR_PX / 3.0,     # 20
            very_small=PHYSICAL_FLOOR_PX / 1.5,         # 40
            small=PHYSICAL_FLOOR_PX,                    # 60
            medium=PHYSICAL_FLOOR_PX * 2.0,             # 120
            provenance=f"{provenance} (too few observations for quantiles; "
                       f"anchored on the {PHYSICAL_FLOOR_PX:.0f}px physical floor)",
            sample_size=len(clean),
        )
    edges = [float(np.quantile(clean, q)) for q in BAND_QUANTILES]
    # Quantiles can collide when the distribution is very concentrated; nudge
    # them apart so classify() stays a partition.
    for i in range(1, len(edges)):
        edges[i] = max(edges[i], edges[i - 1] + 1.0)
    return SizeBands(*edges, provenance=provenance, sample_size=len(clean))


# ---------------------------------------------------------------------------
# Readability and difficulty - kept strictly apart from "is there a plate"
# ---------------------------------------------------------------------------


class Readability(str, Enum):
    """Whether a plate that EXISTS can be read. Never whether it exists."""

    READABLE = "READABLE"
    #: Enough pixels, but blur/glare/angle defeated the recogniser.
    UNREADABLE_QUALITY = "UNREADABLE_QUALITY"
    #: Physically too few pixels to carry the characters.
    UNREADABLE_TOO_SMALL = "UNREADABLE_TOO_SMALL"
    #: OCR was never run on this crop.
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class DifficultyTag(str, Enum):
    """Why a sample is hard. A sample may carry several."""

    TINY = "tiny"
    UNREADABLE = "unreadable"
    BLUR = "blur"
    LOW_LIGHT = "low_light"
    OVEREXPOSED = "overexposed"
    GLARE = "glare"
    ANGLED = "angled"
    PARTIAL = "partial"
    LOW_CONTRAST = "low_contrast"
    MULTI_VEHICLE = "multi_vehicle"
    LOW_CONFIDENCE = "low_confidence"
    VEHICLE_WITHOUT_VISIBLE_PLATE = "vehicle_without_visible_plate"
    #: Set by a person, never automatically - see NEEDS_HUMAN below.
    OCCLUDED = "occluded"
    DIRTY_OR_DAMAGED = "dirty_or_damaged"
    NON_STANDARD = "non_standard"
    FALSE_POSITIVE = "false_positive"


#: Tags no pixel measurement can honestly assign. A crop that might warrant one
#: is routed to human review; nothing here is ever set by the classifier.
NEEDS_HUMAN = frozenset({
    DifficultyTag.OCCLUDED,
    DifficultyTag.DIRTY_OR_DAMAGED,
    DifficultyTag.NON_STANDARD,
    DifficultyTag.FALSE_POSITIVE,
})

#: Crop-level thresholds for the automatic tags.
#:
#: These match the runtime engine's own definitions (anpr/enhance.py) so that
#: "blurred" in the dataset means what "blurred" means in the pipeline. If they
#: drift apart, the dataset stops describing the system it is meant to fix.
BLUR_THRESHOLD = 90.0          # variance of Laplacian, per anpr.enhance
DARK_LUMA = 85.0               # per anpr.enhance.Quality.is_dark
BRIGHT_LUMA = 200.0
GLARE_FRACTION = 0.035         # per anpr.enhance.Quality.is_glared
LOW_CONTRAST_STD = 32.0
LOW_CONF = 0.35
EDGE_MARGIN_PX = 3


@dataclass
class PlateSample:
    """One observed plate box, with everything needed to judge it later.

    Field names match the metadata schema the dataset is required to carry, so
    a row of `metadata.csv` is this object flattened, with nothing renamed on
    the way out.
    """

    image_id: str
    feed_id: str
    frame_number: int
    frame_path: str
    #: xyxy in ORIGINAL frame pixels.
    bbox: tuple[float, float, float, float]
    plate_width_px: float
    plate_height_px: float
    plate_area_px: float
    plate_area_frac: float
    plate_aspect: float
    plate_size_category: str
    detection_confidence: float
    #: "frame" | "roi" | "prior" - which detector pass produced it.
    detection_source: str
    blur_score: float
    brightness: float
    contrast: float
    glare: float
    crop_quality: float
    skew_deg: float | None
    angle_category: str
    touches_border: bool
    lighting: str
    vehicles_in_frame: int
    readability: str = Readability.NOT_ATTEMPTED.value
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    difficulty: list[str] = field(default_factory=list)
    needs_human_review: bool = False
    review_reason: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["bbox"] = [round(v, 2) for v in self.bbox]
        d["difficulty"] = list(self.difficulty)
        for key, value in d.items():
            if isinstance(value, float):
                d[key] = round(value, 4)
        return d


def classify_difficulty(
    sample: PlateSample,
    bands: SizeBands,
    *,
    physical_floor_px: float = PHYSICAL_FLOOR_PX,
) -> list[str]:
    """Tag why this sample is hard, from measurements only.

    Deliberately does NOT tag occlusion, dirt, non-standard plates or false
    positives: none of those is decidable from a crop's statistics, and a
    guessed label is worse than an absent one because it trains the model on
    fiction. `needs_human_review` is set instead.
    """
    tags: list[str] = []

    if sample.plate_size_category in ("EXTREMELY_TINY", "VERY_SMALL"):
        tags.append(DifficultyTag.TINY.value)
    if sample.blur_score < BLUR_THRESHOLD:
        tags.append(DifficultyTag.BLUR.value)
    if sample.brightness < DARK_LUMA:
        tags.append(DifficultyTag.LOW_LIGHT.value)
    if sample.brightness > BRIGHT_LUMA:
        tags.append(DifficultyTag.OVEREXPOSED.value)
    if sample.glare > GLARE_FRACTION:
        tags.append(DifficultyTag.GLARE.value)
    if sample.contrast < LOW_CONTRAST_STD:
        tags.append(DifficultyTag.LOW_CONTRAST.value)
    if sample.angle_category in ("OBLIQUE", "SEVERE"):
        tags.append(DifficultyTag.ANGLED.value)
    if sample.touches_border:
        tags.append(DifficultyTag.PARTIAL.value)
    if sample.vehicles_in_frame >= 3:
        tags.append(DifficultyTag.MULTI_VEHICLE.value)
    if sample.detection_confidence < LOW_CONF:
        tags.append(DifficultyTag.LOW_CONFIDENCE.value)
    if sample.readability in (Readability.UNREADABLE_QUALITY.value,
                              Readability.UNREADABLE_TOO_SMALL.value):
        tags.append(DifficultyTag.UNREADABLE.value)

    return tags


def readability_of(sample: PlateSample, text: str, confidence: float,
                   *, physical_floor_px: float = PHYSICAL_FLOOR_PX) -> str:
    """Decide the readability of a plate that has already been OCR'd.

    The order matters and encodes the rule this whole task turns on: a failed
    read on a plate with too few pixels is NOT the same event as a failed read
    on a plate with plenty. The first is physics and the detector should still
    be trained to find it; the second is a model deficiency and is the case
    worth mining hardest.
    """
    if text:
        return Readability.READABLE.value
    if sample.plate_width_px < physical_floor_px:
        return Readability.UNREADABLE_TOO_SMALL.value
    return Readability.UNREADABLE_QUALITY.value


# ---------------------------------------------------------------------------
# Deterministic splitting
# ---------------------------------------------------------------------------

#: Consecutive frames of one vehicle are near-duplicates. Frames are grouped
#: into blocks of this many and the BLOCK is assigned to a split, so no vehicle
#: sequence can straddle train and val/test.
TIME_BLOCK_FRAMES = 20


def split_of(camera_id: str, frame_number: int, *, seed: int = 0,
             block: int = TIME_BLOCK_FRAMES,
             ratios: tuple[float, float, float] = (0.70, 0.15, 0.15)) -> str:
    """Assign (camera, time block) to train/val/test, stably.

    A stable hash rather than a shuffle: rebuilding the dataset from the same
    footage must reproduce the same split exactly, including when new frames
    are added in the middle - which a positional shuffle would scramble.
    """
    key = f"{seed}:{camera_id}:{frame_number // max(1, block)}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    point = int.from_bytes(digest[:8], "big") / float(1 << 64)
    train, val, _test = ratios
    if point < train:
        return "train"
    if point < train + val:
        return "val"
    return "test"


def frame_number_of(path: Path) -> int:
    """Sequence number from a capture filename, or 0 when it carries none."""
    match = _FLAT.match(path.name)
    if match:
        return int(match.group("seq"))
    digits = re.findall(r"\d+", path.stem)
    return int(digits[-1]) if digits else 0


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def to_yolo(bbox: Sequence[float], width: int, height: int) -> tuple[float, ...]:
    """xyxy in pixels -> YOLO cx,cy,w,h normalised, clamped to the frame."""
    x1, y1, x2, y2 = bbox
    x1, x2 = max(0.0, min(x1, width)), max(0.0, min(x2, width))
    y1, y2 = max(0.0, min(y1, height)), max(0.0, min(y2, height))
    cx = (x1 + x2) / 2.0 / width
    cy = (y1 + y2) / 2.0 / height
    bw = (x2 - x1) / width
    bh = (y2 - y1) / height
    return tuple(round(max(0.0, min(1.0, v)), 6) for v in (cx, cy, bw, bh))


def percentiles(values: Sequence[float],
                points: Sequence[int] = (5, 25, 50, 75, 90, 95, 99)) -> dict:
    """Percentiles of a sample, or an empty dict when there is no sample.

    Percentiles, never a mean: plate sizes on a junction camera are strongly
    skewed by a handful of close vehicles, and a mean describes none of them.
    """
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {}
    array = np.asarray(clean, dtype=float)
    out = {f"p{p}": round(float(np.percentile(array, p)), 2) for p in points}
    out["min"] = round(float(array.min()), 2)
    out["max"] = round(float(array.max()), 2)
    out["n"] = len(clean)
    return out


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
