#!/usr/bin/env python3
"""Does asking "is this plate P?" find more of a known vehicle than reading plates open-ended?

On the event day one registration is given and has to be traced across grid cameras. A reader that only
has to verify one string can accept a blurrier plate than one that must read any string. This measures
that on grid clips with by-eye ground truth (data/eval/anpr_gt), from the crop banks of a benchmark run
(tools/anpr_benchmark.py run --tag T):

- target score of a track for P: for each of the track's best crops, the CTC likelihood of P under each
  CRNN, relative to that CRNN's own best path (0 = P is as likely as anything it could read), averaged
  over the top crops; plus the edit distance from PaddleOCR's open reading;
- per clip, each true plate P (readable in that clip) and each decoy (plates readable in other clips,
  and random plates of the same shape) gets its best track score;
- baseline: the plates the engine itself emitted for the clip (tracks.csv), exact or within the
  cross-camera matcher's tolerance (plate_distance <= 1.0).

Output: found / missed / false alarms at several thresholds, baseline beside it.

    python tools/target_search_eval.py --tag grid_target_base
"""
from __future__ import annotations

import argparse
import csv
import json
import pickle
import random
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

from anpr.plate_grammar import ALPHABET  # noqa: E402
from anpr.read.crnn import CRNNReader  # noqa: E402

GT = REPO / "data" / "eval" / "anpr_gt"
BANKS = Path.home() / "Downloads" / "ANPR_BENCH"
OUT = HERE / "reports" / "anpr_benchmark"
BLANK = len(ALPHABET)
INDEX = {c: i for i, c in enumerate(ALPHABET)}


def ctc_logp(probs: np.ndarray, target: str) -> float:
    """log P(target | probs) under CTC (forward algorithm, log space)."""
    lab = [INDEX[c] for c in target if c in INDEX]
    ext = [BLANK]
    for k in lab:
        ext += [k, BLANK]
    T, S = probs.shape[0], len(ext)
    lp = np.log(np.clip(probs, 1e-12, 1.0))
    alpha = np.full(S, -np.inf)
    alpha[0] = lp[0, ext[0]]
    if S > 1:
        alpha[1] = lp[0, ext[1]]
    for t in range(1, T):
        prev = alpha.copy()
        for s in range(S):
            a = prev[s]
            if s >= 1:
                a = np.logaddexp(a, prev[s - 1])
            if s >= 2 and ext[s] != BLANK and ext[s] != ext[s - 2]:
                a = np.logaddexp(a, prev[s - 2])
            alpha[s] = a + lp[t, ext[s]]
    return float(np.logaddexp(alpha[-1], alpha[-2]) if S > 1 else alpha[-1])


def best_path_logp(probs: np.ndarray) -> tuple[str, float]:
    idx = probs.argmax(1)
    logp = float(np.log(np.clip(probs[np.arange(len(idx)), idx], 1e-12, 1)).sum())
    out, prev = [], None
    for i in idx:
        if i != prev and i != BLANK:
            out.append(ALPHABET[i])
        prev = i
    return "".join(out), logp


def one_row(img: np.ndarray, two_row: bool) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if not two_row:
        return g
    h = g.shape[0] // 2
    top, bottom = g[:h], g[h:]
    bottom = cv2.resize(bottom, (int(bottom.shape[1] * top.shape[0] / max(1, bottom.shape[0])), top.shape[0]))
    return np.hstack([top, bottom])


def edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def gt_plates(camera: str) -> set[str]:
    path = GT / f"{camera}.csv"
    if not path.exists():
        return set()
    with open(path) as fh:
        return {r["plate"] for r in csv.DictReader(fh) if r.get("legibility") == "readable" and r.get("plate") and "?" not in r["plate"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--top", type=int, default=3, help="crops per track (best quality first)")
    ap.add_argument("--decoys", type=int, default=40, help="random same-shape plates added as decoys")
    args = ap.parse_args()

    from cross_camera import plate_distance
    from anpr.read.awiros import AwirosReader

    readers = [CRNNReader(HERE / "models" / w) for w in ("reader_crnn.onnx", "reader_crnn_v6.onnx")]
    paddle = AwirosReader()
    clips = sorted(p.name for p in (BANKS / args.tag).iterdir() if p.is_dir())
    truth = {c: gt_plates(c) for c in clips}
    pool = sorted(set().union(*truth.values()))
    random.seed(3)
    fake = set()
    while len(fake) < args.decoys:
        p = random.choice(pool) if pool else "GJ01AB1234"
        q = list(p)
        for _ in range(2):   # two random substitutions: a near-twin of a real plate, the hard decoy
            i = random.randrange(2, len(q))
            q[i] = random.choice("0123456789") if q[i].isdigit() else random.choice("ABCDEFGHJKLMNPRSTUVWXYZ")
        fake.add("".join(q))
    candidates = sorted(set(pool) | fake)

    rows = []
    for clip in clips:
        tracks = []
        for pkl in sorted((BANKS / args.tag / clip).glob("*.pkl")):
            bank = pickle.load(open(pkl, "rb"))
            crops = sorted(bank.crops, key=lambda c: -c.quality.quality_score)[: args.top]
            if not crops:
                continue
            per_crop = []
            for c in crops:
                g = one_row(c.image, c.two_row)
                probs = [r(g) for r in readers]   # the reader resizes and normalises itself
                paddle_read = paddle.read(c.image)[0]
                per_crop.append((probs, paddle_read))
            tracks.append((pkl.stem, per_crop))
        emitted = set()
        tpath = OUT / args.tag / clip / "tracks.csv"
        if tpath.exists():
            with open(tpath) as fh:
                for r in csv.DictReader(fh):
                    if r.get("plate"):
                        emitted.add(r["plate"])
        for P in candidates:
            best = (-1e9, None, 99)
            for tid, per_crop in tracks:
                rel = []
                for probs, _ in per_crop:
                    rel.append(np.mean([ctc_logp(p, P) - best_path_logp(p)[1] for p in probs]))
                score = float(np.mean(sorted(rel, reverse=True)[:2]))
                pd = min(edits(pr, P) for _, pr in per_crop)
                if score > best[0]:
                    best = (score, tid, pd)
            rows.append({"clip": clip, "plate": P, "present": P in truth[clip], "score": round(best[0], 3),
                         "track": best[1], "paddle_edits": best[2],
                         "baseline_exact": P in emitted,
                         "baseline_match": any(plate_distance(P, e) <= 1.0 for e in emitted)})
        print(clip, len(tracks), "tracks;", len(truth[clip]), "readable plates", flush=True)

    out = OUT / args.tag / "target_search.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    present = [r for r in rows if r["present"]]
    absent = [r for r in rows if not r["present"]]
    summary = {"true_plates": len(present), "decoy_pairs": len(absent),
               "baseline_found_exact": sum(r["baseline_exact"] for r in present),
               "baseline_found_match": sum(r["baseline_match"] for r in present),
               "baseline_false_match": sum(r["baseline_match"] for r in absent)}
    for th in (-2.0, -4.0, -6.0, -8.0, -10.0):
        summary[f"target@{th}"] = {"found": sum(r["score"] >= th for r in present),
                                    "false": sum(r["score"] >= th for r in absent)}
    for k in (0, 1, 2):
        summary[f"paddle_edits<={k}"] = {"found": sum(r["paddle_edits"] <= k for r in present),
                                         "false": sum(r["paddle_edits"] <= k for r in absent)}
    print(json.dumps(summary, indent=1))
    (OUT / args.tag / "target_search.json").write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
