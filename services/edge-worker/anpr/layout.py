"""Is this plate one row of characters, or two stacked rows?

Roughly three quarters of Gujarat's fleet is two-wheelers, and almost all of
them carry a stacked plate: state and district on top, series and number
underneath. Handing that crop to a single-line recogniser reads the two rows as
one horizontal sequence and produces a string that is neither row - so the
grammar rejects it, the track never reaches consensus, and the commonest
vehicle class on the road is the one the system is worst at.

`ocr.py` already knew this. `_looks_stacked()` gates an escalation to the full
detect-then-recognise path when the fast path returns something implausible,
and `_order_lines()` re-joins whatever lines come back in reading order. That
handles the common case and it is why this module extends rather than replaces
it. Two things it could not do:

**It decides per crop.** Aspect ratio on a single blurred frame is a coin flip
near the threshold - a wide two-line plate at 20 degrees of yaw measures like a
narrow single-line one. Layout is a property of the *vehicle*, not of one look
at it, so it belongs to the track and should be voted on like anything else.

**It is aspect ratio alone.** A crop's shape is a weak proxy for what is
actually diagnostic: two bands of ink with a gap between them. The horizontal
projection profile shows that directly, and disagrees with aspect ratio often
enough to be worth computing - a tight crop of a single-line plate can be
squarer than a loose crop of a two-line one.

So: classify with both signals, keep the classification on the track, and let
it stabilise before it constrains the plate string. Layout settles faster than
the characters do - it is one bit against ten glyphs - which is exactly why it
is worth voting on separately and then trusting.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

#: The two layouts this recognises. Deliberately not an enum: these values are
#: stored on detections, voted on, and shipped to the central API, so they are
#: strings at every boundary and there is no gain in wrapping them.
SINGLE_LINE = "single_line"
TWO_LINE = "two_line"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class LayoutVerdict:
    """One crop's opinion about its own layout."""

    layout: str
    #: 0..1. Low means the two signals disagreed, or the crop was too small to
    #: profile, and the caller should weight this look accordingly.
    confidence: float
    #: Row where the bands were split, in crop pixels. None for single line.
    split_row: int | None = None

    @property
    def is_two_line(self) -> bool:
        return self.layout == TWO_LINE


#: Height/width above which shape alone suggests two rows. A single-row Indian
#: plate is about 4.5:1 (ratio ~0.22); a stacked one about 2:1 (ratio ~0.5).
#: Kept identical to ocr.STACKED_ASPECT, which this supersedes but must agree
#: with while both exist.
STACKED_ASPECT = 0.38

#: Below this height there are not enough rows to profile meaningfully and the
#: answer comes from aspect ratio alone.
MIN_PROFILE_HEIGHT = 16


def _ink_profile(crop: np.ndarray) -> np.ndarray | None:
    """Ink per row, normalised 0..1, or None when the crop is too small.

    Plate glyphs are high-contrast against their backing, so an adaptive
    threshold separates them without needing to know whether the plate is dark
    on white (private) or white on dark (commercial) - and both are on the
    road, so assuming either would halve the fleet.
    """
    if crop is None or crop.size == 0:
        return None
    height, width = crop.shape[:2]
    if height < MIN_PROFILE_HEIGHT or width < 8:
        return None

    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    grey = cv2.GaussianBlur(grey, (3, 3), 0)
    # Otsu decides the polarity for us; the ink is whichever class is rarer.
    _, binary = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.mean() > 127:
        binary = 255 - binary

    profile = binary.astype(np.float32).sum(axis=1)
    peak = profile.max()
    if peak <= 0:
        return None
    return profile / peak


