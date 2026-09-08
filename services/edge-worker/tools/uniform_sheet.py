"""Contact sheet of the uniform dataset: vehicle crops with their plate box
and the reader crop cut from the same image, sampled across tiers so the
sheet shows what the models are actually asked to learn.

    python tools/uniform_sheet.py --dataset dataset/v4_uniform --out reports/uniform_sheet.jpg
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="dataset/v4_uniform")
    ap.add_argument("--out", default="reports/uniform_sheet.jpg")
    ap.add_argument("--per-tier", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    root = Path(args.dataset)
    rng = np.random.default_rng(args.seed)
    with open(root / "plates.csv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == "train" and r["row"] == "0"]
    by_image = {}
    for r in rows:
        by_image.setdefault(r["image"], r)
    picks = []
    for tier in ("extreme", "severe", "moderate", "mild"):
        cand = [r for r in by_image.values() if r["tier"] == tier]
        idx = rng.choice(len(cand), size=min(args.per_tier, len(cand)), replace=False)
        picks.extend(cand[i] for i in idx)

    W, H = 300, 340
    tiles = []
    for r in picks:
        img = cv2.imread(str(root / r["image"]))
        h, w = img.shape[:2]
        lbl = root / "labels" / "train" / (Path(r["image"]).stem + ".txt")
        for line in lbl.read_text().splitlines():
            _, cx, cy, bw, bh = map(float, line.split())
            x1, y1 = int((cx - bw / 2) * w), int((cy - bh / 2) * h)
            x2, y2 = int((cx + bw / 2) * w), int((cy + bh / 2) * h)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 1)
        s = min(W / w, 240 / h)
        img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_NEAREST)
        tile = np.full((H, W, 3), 255, np.uint8)
        tile[:img.shape[0], :img.shape[1]] = img
        patch = cv2.imread(str(root / r["plate_image"]))
        ph, pw = patch.shape[:2]
        ps = min(150 / pw, 44 / ph)
        patch = cv2.resize(patch, (max(1, int(pw * ps)), max(1, int(ph * ps))), interpolation=cv2.INTER_NEAREST)
        tile[250:250 + patch.shape[0], W - patch.shape[1] - 4:W - 4] = patch
        cv2.putText(tile, f"{r['tier']} {float(r['plate_px']):.0f}px{' night' if r['night'] == '1' else ''}"
                          f"{' glare' if r.get('glare') == '1' else ''}",
                    (2, 262), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 200), 1)
        cv2.putText(tile, f"{w}x{h} crop", (2, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 90, 90), 1)
        cv2.putText(tile, f"label: {r['text'] or '(empty: unreadable)'}", (2, 300),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 200), 1)
        cv2.putText(tile, f"plate really says {r['full_text']}", (2, 318),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (90, 90, 90), 1)
        tiles.append(tile)
    cols = 4
    n = len(tiles)
    rws = (n + cols - 1) // cols
    sheet = np.full((rws * (H + 2), cols * (W + 2), 3), 255, np.uint8)
    for i, t in enumerate(tiles):
        rr, cc = divmod(i, cols)
        sheet[rr * (H + 2):rr * (H + 2) + H, cc * (W + 2):cc * (W + 2) + W] = t
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(args.out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])

    m = json.loads((root / "manifest.json").read_text())
    with open(root / "plates.csv", encoding="utf-8") as fh:
        allrows = list(csv.DictReader(fh))
    train_r = [r for r in allrows if r["split"] == "train"]
    print(f"wrote {args.out}: {n} vehicles")
    print(f"detector images: synthetic train {m['synthetic_train_images']} + real train "
          f"{m['real_images']['train']}; real val {m['real_images']['val']}; real test {m['real_images']['test']}")
    print("tiers:", {t: v["images"] for t, v in m["tiers"].items()})
    print("plate px median per tier:", {t: v["plate_px_median"] for t, v in m["tiers"].items()})
    print(f"reader crops train: {len(train_r)} (labelled {sum(1 for r in train_r if r['text'])}, "
          f"empty {sum(1 for r in train_r if not r['text'])}), night {sum(1 for r in train_r if r['night']=='1')}, "
          f"glare {sum(1 for r in train_r if r.get('glare')=='1')}, "
          f"stacked {sum(1 for r in train_r if r['stacked']=='1')}")
    bands = Counter()
    for r in train_r:
        px = float(r["plate_px"])
        bands["<16" if px < 16 else "16-24" if px < 24 else "24-32" if px < 32 else "32-40" if px < 40
              else "40-60" if px < 60 else "60-100" if px < 100 else ">=100"] += 1
    print("reader crops by plate width:", dict(sorted(bands.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
