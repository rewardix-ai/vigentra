"""Multi-frame fusion: recovering a plate no single frame read correctly.

The ICPR 2026 LRLPR challenge found that what separated the strongest
submissions was not super-resolution or model size but *effective use of the
track structure* - fusing the frames of one vehicle rather than reading each
in isolation. These tests pin the two behaviours that matter for that:

  * per-position fusion across a track, so complementary evidence survives
    (frame 2 is right about slot 3, frame 5 about slot 6);
  * the layout constraint, so a slot the grammar says is a digit cannot elect
    a letter however many frames agree on it.

The second is the cheaper half and the one this codebase was missing: the
voter picked the highest-weight glyph per slot with no notion of what the slot
was allowed to hold, so `char_classes()` sat unused next to a comment saying
it was for exactly this.
"""
from __future__ import annotations

from anpr import plate_rules as pr
from anpr.config import ConsensusConfig
from anpr.consensus import TrackConsensus


def _track(frames: list[str], *, quality: float = 0.5, confidence: float = 0.60):
    consensus = TrackConsensus(1, ConsensusConfig())
    for index, text in enumerate(frames):
        consensus.observe([pr.normalise(text, confidence=confidence)], quality, index)
    return consensus.verdict


def test_recovers_plate_no_single_frame_read_correctly():
    """Five valid-but-wrong readings, no two alike, one correct answer.

    Every frame here parses as a legal registration, so no amount of grammar
    checking rejects them and a string vote has five candidates tied at one
    vote each. Only fusing slot by slot recovers the plate.
    """
    verdict = _track(
        ["GJ01AB1284", "GJ01AB1734", "GJ01AB1235", "GJ01AB1834", "GJ01AB1234"]
    )
    assert verdict.text == "GJ01AB1234"
    assert verdict.method == "positional"
    assert verdict.valid


def test_layout_mask_keeps_a_letter_out_of_a_digit_slot():
    """A digit slot stays a digit even when most frames read a letter.

    'O' for '0' is the commonest OCR confusion on plates. Three of these five
    frames read the district code as 'O1'; without the layout mask the voter
    elects 'O', the grammar then rejects the whole plate, and a readable track
    is thrown away. With it, those votes are folded onto '0' - the glyph they
    were evidence for - and the track survives.
    """
    verdict = _track(
        ["GJO1AB1234", "GJO1AB1234", "GJO1AB1234", "GJ01AB1234", "GJ01AB1234"]
    )
    assert verdict.text == "GJ01AB1234"
    assert verdict.valid


def test_confusion_folding_is_class_directed():
    """The fold is toward what the slot allows, not a blanket substitution."""
    # A letter slot fed '0' offers letters, best-first.
    letters = [glyph for glyph, _cost in pr.slot_options("0", "A")]
    assert letters[0] == "O"
    assert all(g.isalpha() for g in letters)

    # A digit slot fed 'O' offers '0'.
    assert [g for g, _ in pr.slot_options("O", "N")] == ["0"]

    # A glyph already of the right class is free and unaltered.
    assert pr.slot_options("G", "A") == (("G", 0.0),)


def test_single_frame_track_is_not_fused():
    """One look is not a consensus, however confident it is.

    Confirmation requires agreement across independent frames; a lone reading
    may still be reported, but never as confirmed.
    """
    verdict = _track(["GJ01AB1234"], confidence=0.99, quality=0.9)
    assert not verdict.confirmed