def _split_from_profile(profile: np.ndarray) -> tuple[int | None, float]:
    """Find the gap between two ink bands, and how convincing it is.

    Returns the row to split at and a 0..1 separation score. The score is the
    depth of the valley relative to the bands either side of it: two rows of
    characters leave a genuine trough between them, while one row's profile
    dips only where the glyphs happen to be thin.
    """
    height = len(profile)
    if height < MIN_PROFILE_HEIGHT:
        return None, 0.0

    # Only the middle band can be the gap. A trough in the outer fifth is the
    # plate's border or its mounting, not a line break.
    lo, hi = int(height * 0.30), int(height * 0.70)
    if hi <= lo:
        return None, 0.0

    middle = profile[lo:hi]
    valley_index = int(np.argmin(middle)) + lo
    valley = float(profile[valley_index])

    upper = float(profile[:valley_index].max()) if valley_index > 0 else 0.0
    lower = float(profile[valley_index + 1:].max()) if valley_index + 1 < height else 0.0
    if upper <= 0 or lower <= 0:
        return None, 0.0

    # Both bands must carry real ink, or this is one row with a thin patch.
    weaker = min(upper, lower)
    separation = (weaker - valley) / weaker if weaker > 0 else 0.0
    return valley_index, float(np.clip(separation, 0.0, 1.0))


def classify(crop: np.ndarray) -> LayoutVerdict:
    """Classify one crop, from its shape and its ink distribution.

    Neither signal is trusted alone. Aspect ratio is cheap and survives blur
    but is fooled by a loose or a skewed crop; the projection profile is
    directly diagnostic but needs enough pixels to compute. When they agree the
    answer is confident, and when they disagree the confidence drops and the
    track's other looks get to outvote this one - which is the whole reason
    layout is tracked rather than decided per frame.
    """
    if crop is None or crop.size == 0:
        return LayoutVerdict(UNKNOWN, 0.0)

    height, width = crop.shape[:2]
    if width <= 0:
        return LayoutVerdict(UNKNOWN, 0.0)

    aspect = height / float(width)
    shape_says_two = aspect > STACKED_ASPECT
    # How far the aspect sits from the decision boundary, as confidence.
    shape_confidence = float(np.clip(abs(aspect - STACKED_ASPECT) / STACKED_ASPECT, 0.0, 1.0))

    profile = _ink_profile(crop)
    if profile is None:
        # Too small to profile. Shape is all there is, and it is a weak
        # signal on its own, so say so rather than overclaiming.
        return LayoutVerdict(
            TWO_LINE if shape_says_two else SINGLE_LINE,
            shape_confidence * 0.5,
        )

    split_row, separation = _split_from_profile(profile)
    profile_says_two = split_row is not None and separation >= 0.45

    if shape_says_two == profile_says_two:
        layout = TWO_LINE if profile_says_two else SINGLE_LINE
        confidence = float(np.clip(0.55 + 0.45 * max(shape_confidence, separation), 0.0, 1.0))
        return LayoutVerdict(layout, confidence, split_row if profile_says_two else None)

    # Disagreement. The profile is the better evidence when it is emphatic -
    # it measures the thing that defines the layout rather than correlating
    # with it - so it wins on a clear valley and loses on a marginal one.
    if separation >= 0.60:
        return LayoutVerdict(TWO_LINE, 0.45, split_row)
    if profile_says_two:
        return LayoutVerdict(TWO_LINE if shape_says_two else SINGLE_LINE, 0.30, split_row)
    return LayoutVerdict(SINGLE_LINE if not shape_says_two else TWO_LINE, 0.30, None)


def split_bands(crop: np.ndarray, verdict: LayoutVerdict | None = None
                ) -> list[np.ndarray]:
    """Cut a stacked crop into its upper and lower rows, top first.

    Returns a single-element list for anything that is not confidently two
    line, so callers can treat the result uniformly: read every band, join in
    order. The bands overlap the split row slightly, because glyph descenders
    and the thresholding both wander by a pixel or two and clipping a stroke
    costs more than including a sliver of the neighbouring row.
    """
    if crop is None or crop.size == 0:
        return []
    verdict = verdict or classify(crop)
    height = crop.shape[0]
    row = verdict.split_row
    if not verdict.is_two_line or row is None or row <= 2 or row >= height - 2:
        return [crop]

    pad = max(1, height // 40)
    upper = crop[: min(height, row + pad)]
    lower = crop[max(0, row - pad):]
    bands = [band for band in (upper, lower) if band.size and band.shape[0] >= 4]
    return bands or [crop]
