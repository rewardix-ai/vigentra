"""Track-level hypothesis fusion (spec 8.3): ROVER-style weighted character
voting across frames, image variants and readers; grammar prior; agreement
ratio; calibrated confidence; SR-agreement guard; ranked alternates."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import editdistance
import numpy as np

from anpr.plate_grammar import fits_slot, score_string, FORMATS_BY_LEN


@dataclass
class ReadHypothesis:
    text: str
    char_probs: list[float]
    weight: float              # crop_quality x model_confidence
    source: str                # e.g. 'fused_gray/crnn', 'sr/parseq'
    is_sr: bool = False
    rank: int = 0              # beam rank within its (variant, reader) read; 0 = that read's top string


@dataclass
class FusedRead:
    plate: str
    confidence: float
    alternates: list[dict]
    agreement: float
    grammar_prior: float
    mean_char_prob: float
    n_hyps: int
    sr_disagree: bool
    per_char_conf: list[float] = field(default_factory=list)
    reason: str = ""
    supported: bool = True     # some hypothesis produced exactly this string (not only the vote)


def _align_to_anchor(anchor: str, s: str) -> list[str | None]:
    """Align s to anchor positions using edit-distance backtrace. Returns, for
    each anchor position, the character from s (or None for a deletion)."""
    n, m = len(anchor), len(s)
    dp = np.zeros((n + 1, m + 1), np.int32)
    dp[:, 0] = np.arange(n + 1)
    dp[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i, j] = min(dp[i - 1, j] + 1, dp[i, j - 1] + 1, dp[i - 1, j - 1] + (anchor[i - 1] != s[j - 1]))
    out: list[str | None] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        if dp[i, j] == dp[i - 1, j - 1] + (anchor[i - 1] != s[j - 1]):
            out[i - 1] = s[j - 1]
            i, j = i - 1, j - 1
        elif dp[i, j] == dp[i - 1, j] + 1:
            i -= 1
        else:
            j -= 1
    return out


def _vote(hyps: list[ReadHypothesis], L: int, preferred_state: str, supported_only: bool = False):
    """Positional weighted vote for one candidate length. Returns (text, per-char
    vote shares, grammar score) or None when no hypothesis has this length.
    supported_only: choose among the hypotheses' own grammar-valid strings (scored by the
    same positional vote) instead of assembling each slot's winner; the slot-wise
    assembly produced a string no reader read on 52 of 78 Delhi-clip tracks."""
    same_len = [h for h in hyps if len(h.text) == L]
    if not same_len:
        return None
    anchor = max(same_len, key=lambda h: h.weight * float(np.mean(h.char_probs or [0.5]))).text
    votes: list[Counter] = [Counter() for _ in range(L)]
    for h in hyps:
        al = _align_to_anchor(anchor, h.text) if h.text != anchor else list(h.text)
        cp = h.char_probs if len(h.char_probs) == len(h.text) else [float(np.mean(h.char_probs or [0.5]))] * len(h.text)
        j = 0
        for i, ch in enumerate(al):
            if ch is None:
                continue
            p = cp[min(j, len(cp) - 1)]
            j += 1
            votes[i][ch] += h.weight * p
    if supported_only:
        tot = [max(sum(v.values()), 1e-9) for v in votes]
        pick = None
        for s in {h.text for h in same_len}:
            gs = score_string(s, preferred_state)
            if not gs.valid:
                continue
            pcs = [votes[i].get(ch, 0.0) / tot[i] for i, ch in enumerate(s)]
            sc = float(np.mean(pcs)) * max(gs.prior, 1e-3) ** 0.3
            if pick is None or sc > pick[0]:
                pick = (sc, s, pcs, gs)
        if pick is not None:
            return pick[1], pick[2], pick[3]
    templates = FORMATS_BY_LEN.get(L, [])
    best_text, best_score, best_pc = None, -1.0, []
    for f in sorted(templates, key=lambda f: f.prior, reverse=True):
        text, pcs, ok = [], [], True
        for i in range(L):
            slot = f.template[i]
            cand = [(ch, w) for ch, w in votes[i].items() if fits_slot(ch, slot)]
            if not cand:
                ok = False
                break
            ch, w = max(cand, key=lambda x: x[1])
            text.append(ch)
            pcs.append(w / max(sum(votes[i].values()), 1e-9))
        if not ok:
            continue
        s = "".join(text)
        gs = score_string(s, preferred_state)
        # the vote is the evidence; the format prior is a tie-breaker (compressed),
        # otherwise the common 10-char template outvotes what the characters say
        sc = float(np.mean(pcs)) * max(gs.prior, 1e-3) ** 0.3
        if sc > best_score:
            best_text, best_score, best_pc = s, sc, pcs
    if best_text is None:
        # no template fits; fall back to raw majority
        best_text = "".join(max(v, key=v.get) if v else "?" for v in votes)
        best_pc = [max(v.values()) / max(sum(v.values()), 1e-9) if v else 0.0 for v in votes]
    return best_text, best_pc, score_string(best_text, preferred_state)


def rover(hyps: list[ReadHypothesis], preferred_state: str = "GJ", temperature: float = 1.0,
          agreement_power: float = 1.0, sr_penalty: float = 0.6, topn: int = 3, n_lengths: int = 1,
          supported_only: bool = False) -> FusedRead:
    hyps = [h for h in hyps if h.text]
    if not hyps:
        return FusedRead("", 0.0, [], 0.0, 0.0, 0.0, 0, False, reason="no_hypotheses")
    total_w = sum(h.weight for h in hyps)
    # 1. candidate lengths: the n_lengths most-weighted string lengths. Voting only on
    # the single most common length let a crowd of weak single-frame reads fix a 9-char
    # length while the two strong fused reads said 10 (sandbox cam06 green plate
    # GJ32AG2883 became GJ32G2883: an alignment can never re-insert the dropped 'A').
    # Each candidate length is voted separately; the read with the strongest evidence wins.
    # Default n_lengths=1 (previous behaviour): with 2 the offline ablation fixed that plate but
    # flipped another and, under one weighting, produced a false confirm (GJ11E2257 0.89) - the
    # false-confirm floor outranks a candidate-only gain. Kept as an option for measurement.
    len_w = Counter()
    for h in hyps:
        len_w[len(h.text)] += h.weight
    lengths = [L for L, _ in len_w.most_common(max(1, n_lengths))]
    best = None
    for L in lengths:
        v = _vote(hyps, L, preferred_state, supported_only)
        if v is None:
            continue
        text, pcs, gs = v
        # agreement ratio: weight fraction of hyps within edit distance <= 1 of the winner.
        # Exact-string agreement double-penalised the positional vote: on real footage a
        # correct DL14CE5987 read scored 0.48 because glare/binarised/single-frame variants
        # each differed by one character. Near-agreement is the evidence that matters.
        agree_w = sum(h.weight for h in hyps if editdistance.eval(h.text, text) <= 1)
        agreement = agree_w / max(total_w, 1e-9)
        mean_cp = float(np.mean(pcs)) if pcs else 0.0
        raw = mean_cp * (0.5 + 0.5 * gs.prior) * (agreement ** agreement_power)
        if best is None or raw > best[0]:
            best = (raw, text, pcs, gs, agreement, mean_cp)
    raw, best_text, best_pc, gs, agreement, mean_cp = best
    # SR guard: if SR-only hypotheses disagree with the non-SR consensus, penalise
    non_sr = [h for h in hyps if not h.is_sr]
    sr = [h for h in hyps if h.is_sr]
    sr_disagree = False
    if sr and non_sr:
        top_non_sr = Counter()
        for h in non_sr:
            top_non_sr[h.text] += h.weight
        top_sr = Counter()
        for h in sr:
            top_sr[h.text] += h.weight
        sr_disagree = max(top_non_sr, key=top_non_sr.get) != max(top_sr, key=top_sr.get)
    # calibrated confidence. Temperature was fitted offline on the FINAL
    # reported confidence, so it is applied to the full product, not the character term.
    if sr_disagree:
        raw *= sr_penalty
    conf = _temperature(raw, temperature)
    cal = conf
    # alternates: other full strings by total weight, edit distance <= 2
    alt_w = Counter()
    for h in hyps:
        if h.text != best_text and editdistance.eval(h.text, best_text) <= 2:
            alt_w[h.text] += h.weight
    alts = [{"plate": t, "confidence": round(float(w / max(total_w, 1e-9)) * cal, 4)}
            for t, w in alt_w.most_common(topn)]
    return FusedRead(best_text, float(np.clip(conf, 0, 1)), alts, float(agreement), float(gs.prior), mean_cp,
                     len(hyps), sr_disagree, [float(x) for x in best_pc],
                     supported=any(h.text == best_text for h in hyps))


def _temperature(p: float, T: float) -> float:
    if T == 1.0 or p <= 0 or p >= 1:
        return float(np.clip(p, 0, 1))
    logit = np.log(p / (1 - p)) / T
    return float(1.0 / (1.0 + np.exp(-logit)))
