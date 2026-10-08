#!/usr/bin/env python3
"""How good is the Gemini second opinion (app/second_opinion.py) on grid plates?

From the crop banks of the target-search run (tools/target_search_eval.py, --tag grid_target_base):
- every readable plate's best track (target_search.csv, present=True): verdict for its own plate
  (should agree) and for a near-twin, two characters changed (must not agree);
- tracks on the same clips that carry no readable plate: verdict for a plate of that clip (must not agree).

    GEMINI_API_KEY=... python tools/second_opinion_eval.py --tag grid_target_base [--model M]
"""
from __future__ import annotations

import argparse
import csv
import json
import pickle
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from app import second_opinion  # noqa: E402
from app.target_check import one_row  # noqa: E402

BANKS = Path.home() / "Downloads" / "ANPR_BENCH"
OUT = HERE / "reports" / "anpr_benchmark"


def twin(p: str, rng: random.Random) -> str:
    q = list(p)
    for i in rng.sample(range(2, len(q)), 2):
        pool = "0123456789" if q[i].isdigit() else "ABCDEFGHJKLMNPRSTUVWXYZ"
        q[i] = rng.choice([c for c in pool if c != q[i]])
    return "".join(q)


def crops_of(tag: str, clip: str, track: str, top: int = 3):
    bank = pickle.load(open(BANKS / tag / clip / f"{track}.pkl", "rb"))
    best = sorted(bank.crops, key=lambda c: -c.quality.quality_score)[:top]
    return [c.image for c in best]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="grid_target_base")
    ap.add_argument("--model", default=None)
    ap.add_argument("--empty", type=int, default=20, help="tracks without a readable plate to try")
    args = ap.parse_args()
    rng = random.Random(5)
    rows = list(csv.DictReader(open(OUT / args.tag / "target_search.csv")))
    true = {(r["clip"], r["plate"]): r["track"] for r in rows if r["present"] == "True"}
    used = set(true.values())
    results, tally = [], Counter()
    for (clip, plate), track in sorted(true.items()):
        reading, model = second_opinion.read(second_opinion.stack(crops_of(args.tag, clip, track)), args.model)
        tw = twin(plate, rng)
        own, other = second_opinion.verdict(reading, plate), second_opinion.verdict(reading, tw)
        tally[f"true:{own}"] += 1
        tally[f"twin:{other}"] += 1
        results.append({"clip": clip, "track": track, "plate": plate, "read": reading, "model": model,
                        "verdict": own, "twin": tw, "twin_verdict": other})
        print(clip, plate, reading, own, "| twin", tw, other, flush=True)
    empties = []
    for clip in sorted({c for c, _ in true}):
        plates = [p for c, p in true if c == clip]
        for pkl in sorted((BANKS / args.tag / clip).glob("*.pkl")):
            if pkl.stem not in used:
                empties.append((clip, pkl.stem, rng.choice(plates)))
    rng.shuffle(empties)
    n = 0
    for clip, track, plate in empties:
        try:
            crops = crops_of(args.tag, clip, track)
        except Exception:
            continue
        if not crops:
            continue
        reading, model = second_opinion.read(second_opinion.stack(crops), args.model)
        v = second_opinion.verdict(reading, plate)
        tally[f"other:{v}"] += 1
        results.append({"clip": clip, "track": track, "plate": plate, "read": reading, "model": model, "verdict": v, "kind": "other"})
        print("other", clip, track, plate, reading, v, flush=True)
        n += 1
        if n >= args.empty:
            break
    (OUT / args.tag / f"second_opinion_{args.model or 'default'}.json").write_text(json.dumps({"tally": tally, "rows": results}, indent=1))
    print(json.dumps(tally, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
