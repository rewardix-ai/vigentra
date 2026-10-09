"""The pursued plate scores near 0 on crops that say it, far below on crops that say something else."""
from __future__ import annotations

import numpy as np

from anpr.plate_grammar import ALPHABET
from app import target_check as tc


def probs_for(text: str, sharp: float = 0.9) -> np.ndarray:
    """CTC frames spelling `text` with blanks between, each frame `sharp` on its symbol."""
    seq = []
    for ch in text:
        seq += [ALPHABET.index(ch), tc.BLANK]
    p = np.full((len(seq), len(ALPHABET) + 1), (1 - sharp) / len(ALPHABET))
    for t, k in enumerate(seq):
        p[t, k] = sharp
    return p / p.sum(1, keepdims=True)


def test_the_read_string_is_the_best_path():
    assert tc.best_path(probs_for("GJ01AB1234"))[0] == "GJ01AB1234"


def test_target_scores_high_on_itself_and_low_on_a_near_twin():
    p = probs_for("GJ01AB1234")
    own = tc.ctc_logp(p, "GJ01AB1234") - tc.best_path(p)[1]
    twin = tc.ctc_logp(p, "GJ01AB7234") - tc.best_path(p)[1]
    assert own >= tc.MATCH_SCORE > twin
