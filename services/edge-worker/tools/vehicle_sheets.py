#!/usr/bin/env python3
"""Check a vehicle count by eye: contact sheets from an `annotate_video.py --verify --cache` run.

    python tools/vehicle_sheets.py CLIP CACHE.pkl OUTPREFIX [--context 4,10,13]

Writes, beside OUTPREFIX:
  _vehicles.jpg  one thumbnail per counted vehicle (its longest track, largest box), in order of first
                 appearance - the same vehicle twice is visible as two neighbouring look-alikes
  _merges.jpg    every group of tracker ids counted as one vehicle, its members side by side - a wrong
                 merge is visible as two different vehicles in one row
  _context.jpg   (with --context) the listed vehicles in their wider scene, box drawn, for the doubtful ones

The counting is `anpr/track/vehicle_count.py` with the same inputs the render uses, so what the sheets
show is exactly what the video counts.
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anpr.track.vehicle_count import (STILL_MIN_SCORE, appearance_from_video, link_fragments,  # noqa: E402
                                      reader_groups)

TW, TH = 140, 105


def tile(img, text, w=TW, h=TH):
    im = cv2.resize(img, (w, h)) if img is not None and img.size else np.zeros((h, w, 3), np.uint8)
    cv2.rectangle(im, (0, 0), (w, 16), (0, 0, 0), -1)
    cv2.putText(im, text, (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)
    return im


def grid(tiles, cols, w=TW, h=TH):
    blank = np.zeros((h, w, 3), np.uint8)
    rows = [np.hstack(tiles[k:k + cols] + [blank] * (cols - len(tiles[k:k + cols]))) for k in range(0, len(tiles), cols)]
    return np.vstack(rows) if rows else blank


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("cache")
    ap.add_argument("out")
    ap.add_argument("--context", default="", help="vehicle numbers to show in their wider scene")
    ap.add_argument("--min-similarity", type=float, default=None,
                    help="appearance gate for links (default: the render's); 0 shows every box-based link")
    a = ap.parse_args()
    fps = cv2.VideoCapture(a.clip).get(cv2.CAP_PROP_FPS) or 25.0
    veh, _plt, cls, records, _ev, _best, _frames, score = pickle.load(open(a.cache, "rb"))
    majority = {t: c.most_common(1)[0][0] for t, c in cls.items()}
    joined = reader_groups(records, {t: o[0][0] for t, o in veh.items()}, fps)
    from anpr.track.vehicle_count import MIN_SIMILARITY
    looks = appearance_from_video(a.clip, veh)
    links: list = []
    number, n = link_fragments(veh, majority, fps=fps, same_vehicle=joined, scores=score, min_score=STILL_MIN_SCORE,
                               appearance=looks, explain=links,
                               min_similarity=MIN_SIMILARITY if a.min_similarity is None else a.min_similarity)
    key = lambda t: str(t).split("_")[-1]
    plate = {key(r["track_id"]): r["plate"] for r in records if r.get("status") == "CONFIRMED" and r.get("plate")}
    groups: dict = {}
    for t, g in number.items():
        if g:
            groups.setdefault(g, []).append(t)
    context = {int(x) for x in a.context.split(",") if x.strip()}

    # one decode: every track's largest box, and the middle box of each vehicle asked for in context
    want: dict = {}
    for t, o in veh.items():
        f, b = max(o, key=lambda x: (x[1][2] - x[1][0]) * (x[1][3] - x[1][1]))
        want.setdefault(f, []).append(("thumb", t, b))
        want.setdefault(o[0][0], []).append(("first", t, o[0][1]))
        want.setdefault(o[-1][0], []).append(("last", t, o[-1][1]))
    for g in context:
        t = max(groups.get(g, []), key=lambda x: len(veh[x]), default=None)
        if t is not None:
            f, b = veh[t][len(veh[t]) // 2]
            want.setdefault(f, []).append(("scene", t, b))
    thumbs, ends, scenes, cap, i = {}, {}, {}, cv2.VideoCapture(a.clip), 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        for kind, t, b in want.get(i, []):
            x1, y1, x2, y2 = (int(max(0, v)) for v in b)
            if kind == "thumb":
                thumbs[t] = fr[y1:y2, x1:x2].copy()
            elif kind in ("first", "last"):
                ends[(t, kind)] = fr[y1:y2, x1:x2].copy()
            else:
                w, h = x2 - x1, y2 - y1
                X1, Y1, X2, Y2 = max(0, x1 - w), max(0, y1 - h), min(fr.shape[1], x2 + w), min(fr.shape[0], y2 + h)
                c = fr[Y1:Y2, X1:X2].copy()
                cv2.rectangle(c, (x1 - X1, y1 - Y1), (x2 - X1, y2 - Y1), (0, 255, 255), 2)
                scenes[t] = (i, c)
        i += 1

    out = a.out
    sheet = [tile(thumbs.get(max(groups[g], key=lambda x: len(veh[x]))),
                  f"V{g} {min(veh[t][0][0] for t in groups[g]) / fps:.0f}s {majority[groups[g][0]][:4]} "
                  f"{next((plate[t] for t in groups[g] if t in plate), '')}") for g in sorted(groups)]
    cv2.imwrite(f"{out}_vehicles.jpg", grid(sheet, 12))
    rows = []
    for g in sorted(groups):
        if len(groups[g]) > 1:
            ms = sorted(groups[g], key=lambda x: veh[x][0][0])[:6]
            rows.append(grid([tile(thumbs.get(t), f"V{g} {t} {veh[t][0][0] / fps:.0f}-{veh[t][-1][0] / fps:.0f}s")
                              for t in ms], 6))
    if rows:
        cv2.imwrite(f"{out}_merges.jpg", np.vstack(rows))
    # every link, the two fragments side by side where they met, least alike first
    pairs = []
    for rule, x, y, sim in sorted(links, key=lambda r: (r[3] if r[3] is not None else 9)):
        wx, wy = ("mid", "mid") if rule in ("duplicate", "parked") else ("last", "first")
        pairs += [tile(ends.get((x, "last")) if wx == "last" else thumbs.get(x), f"{rule[:4]} {sim} {x}"),
                  tile(ends.get((y, "first")) if wy == "first" else thumbs.get(y), f"V{number.get(y)} {y}")]
    if pairs:
        cv2.imwrite(f"{out}_links.jpg", grid(pairs, 8))
    if context:
        tiles = []
        for g in sorted(context):
            t = max(groups.get(g, []), key=lambda x: len(veh[x]), default=None)
            if t in scenes:
                f, c = scenes[t]
                tiles.append(tile(c, f"V{g} {t} {f / fps:.0f}s s{score.get(t, 0):.2f} n{len(veh[t])} {majority[t][:4]}", 260, 195))
        cv2.imwrite(f"{out}_context.jpg", grid(tiles, 5, 260, 195))
    print(f"{len(veh)} tracker ids, {n} vehicles, {len(rows)} merged groups -> {out}_vehicles.jpg, {out}_merges.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
