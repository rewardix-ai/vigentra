"""Whether a plate could have been read at all - and saying so out loud.

The pipeline's silence is ambiguous. A camera that reports no plates might have
seen no vehicles, or it might have seen two hundred vehicles whose plates were
forty pixels wide. Those are opposite situations - one is a quiet road, the
other is a camera pointed too far from the traffic to do ANPR - and until this
module existed the platform could not tell them apart. An operator asking "why
is this camera producing nothing?" got no answer, and a wrong answer ("no
traffic") is worse than none.

So every track that leaves the frame without a settled plate is now given a
reason:

    CONFIRMED   the vote settled and agreed
    UNCERTAIN   the plate was big enough to read, but the readings disagreed
    UNREADABLE  the plate never reached a size any recogniser could resolve

Only the third is a property of the camera. The second is a property of this
particular vehicle - motion blur, an oblique angle, glare - and it is the one
worth showing a human.

The width floors
----------------
An Indian single-row plate carries about ten characters across its width, so
character width is roughly plate_width/10 and character height roughly
plate_width/7.

    >=160px  COMFORTABLE  reads off the unmodified crop
    >=120px  READABLE     the raw read may fail; restoration recovers it
    >= 80px  MARGINAL     reads only when several restorations agree
    >= 40px  SUB_MARGINAL under 4px per character; agreement is luck
     < 40px  UNREADABLE   the information is not in the pixels

Only the last of those is enforced as a gate, and that restraint is the whole
point of this module - it is the opposite of what it looks like it should do.

Why the gate sits at 40px and not at 80 or 120
----------------------------------------------
Because a higher floor was measured, on this estate, to destroy evidence rather
than save effort. Adjudicated crops - a human read the registration off each
one - came out like this:

    GJ06D02415   61px   fully legible by eye
    GJ01RY6237   64px   fully legible by eye
    GJ01MR4873   53px   legible, marginal
    (illegible)  71px   motion blur
    (illegible)  68px   oblique angle + glare

Real plates read well BELOW 120px and fail well ABOVE it. No width separates
the two sets. A width floor is a crude proxy for a question - "do the readings
agree?" - that this pipeline already answers directly, in `consensus.py`, by
requiring several restorations of several frames to arrive at the same string.
A sweep of width floors against confirm thresholds found zero wrong
confirmations at any floor, and found that a 100px floor confirms nothing
anywhere while a 90px floor was the only one admitting the estate's single
verified correct read.

The temptation this module exists to resist is closing the gate further to make
the UNREADABLE count look smaller. That does not turn misses into reads; run
the same undersized crops through the recogniser with the gate open and they
come back as fabrications - GJ06D02415 read as "LD607415", GJ01MR4873 as
"GI667673". A fabricated registration is not a near miss. It is a wrong vehicle
attached to a real place and time, and it is the one output of this system that
can send police to the wrong address. The floor stays where the information
physically runs out; agreement does the rest of the work.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Plate width in pixels at which the unmodified crop reads directly.
COMFORTABLE_PX = 160.0
#: Restoration is needed below this, but the read is usually recoverable.
READABLE_PX = 120.0
#: Below this a read survives only when several restorations agree.
MARGINAL_PX = 80.0
#: The gate. Under ~4px per character there is nothing left to recover, and
#: everything admitted below here has been measured to arrive as invention.
HARD_FLOOR_PX = 40.0

#: Characters across a single-row Indian registration, used to turn a plate
#: width into a per-character width. Two-row plates carry fewer per row and so
#: are treated generously by this estimate, which is the safe direction.
CHARS_ACROSS = 10.0

TIERS = ("COMFORTABLE", "READABLE", "MARGINAL", "SUB_MARGINAL", "UNREADABLE")

VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_UNCERTAIN = "UNCERTAIN"
VERDICT_UNREADABLE = "UNREADABLE"


def tier_of(width_px: float) -> str:
    """Which size band a plate crop of this width falls into."""
    if width_px >= COMFORTABLE_PX:
        return "COMFORTABLE"
    if width_px >= READABLE_PX:
        return "READABLE"
    if width_px >= MARGINAL_PX:
        return "MARGINAL"
    if width_px >= HARD_FLOOR_PX:
        return "SUB_MARGINAL"
    return "UNREADABLE"


def char_width_px(width_px: float) -> float:
    """Approximate width of one character on a plate this wide."""
    return width_px / CHARS_ACROSS


def worth_reading(width_px: float) -> bool:
    """Is there enough resolution here for OCR to be attempted honestly?"""
    return width_px >= HARD_FLOOR_PX


@dataclass
class TrackSizes:
    """The best look this pipeline ever got at one vehicle's plate."""

    best_width: float = 0.0
    crops: int = 0
    #: True once OCR was actually spent on this track. A track that was only
    #: ever seen below the floor is a different case from one that was read
    #: and disagreed with itself, and the verdict must not conflate them.
    read: bool = False


