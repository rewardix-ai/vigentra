"""Multi-frame consensus.

A single frame is a coin flip; a track is evidence.  The same vehicle is seen
across dozens of frames at different distances, angles and exposures, and the
errors those frames make are largely independent.  Aggregating them is the
single most effective accuracy mechanism available for video ANPR - it
routinely fixes plates that no individual frame read correctly.

Two votes run in parallel:

* a **string vote**, which picks the reading that accumulated the most
  weight, and
* a **positional vote**, which builds a plate character-by-character and can
  therefore synthesise a correct plate that appeared in *no* single frame.

Whichever scores better after re-validation through the grammar engine wins.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from . import plate_rules as pr
from .config import ConsensusConfig


@dataclass
class Observation:
    """One OCR reading of one plate crop at one instant."""
    text: str
    confidence: float
    score: float
    valid: bool
    fmt: str | None
    quality: float                #: crop usability, 0..1
    frame: int
    engine: str = ""
    variant: str = ""

    @property
    def weight(self) -> float:
        """How much this reading counts toward the verdict."""
        # Crop quality gates the vote: a confident reading of a 20 px smear
        # should not outweigh a tentative reading of a clean crop.
        return self.score * (0.35 + 0.65 * self.quality)


@dataclass
class Consensus:
    """The current verdict for one track."""
    text: str = ""
    pretty: str = ""
    score: float = 0.0
    confidence: float = 0.0
    valid: bool = False
    fmt: str | None = None
    state: str | None = None
    confirmed: bool = False
    locked: bool = False          #: strong enough that we stop spending OCR
    observations: int = 0
    margin: float = 0.0
    method: str = ""              #: "string" | "positional"
    runners_up: list[tuple[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "text": self.text, "pretty": self.pretty, "score": round(self.score, 4),
            "confidence": round(self.confidence, 4), "valid": self.valid,
            "format": self.fmt, "state": self.state, "confirmed": self.confirmed,
            "locked": self.locked, "observations": self.observations,
            "margin": round(self.margin, 4), "method": self.method,
            "runners_up": [[t, round(w, 3)] for t, w in self.runners_up],
        }


class TrackConsensus:
    """Accumulates readings for a single tracked vehicle."""

    def __init__(self, track_id: int, cfg: ConsensusConfig) -> None:
        self.track_id = track_id
        self.cfg = cfg
        self.observations: deque[Observation] = deque(maxlen=cfg.history)
        self.best_quality: float = 0.0
        self.last_ocr_frame: int = -10_000
        #: last frame index this track was visible on, for the grace period
        self.last_frame: int = -10_000
        #: first frame index carrying a reading, used to merge fragments
        self.first_frame: int = -1
        self.first_seen: float = time.time()
        self.last_seen: float = self.first_seen
        #: capture time (from PTS) of this track's first reading
        self.first_capture: float | None = None
        #: capture time this track was last visible, for time-based ageing
        self.last_capture: float | None = None
        self._verdict: Consensus = Consensus()
        self._dirty = True

    # -- ingest ----------------------------------------------------------
    def observe(self, candidates: Iterable[pr.PlateCandidate], quality: float,
                frame: int) -> None:
        """Record every reading produced for one crop of this track."""
        self.last_ocr_frame = frame
        self.last_frame = frame
        if self.first_frame < 0:
            self.first_frame = frame
        self.best_quality = max(self.best_quality, quality)
        for c in candidates:
            if not c.text:
                continue
            self.observations.append(Observation(
                text=c.text, confidence=c.confidence, score=c.score,
                valid=c.valid, fmt=c.fmt, quality=quality, frame=frame,
                engine=c.engine, variant=c.variant,
            ))
            self._dirty = True

    # -- verdict ---------------------------------------------------------
    @property
    def verdict(self) -> Consensus:
        if self._dirty:
            self._verdict = self._resolve()
            self._dirty = False
        return self._verdict

    def _resolve(self) -> Consensus:
        obs = list(self.observations)
        if not obs:
            return Consensus()

        cfg = self.cfg
        # ---- string vote ------------------------------------------------
        totals: dict[str, float] = defaultdict(float)
        for o in obs:
            totals[o.text] += o.weight * (cfg.valid_weight if o.valid else 1.0)

        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        top_text, top_weight = ranked[0]
        second_weight = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = (top_weight - second_weight) / top_weight if top_weight else 0.0

        best = self._score_text(top_text, obs, "string", share=None)
        best_margin = margin

        # ---- positional vote --------------------------------------------
        voted, agreement = self._positional_vote(obs)
        if voted and voted != top_text:
            # A synthesised plate is supported by per-slot agreement, not by
            # how many whole strings matched it - typically none did.  Using
            # whole-string support here would always sink it.
            alt = self._score_text(voted, obs, "positional", share=agreement)
            # Only let it win if it is genuinely better; a tie must go to a
            # string that some frame actually produced.
            if (alt.valid, alt.score) > (best.valid, best.score):
                best, best_margin = alt, agreement

        best.observations = len(obs)
        best.margin = best_margin
        best.runners_up = [(t, w) for t, w in ranked[1:4]]

        # Confirmation needs agreement across *independent looks*, not just a
        # high score - one very good frame is not a consensus.
        distinct_frames = len({o.frame for o in obs})
        best.confirmed = (
            distinct_frames >= cfg.min_observations
            and best.score >= cfg.min_score
            and best_margin >= cfg.min_margin
        )
        best.locked = best.confirmed and best.valid and best.score >= cfg.lock_score
        return best

    def _score_text(self, text: str, obs: list[Observation], method: str,
                    share: float | None = None) -> Consensus:
        """Re-validate a candidate string and blend in its supporting evidence.

        *share* is how much of the total evidence backs this reading, on 0..1.
        Pass it explicitly for synthesised strings whose support is measured
        per character slot; leave it None to derive it from whole-string
        agreement.
        """
        supporting = [o for o in obs if o.text == text]
        if supporting:
            conf = max(o.confidence for o in supporting)
        else:
            # Synthesised reading: it inherits the credibility of the frames
            # that built it, discounted because no frame produced it whole.
            conf = sum(o.confidence for o in obs) / len(obs) * 0.95

        cand = pr.normalise(text, conf)
        if share is None:
            total = sum(o.weight * (self.cfg.valid_weight if o.valid else 1.0)
                        for o in obs) or 1.0
            share = sum(o.weight * (self.cfg.valid_weight if o.valid else 1.0)
                        for o in supporting) / total

        # Agreement across frames and grammar validity reinforce each other.
        score = cand.score * (0.55 + 0.45 * min(1.0, share * 1.3))
        return Consensus(
            text=cand.text, pretty=pr.format_pretty(cand.text, cand.fmt),
            score=min(1.0, score), confidence=conf, valid=cand.valid,
            fmt=cand.fmt, state=cand.state, method=method,
        )

    def _positional_vote(self, obs: list[Observation]) -> tuple[str | None, float]:
        """Rebuild the plate one character slot at a time.

        Restricted to readings whose length matches the dominant length, so
        we never average across two different plates.  Returns the assembled
        string together with the mean per-slot agreement, which is the real
        measure of how much evidence stands behind it.
        """
        if len(obs) < 2:
            return None, 0.0

        lengths: dict[int, float] = defaultdict(float)
        for o in obs:
            lengths[len(o.text)] += o.weight * (self.cfg.valid_weight if o.valid else 1.0)
        target_len = max(lengths.items(), key=lambda kv: kv[1])[0]
        if not (pr.MIN_PLATE_LEN <= target_len <= pr.MAX_PLATE_LEN):
            return None, 0.0

        pool = [o for o in obs if len(o.text) == target_len]
        if len(pool) < 2:
            return None, 0.0

        # ---- layout mask ------------------------------------------------
        # Which format the pool believes it is looking at, so each slot can be
        # constrained to the class the layout allows. An Indian plate is
        # AA NN AA NNNN: position 0 is a letter, position 2 is a digit, and no
        # amount of agreement makes 'O' legal in a digit slot. Without this the
        # voter would happily elect a glyph the grammar must then reject,
        # throwing away the whole track.
        fmts: dict[str, float] = defaultdict(float)
        for o in pool:
            if o.valid and o.fmt:
                fmts[o.fmt] += o.weight
        fmt_name = max(fmts.items(), key=lambda kv: kv[1])[0] if fmts else None
        classes = pr.char_classes(fmt_name, target_len)

        out: list[str] = []
        agreements: list[float] = []
        for i in range(target_len):
            want = classes[i] if i < len(classes) else "?"
            slot: dict[str, float] = defaultdict(float)
            for o in pool:
                w = o.weight * (self.cfg.valid_weight if o.valid else 1.0)
                # A reading that breaks the layout is evidence, not noise: OCR
                # confuses a known, small set of glyph pairs, so '0' seen in a
                # letter slot is a vote for O/D/Q. Spend it on the legal glyphs
                # it most likely was, discounted by how far down the confusion
                # list each one sits. This is what turns five disagreeing
                # frames into one correct plate rather than five rejects.
                for glyph, cost in pr.slot_options(o.text[i], want):
                    slot[glyph] += w / (1.0 + cost)
            if not slot:
                return None, 0.0
            ch, won = max(slot.items(), key=lambda kv: kv[1])
            total = sum(slot.values()) or 1.0
            out.append(ch)
            agreements.append(won / total)

        return "".join(out), sum(agreements) / len(agreements)

    # -- scheduling hints -------------------------------------------------
    def wants_ocr(self, frame: int, quality: float, cfg) -> bool:
        """Should we spend an OCR call on this track's current crop?

        This is the throttle that keeps crowded scenes real-time: locked
        tracks stop consuming budget entirely, and a crop that is clearly
        worse than what we already have is not worth reading.
        """
        if self.verdict.locked:
            return False
        if frame - self.last_ocr_frame < cfg.track_cooldown:
            return False
        if self.observations and quality < self.best_quality - cfg.quality_regression:
            return False
        return True

    def priority(self, quality: float) -> float:
        """Higher = more deserving of a scarce OCR slot."""
        v = self.verdict
        # Unread tracks first, then poorly-resolved ones, then quality.
        urgency = 1.0 if not self.observations else (1.0 - v.score)
        return urgency * 2.0 + quality


def is_truncation(short: str, full: str) -> bool:
    """True when *short* is *full* with exactly one character dropped.

    OCR loses a character far more often than it invents one, and a plate
    missing a digit can still satisfy a shorter legal format - 'GJ03KL556'
    parses happily as an old-style 3-digit-tail plate.  When one vehicle's
    track fragments, the two halves can therefore confirm the full plate and
    its truncation as if they were two different vehicles.
    """
    if len(short) != len(full) - 1 or not short:
        return False
    i = j = 0
    skipped = False
    while i < len(short) and j < len(full):
        if short[i] == full[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return i == len(short)


def supersedes(a: str, b: str) -> bool:
    """True when reading *a* should absorb reading *b*."""
    return is_truncation(b, a)


class ConsensusStore:
    """All live tracks, plus retirement of tracks that have left the scene."""

    #: Frames a track may go unseen before it is retired.  Detection flickers
    #: constantly - a plate is routinely missed for a frame or two behind a
    #: wiper, a pole or a moment of blur - and retiring on the first miss
    #: splits one vehicle into several tracks, each with too little evidence
    #: to confirm and each free to confirm a *different* wrong answer.
    GRACE_FRAMES = 15
    #: The same grace period expressed in **seconds of capture time**.
    #:
    #: Frame counts are not a duration when the frame rate varies, and stream
    #: frame intervals are explicitly not guaranteed to be uniform: 15 frames
    #: is half a second at 30 fps but three seconds at 5 fps.  When a capture
    #: timestamp is available it governs, and the frame count is the fallback.
    GRACE_SECONDS = 1.5

    def __init__(self, cfg: ConsensusConfig) -> None:
        self.cfg = cfg
        self.tracks: dict[int, TrackConsensus] = {}
        self.retired: dict[int, TrackConsensus] = {}

    def get(self, track_id: int) -> TrackConsensus:
        tc = self.tracks.get(track_id)
        if tc is not None:
            return tc
        # Resurrect rather than restart: if this id was retired a moment ago
        # its accumulated evidence is still about this same vehicle.
        tc = self.retired.pop(track_id, None)
        if tc is None:
            tc = TrackConsensus(track_id, self.cfg)
        self.tracks[track_id] = tc
        return tc

    def touch(self, track_id: int, frame: int) -> None:
        """Mark a track as still present this frame, even without a reading."""
        tc = self.tracks.get(track_id)
        if tc is not None:
            tc.last_frame = frame

    def retire_missing(self, alive: set[int], frame: int,
                       now: float | None = None) -> list[TrackConsensus]:
        """Retire tracks unseen for longer than the grace period.

        *now* is the current capture time.  When supplied, the grace period is
        measured in seconds of footage rather than in frames, so it means the
        same thing on a 5 fps camera as on a 30 fps one.
        """
        out: list[TrackConsensus] = []
        for tid, tc in list(self.tracks.items()):
            if tid in alive:
                tc.last_frame = frame
                if now is not None:
                    tc.last_capture = now
                continue
            if now is not None and tc.last_capture is not None:
                if now - tc.last_capture < self.GRACE_SECONDS:
                    continue                    # still within the grace window
            elif frame - tc.last_frame < self.GRACE_FRAMES:
                continue
            self.tracks.pop(tid, None)
            if tc.observations:
                self.retired[tid] = tc
                out.append(tc)
        return out

    def all_tracks(self) -> list[TrackConsensus]:
        """Active and retired, with no track counted twice."""
        merged = dict(self.retired)
        merged.update(self.tracks)
        return list(merged.values())

    def reset(self) -> None:
        self.tracks.clear()
        self.retired.clear()
