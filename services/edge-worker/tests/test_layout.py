"""Two-line plates: classify them, split them, and vote them per track.

Two-wheelers are roughly three quarters of Gujarat's fleet and almost all carry
a stacked plate, so a pipeline that only reads single-line plates is worst at
the commonest vehicle on the road. These tests pin the three properties that
stop that happening.
"""
from __future__ import annotations

import cv2
import numpy as np

from anpr import layout as lay
from anpr import plate_rules as pr
from anpr.config import ConsensusConfig
from anpr.consensus import TrackConsensus


def single_line(text: str = "GJ01AB1234", width: int = 320, height: int = 68):
    img = np.full((height, width, 3), 235, np.uint8)
    cv2.putText(img, text, (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                (18, 18, 18), 3, cv2.LINE_AA)
    return img


def two_line(top: str = "GJ01", bottom: str = "AB1234",
             width: int = 210, height: int = 115):
    img = np.full((height, width, 3), 235, np.uint8)
    cv2.putText(img, top, (18, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                (18, 18, 18), 3, cv2.LINE_AA)
    cv2.putText(img, bottom, (10, 102), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                (18, 18, 18), 3, cv2.LINE_AA)
    return img


def test_classifies_single_and_two_line():
    assert lay.classify(single_line()).layout == lay.SINGLE_LINE
    verdict = lay.classify(two_line())
    assert verdict.layout == lay.TWO_LINE
    assert verdict.split_row is not None


def test_split_bands_cuts_between_the_rows():
    """The split lands in the gap, not through a row of glyphs."""
    crop = two_line()
    verdict = lay.classify(crop)
    bands = lay.split_bands(crop, verdict)
    assert len(bands) == 2
    # Both bands must carry ink; a split through a row leaves one nearly blank.
    for band in bands:
        grey = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        assert grey.min() < 100, "band has no dark glyph pixels"


def test_single_line_is_never_split():
    crop = single_line()
    assert len(lay.split_bands(crop, lay.classify(crop))) == 1


def test_layout_is_a_track_property_and_outvotes_one_bad_look():
    """One skewed frame must not decide the vehicle's layout.

    Aspect ratio near the threshold is a coin flip on any single frame, which
    is the whole reason layout is voted rather than decided per crop.
    """
    consensus = TrackConsensus(1, ConsensusConfig())
    stacked = lay.LayoutVerdict(lay.TWO_LINE, 0.9, 48)
    flat = lay.LayoutVerdict(lay.SINGLE_LINE, 0.2, None)
    for index, verdict in enumerate((stacked, stacked, flat, stacked)):
        consensus.observe([pr.normalise("GJ01AB1234", 0.7)], 0.6, index,
                          layout=verdict)
    assert consensus.layout == lay.TWO_LINE


def test_positional_vote_scopes_to_the_settled_layout():
    """Readings taken under a different layout do not vote on position.

    A stacked plate read as one line and the same plate read band-by-band are
    both ten characters of the same vehicle and are NOT aligned. Voting them
    together votes two coordinate systems into one string.
    """
    consensus = TrackConsensus(2, ConsensusConfig())
    stacked = lay.LayoutVerdict(lay.TWO_LINE, 0.9, 40)
    flat = lay.LayoutVerdict(lay.SINGLE_LINE, 0.3, None)
    for index, text in enumerate(("GJ01AB1234", "GJ01AB1284", "GJ01AB1234")):
        consensus.observe([pr.normalise(text, 0.7)], 0.6, index, layout=stacked)
    # A misaligned single-line read of the same plate, which must not shift
    # the per-position tally it is not aligned with.
    consensus.observe([pr.normalise("1234GJ01AB", 0.7)], 0.6, 3, layout=flat)

    assert consensus.layout == lay.TWO_LINE
    assert consensus.verdict.text == "GJ01AB1234"


def test_layout_survives_an_unreadable_crop():
    """A crop nobody could read still tells us the plate's shape."""
    consensus = TrackConsensus(3, ConsensusConfig())
    consensus.observe([], 0.4, 0, layout=lay.LayoutVerdict(lay.TWO_LINE, 0.8, 30))
    consensus.observe([pr.normalise("GJ01AB1234", 0.7)], 0.6, 1,
                      layout=lay.LayoutVerdict(lay.TWO_LINE, 0.8, 30))
    assert consensus.layout == lay.TWO_LINE
