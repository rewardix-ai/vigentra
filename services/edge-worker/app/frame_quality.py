"""Frame-quality routing for edge inference.

Classifies a frame before it reaches the detector and decides what to do:

    normal        pass the original frame straight through
    low_light     optionally apply CLAHE / gamma, then detect, and flag it
    overexposed   detect on the ORIGINAL and flag it - see the honesty note
    blurred       flag, and skip inference when it is bad enough to be useless

**What enhancement can and cannot do.** Boosting a dark frame recovers
contrast that is present but compressed into a narrow band — that genuinely
helps. It does not create information that was never captured. Where highlights
are clipped to pure white, or where a number plate is a saturated smear under
direct headlight glare, the pixels carry no recoverable signal and any
"enhanced" version is the algorithm inventing plausible texture. That is worse
than useless for evidence, so overexposed frames are detected on the original
and flagged rather than dressed up.

Every processed frame records which path it took, so a downstream reviewer can
always tell whether a detection came from original or enhanced pixels.

numpy is used when available; a pure-Python fallback keeps the class importable
(and unit-testable) on a machine with no CV stack.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger("sentinel.edge.quality")

try:  # optional, and genuinely optional
    import numpy as _np
except Exception:  # pragma: no cover - exercised on minimal installs
    _np = None

try:
    import cv2 as _cv2
except Exception:  # pragma: no cover
    _cv2 = None


class FrameQuality(str, Enum):
    NORMAL = "normal"
    LOW_LIGHT = "low_light"
    OVEREXPOSED = "overexposed"
    BLURRED = "blurred"


@dataclass(frozen=True)
class QualityAssessment:
    """What the router decided, and the measurements behind it."""

    quality: FrameQuality
    mean_luma: float | None = None
    laplacian_variance: float | None = None
    clipped_highlight_ratio: float | None = None
    enhancement_applied: str | None = None
    #: True when the frame is too degraded for inference to mean anything.
    inference_skipped: bool = False
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "quality": self.quality.value,
            "mean_luma": self.mean_luma,
            "laplacian_variance": self.laplacian_variance,
            "clipped_highlight_ratio": self.clipped_highlight_ratio,
            "enhancement_applied": self.enhancement_applied,
            "inference_skipped": self.inference_skipped,
            "detail": self.detail or {},
        }


class FrameQualityRouter:
    """Classify a frame and hand back the frame that should be detected on.

    Thresholds are conservative defaults for 8-bit imagery and are meant to be
    tuned per site — a tunnel camera and a west-facing junction at sunset do
    not share a sensible low-light cutoff.
    """

    def __init__(
        self,
        *,
        low_light_luma: float = 60.0,
        overexposed_luma: float = 200.0,
        clipped_highlight_ratio: float = 0.18,
        blur_variance: float = 40.0,
        skip_blur_variance: float = 12.0,
        enhance_low_light: bool = True,
    ) -> None:
        self.low_light_luma = low_light_luma
        self.overexposed_luma = overexposed_luma
        self.clipped_highlight_ratio = clipped_highlight_ratio
        self.blur_variance = blur_variance
        # Below this the frame carries so little structure that a detection
        # would be noise dressed as a finding.
        self.skip_blur_variance = skip_blur_variance
        self.enhance_low_light = enhance_low_light

    # -- measurement ------------------------------------------------------

    def _to_gray(self, frame):
        if _np is not None and hasattr(frame, "shape"):
            array = _np.asarray(frame)
            if array.ndim == 3:
                if _cv2 is not None:
                    return _cv2.cvtColor(array, _cv2.COLOR_BGR2GRAY)
                # Rec. 601 luma, matching what cvtColor would produce.
                return (
                    0.114 * array[:, :, 0] + 0.587 * array[:, :, 1] + 0.299 * array[:, :, 2]
                )
            return array
        return frame

    def _mean_luma(self, gray) -> float:
        if _np is not None and hasattr(gray, "mean"):
            return float(_np.asarray(gray).mean())
        flat = _flatten(gray)
        return sum(flat) / len(flat) if flat else 0.0

    def _clipped_ratio(self, gray) -> float:
        """Fraction of pixels at or near full white - the glare signal."""
        if _np is not None and hasattr(gray, "shape"):
            array = _np.asarray(gray)
            return float((array >= 250).sum() / max(1, array.size))
        flat = _flatten(gray)
        if not flat:
            return 0.0
        return sum(1 for value in flat if value >= 250) / len(flat)

    def _laplacian_variance(self, gray) -> float:
        """Focus measure. High variance = crisp edges; low = blurred."""
        if _cv2 is not None and _np is not None and hasattr(gray, "shape"):
            array = _np.asarray(gray, dtype="uint8")
            return float(_cv2.Laplacian(array, _cv2.CV_64F).var())
        if _np is not None and hasattr(gray, "shape"):
            array = _np.asarray(gray, dtype=float)
            if array.ndim != 2 or min(array.shape) < 3:
                return 0.0
            # 4-neighbour Laplacian without cv2.
            centre = array[1:-1, 1:-1]
            lap = (
                array[:-2, 1:-1] + array[2:, 1:-1] + array[1:-1, :-2] + array[1:-1, 2:]
                - 4 * centre
            )
            return float(lap.var())
        # Pure-Python 4-neighbour Laplacian over a nested list. Measures EDGE
        # energy, not intensity spread - a uniformly grey frame and a uniformly
        # bright one are both edgeless, and plain variance would not say so.
        rows = gray if isinstance(gray, (list, tuple)) else []
        if len(rows) < 3 or not isinstance(rows[0], (list, tuple)) or len(rows[0]) < 3:
            return 0.0
        responses: list[float] = []
        for y in range(1, len(rows) - 1):
            for x in range(1, len(rows[y]) - 1):
                responses.append(
                    float(rows[y - 1][x]) + float(rows[y + 1][x])
                    + float(rows[y][x - 1]) + float(rows[y][x + 1])
                    - 4.0 * float(rows[y][x])
                )
        if not responses:
            return 0.0
        mean = sum(responses) / len(responses)
        return sum((value - mean) ** 2 for value in responses) / len(responses)

    # -- classification ---------------------------------------------------

    def classify(self, frame) -> QualityAssessment:
        """Classify without modifying anything."""
        gray = self._to_gray(frame)
        mean_luma = self._mean_luma(gray)
        clipped = self._clipped_ratio(gray)
        variance = self._laplacian_variance(gray)

        # Order matters, and not in the obvious way.
        #
        # A Laplacian focus measure is only meaningful on a frame whose
        # exposure is in a usable range. Darkness compresses contrast and
        # clipping destroys it outright, so BOTH depress edge variance for
        # reasons that have nothing to do with focus. Checking blur first
        # therefore mislabels every dark frame and every glared frame as
        # "blurred" - which then routes them away from the low-light
        # enhancement that might actually have helped.
        #
        # So: exposure first, blur only on a frame where the measure means
        # something. The variance is still reported in all cases.
        if mean_luma >= self.overexposed_luma or clipped >= self.clipped_highlight_ratio:
            return QualityAssessment(
                quality=FrameQuality.OVEREXPOSED,
                mean_luma=mean_luma,
                laplacian_variance=variance,
                clipped_highlight_ratio=clipped,
                # Nothing is recoverable from a clipped highlight, so there is
                # no point spending inference on a frame that is mostly white.
                inference_skipped=clipped >= 0.85,
                detail={
                    "reason": "clipped highlights",
                    "note": (
                        "Detail lost to clipping is not recoverable. Detection runs "
                        "on the original frame and results carry this flag."
                    ),
                },
            )

        if mean_luma <= self.low_light_luma:
            return QualityAssessment(
                quality=FrameQuality.LOW_LIGHT,
                mean_luma=mean_luma,
                laplacian_variance=variance,
                clipped_highlight_ratio=clipped,
                detail={
                    "reason": "mean luma below the low-light threshold",
                    "note": (
                        "Edge variance is expected to be low here because darkness "
                        "compresses contrast; it is not evidence of blur."
                    ),
                },
            )

        # Well-exposed frame: now the focus measure is trustworthy.
        if variance < self.blur_variance:
            return QualityAssessment(
                quality=FrameQuality.BLURRED,
                mean_luma=mean_luma,
                laplacian_variance=variance,
                clipped_highlight_ratio=clipped,
                inference_skipped=variance < self.skip_blur_variance,
                detail={"reason": "low edge variance on a well-exposed frame"},
            )

        return QualityAssessment(
            quality=FrameQuality.NORMAL,
            mean_luma=mean_luma,
            laplacian_variance=variance,
            clipped_highlight_ratio=clipped,
        )

    # -- enhancement ------------------------------------------------------

    def _enhance_low_light(self, frame) -> tuple[Any, str | None]:
        """CLAHE on the luminance channel, falling back to a gamma lift.

        CLAHE is preferred because it works on local neighbourhoods, so it
        lifts a shadowed kerb without blowing out an already-lit carriageway.
        """
        if _cv2 is None or _np is None:
            return frame, None
        try:
            array = _np.asarray(frame)
            if array.ndim == 3:
                lab = _cv2.cvtColor(array, _cv2.COLOR_BGR2LAB)
                lightness, a_channel, b_channel = _cv2.split(lab)
                clahe = _cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                merged = _cv2.merge((clahe.apply(lightness), a_channel, b_channel))
                return _cv2.cvtColor(merged, _cv2.COLOR_LAB2BGR), "clahe_lab_l"
            clahe = _cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            return clahe.apply(array.astype("uint8")), "clahe_gray"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("low-light enhancement failed, using original: %s", exc)
            return frame, None

    def route(self, frame) -> tuple[Any, QualityAssessment]:
        """Classify, then return (frame_to_detect_on, assessment).

        The returned frame is the original in every case except low light with
        enhancement enabled — and the assessment always records which it was.
        """
        assessment = self.classify(frame)

        if assessment.quality is FrameQuality.LOW_LIGHT and self.enhance_low_light:
            enhanced, method = self._enhance_low_light(frame)
            if method:
                return enhanced, QualityAssessment(
                    quality=assessment.quality,
                    mean_luma=assessment.mean_luma,
                    laplacian_variance=assessment.laplacian_variance,
                    clipped_highlight_ratio=assessment.clipped_highlight_ratio,
                    enhancement_applied=method,
                    inference_skipped=False,
                    detail={
                        **(assessment.detail or {}),
                        "provenance": "detection ran on an ENHANCED frame",
                    },
                )

        # Overexposed and blurred both detect on the original: there is nothing
        # honest to add to a clipped highlight or a smeared edge.
        return frame, assessment


def _flatten(value) -> list[float]:
    """Flatten nested lists to a flat float list (pure-Python fallback)."""
    out: list[float] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, (int, float)):
            out.append(float(item))
    return out
