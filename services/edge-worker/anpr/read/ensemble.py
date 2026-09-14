"""Reader ensemble (spec 8.2): run every available reader over every image
variant {fused gray, fused binarised, SR, plus top single crops} and return
ReadHypothesis objects for track-level fusion. Two-row plates are split and
rows read separately, then concatenated."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from anpr.fuse.rover import ReadHypothesis
from anpr.plate_grammar import ALPHABET, score_string
from anpr.read.beam_grammar import ctc_beam_search, slot_beam_search
from anpr.read.rows import side_by_side, split_rows

_BLANK = len(ALPHABET)


def _greedy_with_probs(probs: np.ndarray) -> tuple[str, list[float]]:
    """CTC greedy collapse returning the text and the max prob of each emitted char."""
    idx = probs.argmax(-1)
    out, cps, prev = [], [], -1
    for t, i in enumerate(idx):
        if i != prev and i != _BLANK:
            out.append(ALPHABET[i])
            cps.append(float(probs[t, i]))
        prev = i
    return "".join(out), cps


@dataclass
class Variant:
    name: str
    gray: np.ndarray
    weight: float
    is_sr: bool = False
    two_row: bool = False
    color: Optional[np.ndarray] = None   # BGR crop when there is one (whole-crop text readers read colour)


class ReaderEnsemble:
    def __init__(self, readers: list, beam: int = 16, topn_per_read: int = 3, preferred_state: str = "GJ",
                 reader_weight: Optional[dict] = None, text_variants: int = 0):
        self.readers = [r for r in readers if getattr(r, "ok", False)]
        self.beam = beam
        self.topn = topn_per_read
        self.preferred_state = preferred_state
        self.reader_weight = reader_weight or {}
        # > 0: whole-crop text readers (slow, ~0.1 s per crop on CPU) read only the first N variants
        # that carry a colour crop (the best single crops), not every fused / enhanced variant
        self.text_variants = text_variants

    @property
    def names(self) -> list[str]:
        return [r.name for r in self.readers]

    def _decode(self, reader, probs: np.ndarray):
        if getattr(reader, "slot_style", False):
            return slot_beam_search(probs, self.beam, preferred_state=self.preferred_state, topn=self.topn)
        return ctc_beam_search(probs, self.beam, preferred_state=self.preferred_state, topn=self.topn)

    def read(self, variants: list[Variant]) -> list[ReadHypothesis]:
        out: list[ReadHypothesis] = []
        if not self.readers:
            return out
        for reader in self.readers:
            if hasattr(reader, "read_batch"):
                # whole-crop text reader (anpr/read/awiros.py): colour when available, two-row plates
                # read in one pass without a row split, one hypothesis per variant
                rw = float(self.reader_weight.get(reader.name, 1.0))
                vs = [v for v in variants if v.color is not None][: self.text_variants] if self.text_variants else variants
                if not vs:
                    continue
                res = reader.read_batch([v.color if v.color is not None else v.gray for v in vs])
                for v, (text, conf) in zip(vs, res):
                    if text:
                        out.append(ReadHypothesis(text, [conf] * len(text), v.weight * conf * rw,
                                                  f"{v.name}/{reader.name}", v.is_sr, 0))
                continue
            for v in variants:
                if v.two_row and getattr(reader, "two_row_mode", "split") == "side_by_side":
                    # v4+ readers were trained on two-row plates laid out as one line: read the
                    # whole registration in one pass and let the grammar beam see all of it
                    probs = reader(side_by_side(v.gray))
                elif v.two_row:
                    rows = split_rows(v.gray)
                    ps = reader.probs(rows)
                    if len(ps) != 2:
                        continue
                    # Per-row GREEDY decode, then join and validate the whole string.
                    # The plate grammar must never run on a single row: a row like
                    # 'A2257' does not fit any full-plate template, so the constrained
                    # beam bent it into 'AZ257' (measured on sandbox cam06). The
                    # concatenated-probability beam is kept as a second hypothesis.
                    parts, cps = [], []
                    for p in ps:
                        t, cp = _greedy_with_probs(p)
                        parts.append(t)
                        cps.extend(cp)
                    joined = "".join(parts)
                    gs = score_string(joined, self.preferred_state) if joined else None
                    if gs is not None and gs.valid:
                        mc = float(np.mean(cps)) if cps else 0.0
                        out.append(ReadHypothesis(joined, cps, v.weight * mc * 1.2, f"{v.name}/{reader.name}/rows", v.is_sr))
                    probs = np.concatenate(ps, 0)
                else:
                    probs = reader(v.gray)
                if probs is None or probs.shape[0] == 0:
                    continue
                hyps = self._decode(reader, probs)
                for rank, h in enumerate(hyps):
                    mc = float(np.mean(h.char_probs)) if h.char_probs else 0.0
                    w = v.weight * mc * (1.0 if rank == 0 else 0.4 / rank)
                    out.append(ReadHypothesis(h.text, h.char_probs, w, f"{v.name}/{reader.name}", v.is_sr, rank))
        return out
