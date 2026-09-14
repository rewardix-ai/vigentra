"""Grammar-constrained beam search over per-character probability matrices
(spec 5.4 / 8.2). Supports CTC outputs (with blank) and fixed-slot outputs.

Input : probs [T, C] where C = len(ALPHABET)+1 (last index = blank) for CTC,
        or [L, C] for attention-style readers (no blank needed).
Output: ranked list of (string, log_score, per_char_probs, format_name).

The search is a prefix beam search over CTC collapse; each completed prefix
is re-scored by the plate grammar: strings that cannot extend into any format
are pruned early using the length-indexed template set, and positional
confusion mass is added so that a digit-looking glyph in a letter slot can
become the confusable letter at a cost.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from anpr.plate_grammar import ALPHABET, CONFUSABLE, FORMATS, FORMATS_BY_LEN, fits_slot, score_string

NEG_INF = -1e30
C2I = {c: i for i, c in enumerate(ALPHABET)}
MAX_LEN = max(f.length for f in FORMATS)
MIN_LEN = min(f.length for f in FORMATS)


@dataclass
class Hypothesis:
    text: str
    score: float                # log prob incl. grammar
    char_probs: list[float]     # per-char probability actually assigned
    fmt: str
    grammar_prior: float


@lru_cache(maxsize=200_000)
def _prefix_ok(prefix: str) -> bool:
    """Can `prefix` extend into at least one format? (cached: prefixes repeat heavily across beams)"""
    n = len(prefix)
    if n > MAX_LEN:
        return False
    for L, fmts in FORMATS_BY_LEN.items():
        if L < n:
            continue
        for f in fmts:
            if all(fits_slot(ch, f.template[i]) for i, ch in enumerate(prefix)):
                return True
    return False


def _logsumexp(a: float, b: float) -> float:
    if a == NEG_INF:
        return b
    if b == NEG_INF:
        return a
    m = max(a, b)
    return m + math.log(math.exp(a - m) + math.exp(b - m))


def ctc_beam_search(probs: np.ndarray, beam: int = 16, blank: int | None = None,
                    confusion_mass: float = 0.15, preferred_state: str = "GJ",
                    topn: int = 5) -> list[Hypothesis]:
    """probs: [T, C]. blank defaults to last column."""
    T, C = probs.shape
    blank = C - 1 if blank is None else blank
    logp = np.log(np.clip(probs, 1e-9, 1.0))
    # spread confusion mass: for each symbol, add a fraction of its prob to its confusables
    if confusion_mass > 0:
        spread = probs.copy()
        for c, alts in CONFUSABLE.items():
            i = C2I.get(c)
            if i is None or i >= C:
                continue
            for a in alts:
                j = C2I.get(a)
                if j is not None and j < C:
                    spread[:, j] += confusion_mass * probs[:, i]
        logp = np.log(np.clip(spread, 1e-9, 1.5))
    # beams: prefix -> (p_blank, p_nonblank, charprobs list)
    beams: dict[str, tuple[float, float, tuple]] = {"": (0.0, NEG_INF, ())}
    for t in range(T):
        nxt: dict[str, list] = defaultdict(lambda: [NEG_INF, NEG_INF, ()])
        # top-k symbols this step for speed
        top_syms = np.argsort(logp[t])[::-1][: min(C, 12)]
        for prefix, (pb, pnb, cps) in beams.items():
            ptot = _logsumexp(pb, pnb)
            # blank
            e = nxt[prefix]
            e[0] = _logsumexp(e[0], ptot + logp[t, blank])
            e[2] = cps
            for s in top_syms:
                if s == blank:
                    continue
                ch = ALPHABET[s]
                lp = logp[t, s]
                if prefix and prefix[-1] == ch:
                    # repeat without blank: stays same prefix (nonblank path)
                    e2 = nxt[prefix]
                    e2[1] = _logsumexp(e2[1], pnb + lp)
                    # extend after a blank
                    np_ = prefix + ch
                    if _prefix_ok(np_):
                        e3 = nxt[np_]
                        e3[1] = _logsumexp(e3[1], pb + lp)
                        if not e3[2]:
                            e3[2] = cps + (float(probs[t, s]),)
                else:
                    np_ = prefix + ch
                    if not _prefix_ok(np_):
                        continue
                    e3 = nxt[np_]
                    e3[1] = _logsumexp(e3[1], ptot + lp)
                    if not e3[2] or len(e3[2]) < len(np_):
                        e3[2] = cps + (float(probs[t, s]),)
        # prune
        items = sorted(nxt.items(), key=lambda kv: _logsumexp(kv[1][0], kv[1][1]), reverse=True)[:beam]
        beams = {k: (v[0], v[1], v[2]) for k, v in items}
    return _finalise(beams, preferred_state, topn)


def _finalise(beams, preferred_state: str, topn: int) -> list[Hypothesis]:
    out: list[Hypothesis] = []
    for text, (pb, pnb, cps) in beams.items():
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            continue
        gs = score_string(text, preferred_state)
        if not gs.valid:
            continue
        lp = _logsumexp(pb, pnb)
        score = lp + math.log(max(gs.prior, 1e-4))
        cp = list(cps)[: len(text)] if cps else [math.exp(lp / max(len(text), 1))] * len(text)
        while len(cp) < len(text):
            cp.append(cp[-1] if cp else 0.5)
        out.append(Hypothesis(text, score, cp, gs.fmt.name if gs.fmt else "", gs.prior))
    out.sort(key=lambda h: h.score, reverse=True)
    return out[:topn]


def slot_beam_search(probs: np.ndarray, beam: int = 16, confusion_mass: float = 0.15,
                     preferred_state: str = "GJ", topn: int = 5) -> list[Hypothesis]:
    """For attention-style readers that emit one distribution per output slot
    (with an optional EOS column at the end)."""
    L, C = probs.shape
    has_eos = C == len(ALPHABET) + 1
    beams: list[tuple[str, float, tuple]] = [("", 0.0, ())]
    for t in range(L):
        row = probs[t].copy()
        if confusion_mass > 0:
            for c, alts in CONFUSABLE.items():
                i = C2I[c]
                for a in alts:
                    row[C2I[a]] += confusion_mass * probs[t, i]
        nxt = []
        top = np.argsort(row)[::-1][:12]
        for prefix, lp, cps in beams:
            for s in top:
                if has_eos and s == C - 1:
                    nxt.append((prefix + "\x00", lp + math.log(max(row[s], 1e-9)), cps))
                    continue
                if prefix.endswith("\x00"):
                    continue
                np_ = prefix + ALPHABET[s]
                if not _prefix_ok(np_):
                    continue
                nxt.append((np_, lp + math.log(max(row[s], 1e-9)), cps + (float(probs[t, s]),)))
        beams = sorted(nxt, key=lambda b: b[1], reverse=True)[:beam]
    d = {}
    for text, lp, cps in beams:
        t = text.rstrip("\x00")
        if t not in d or d[t][0] < lp:
            d[t] = (lp, NEG_INF, cps)
    return _finalise(d, preferred_state, topn)