@dataclass
class ReadabilityLedger:
    """Per-camera record of what the plates on this feed actually looked like.

    Kept beside the consensus table rather than inside it because it answers a
    question about the *camera*, not about any vehicle: consensus asks "what
    does this plate say", this asks "could it ever have said anything".
    """

    tracks: dict[int, TrackSizes] = field(default_factory=dict)
    #: Verdict counts for tracks that have left the frame.
    verdicts: dict[str, int] = field(
        default_factory=lambda: {
            VERDICT_CONFIRMED: 0, VERDICT_UNCERTAIN: 0, VERDICT_UNREADABLE: 0
        }
    )
    #: Size-band counts over every crop measured, for the camera report.
    bands: dict[str, int] = field(default_factory=lambda: {t: 0 for t in TIERS})
    #: Crops declined by the floor. Not a loss - see the module docstring.
    below_floor: int = 0

    def observe(self, track_id: int, width_px: float) -> bool:
        """Record one plate crop. Returns whether it is worth reading."""
        st = self.tracks.get(track_id)
        if st is None:
            st = self.tracks[track_id] = TrackSizes()
        st.crops += 1
        st.best_width = max(st.best_width, float(width_px))
        self.bands[tier_of(width_px)] += 1
        ok = worth_reading(width_px)
        if not ok:
            self.below_floor += 1
        return ok

    def note_read(self, track_id: int) -> None:
        """Mark that OCR was actually spent on this track."""
        st = self.tracks.get(track_id)
        if st is not None:
            st.read = True

    def retire(self, track_id: int, *, confirmed: bool) -> str:
        """Settle one track's verdict as it leaves the frame."""
        st = self.tracks.pop(track_id, None)
        if confirmed:
            verdict = VERDICT_CONFIRMED
        elif st is None or st.best_width < MARGINAL_PX:
            # Never big enough for agreement to mean anything. This is the
            # camera's answer, not the vehicle's.
            verdict = VERDICT_UNREADABLE
        else:
            # Big enough to have been read, and the readings did not agree.
            verdict = VERDICT_UNCERTAIN
        self.verdicts[verdict] += 1
        return verdict

    def drop_tracks(self) -> None:
        """Forget the vehicles, keep what the camera has told us about itself.

        Called at a scene cut. The track ids on the far side of a cut describe
        different vehicles, so their best-seen widths must not carry over - but
        the size distribution this camera produces is a fact about the camera,
        and re-learning it from zero every cycle would mean never learning it.
        """
        self.tracks.clear()

    def reset(self) -> None:
        self.tracks.clear()
        for k in self.verdicts:
            self.verdicts[k] = 0
        for k in self.bands:
            self.bands[k] = 0
        self.below_floor = 0

    # -- reporting ---------------------------------------------------------

    def describe(self) -> dict:
        """What this camera can and cannot do, in numbers an operator can use."""
        settled = sum(self.verdicts.values())
        readable_crops = sum(
            self.bands[t] for t in ("COMFORTABLE", "READABLE", "MARGINAL")
        )
        crops = sum(self.bands.values())
        return {
            "tracks_settled": settled,
            "confirmed": self.verdicts[VERDICT_CONFIRMED],
            "uncertain": self.verdicts[VERDICT_UNCERTAIN],
            "unreadable": self.verdicts[VERDICT_UNREADABLE],
            "crops_measured": crops,
            "crops_at_readable_size": readable_crops,
            "crops_below_floor": self.below_floor,
            "size_bands": dict(self.bands),
            "verdict": self._camera_verdict(settled, crops, readable_crops),
        }

    def _camera_verdict(self, settled: int, crops: int, readable: int) -> str:
        """One line an operator can act on.

        Deliberately refuses to judge on thin evidence: a camera that has shown
        us four plates has not told us anything about itself yet.
        """
        if crops < 10:
            return "INSUFFICIENT_EVIDENCE"
        share = readable / crops if crops else 0.0
        if self.verdicts[VERDICT_CONFIRMED] > 0 and share >= 0.15:
            return "ANPR_WORKING"
        if share >= 0.15:
            return "ANPR_MARGINAL"
        if settled and self.verdicts[VERDICT_UNREADABLE] == settled:
            return "ANPR_INFEASIBLE_HERE"
        return "ANPR_MARGINAL"
