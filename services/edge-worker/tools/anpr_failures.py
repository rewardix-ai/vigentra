#!/usr/bin/env python3
"""Why each readable plate was missed, and what the wrong readings are.

    python tools/anpr_failures.py --tag opt2 [--clips delhi_1080p ...]

For a benchmark run it reports, per clip:
- every ground-truth plate marked readable that the run did not confirm, with the nearest reading the
  run produced anywhere on that clip (edit distance <= 3), that record's status, the reason it was not
  confirmed, its plate width and how many crops it had - so a miss can be attributed to detection,
  tracking, size, or the decision;
- the readings that are valid registrations but match no ground-truth plate (the wrong ones);
- the reasons tracks produced nothing at all.

Writes reports/anpr_benchmark/<tag>/FAILURES.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import editdistance

WORKER = Path(__file__).resolve().parent.parent
REPO = WORKER.parent.parent
GT_DIR = REPO / "data" / "eval" / "anpr_gt"
OUT = WORKER / "reports" / "anpr_benchmark"


def gt_plates(camera: str) -> tuple[set, list]:
    p = GT_DIR / f"{camera}.csv"
    if not p.exists():
        return set(), []
    rows = list(csv.DictReader(open(p)))
    return ({r["plate"] for r in rows if r["legibility"] == "readable" and r["plate"]},
            [r["plate"] for r in rows if r["legibility"] != "readable" and r["plate"]])


def nearest(tracks: list[dict], plate: str) -> tuple[dict | None, int]:
    best, dist = None, 99
    for t in tracks:
        text = t.get("OCR_result") or ""
        if not text:
            continue
        d = editdistance.eval(text, plate)
        if d < dist:
            best, dist = t, d
    return best, dist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--clips", nargs="*")
    a = ap.parse_args()
    root = OUT / a.tag
    cams = a.clips or sorted(d.name for d in root.iterdir() if (d / "tracks.csv").exists())
    lines = [f"# Why plates were missed — run `{a.tag}`", "",
             "Each missed plate is shown with the nearest reading the run produced on that clip, so the",
             "miss can be attributed. `-` in the nearest column means nothing on the clip read within 3",
             "characters of it.", ""]
    for cam in cams:
        readable, partial = gt_plates(cam)
        tracks = list(csv.DictReader(open(root / cam / "tracks.csv")))
        confirmed = {t["OCR_result"] for t in tracks if t["status"] == "CONFIRMED"}
        missed = sorted(readable - confirmed)
        wrong = sorted({t["OCR_result"] for t in tracks if t["OCR_result"] and t["valid_format"] == "1"
                        and t["OCR_result"] not in readable
                        and not any(len(p) == len(t["OCR_result"])
                                    and all(c in ("?", d) for c, d in zip(p, t["OCR_result"])) for p in partial)})
        if not readable and not wrong:
            continue
        lines += [f"## {cam}", "",
                  f"- readable plates: {len(readable)}, confirmed {len(readable & confirmed)}",
                  f"- tracks: {len(tracks)}, with a plate candidate "
                  f"{sum(1 for t in tracks if t['number_of_plate_candidates'] not in ('', '0'))}", ""]
        if missed:
            lines += ["| missed plate | nearest reading | edits | status | reason | plate size | crops |",
                      "|---|---|---:|---|---|---|---:|"]
            for plate in missed:
                t, d = nearest(tracks, plate)
                if t is None or d > 3:
                    lines.append(f"| {plate} | - | | | no reading within 3 characters | | |")
                else:
                    lines.append(f"| {plate} | {t['OCR_result']} | {d} | {t['status']} | "
                                 f"{t['reason'] or '-'} | {t['best_plate_size']} | "
                                 f"{t['number_of_plate_candidates']} |")
            lines.append("")
        if wrong:
            lines += [f"**Valid-format readings that match no plate ({len(wrong)}):** "
                      + ", ".join(wrong[:20]) + ("…" if len(wrong) > 20 else ""), ""]
        reasons = Counter(t["reason"].split(":")[0] for t in tracks if t["status"] != "CONFIRMED" and t["reason"])
        if reasons:
            lines += ["**Why the other tracks settled nothing:** "
                      + ", ".join(f"{k} {v}" for k, v in reasons.most_common(8)), ""]
    (root / "FAILURES.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
