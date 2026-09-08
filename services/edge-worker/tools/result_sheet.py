"""Result sheet, one plate per row, left to right with gaps:

    vehicle (box marked) | plate snapshot | bicubic x4 | single-frame SR | multi-frame SR | truth

Rows come from the synthetic validation set, whose vehicles are REAL crops
from the grid cameras and whose plates carry a known truth. Sorted small to
large so the eye can find where each enhancement stops helping.

    python tools/result_sheet.py --single runs/sr/S1_hard_first/best.pt --out reports/result_sheet_S1.jpg
    python tools/result_sheet.py --single runs/sr/S3_wide_cont/best.pt --multi runs/mfsr/M1_hard_first/best.pt
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))

GAP = 18
CELL_W = 230
CELL_H = 96
VEH_W = 260


def _fit(im: np.ndarray, w: int, h: int, nearest: bool = False) -> np.ndarray:
    s = min(w / im.shape[1], h / im.shape[0])
    im = cv2.resize(im, (max(1, int(im.shape[1] * s)), max(1, int(im.shape[0] * s))),
                    interpolation=cv2.INTER_NEAREST if nearest else cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    c = np.full((h, w, 3), 255, np.uint8)
    y0, x0 = (h - im.shape[0]) // 2, (w - im.shape[1]) // 2
    c[y0:y0 + im.shape[0], x0:x0 + im.shape[1]] = im
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="dataset/v4_uniform")
    ap.add_argument("--single", default=None, help="single-frame upscaler weights")
    ap.add_argument("--multi", default=None, help="multi-frame upscaler weights")
    ap.add_argument("--count", type=int, default=16)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", default="reports/result_sheet.jpg")
    ap.add_argument("--split", default="val_synth")
    args = ap.parse_args()
    root = Path(args.dataset)

    with open(root / "plates.csv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r["split"] == args.split and r["row"] == "0" and r.get("sr_pair")
                and (not args.multi or r.get("mfsr_pair"))]
    rng = np.random.default_rng(args.seed)
    picks = [rows[i] for i in rng.choice(len(rows), size=min(args.count, len(rows)), replace=False)]
    picks.sort(key=lambda r: float(r["plate_px"]))

    single = multi = None
    if args.single:
        from anpr.sr import PlateUpscaler
        single = PlateUpscaler(args.single, "cpu")
    if args.multi:
        from anpr.sr import MultiFrameUpscaler
        multi = MultiFrameUpscaler(args.multi, "cpu")

    heads = ["vehicle (real crop)", "plate snapshot", "bicubic x4",
             "single-frame SR" if single else None, "multi-frame SR (5 frames)" if multi else None, "truth"]
    heads = [h for h in heads if h]
    widths = [VEH_W] + [CELL_W] * (len(heads) - 1)
    total_w = sum(widths) + GAP * (len(widths) + 1)
    tiles = []
    for r in picks:
        veh = cv2.imread(str(root / r["image"]))
        h, w = veh.shape[:2]
        lbl = root / "labels" / args.split / (Path(r["image"]).stem + ".txt")
        if lbl.exists():
            for line in lbl.read_text().splitlines():
                _, cx, cy, bw, bh = map(float, line.split())
                cv2.rectangle(veh, (int((cx - bw / 2) * w), int((cy - bh / 2) * h)),
                              (int((cx + bw / 2) * w), int((cy + bh / 2) * h)), (0, 255, 0), max(1, w // 300))
        lr = cv2.imread(str(root / f"{r['sr_pair']}_lr.png"))
        hr = cv2.imread(str(root / f"{r['sr_pair']}_hr.jpg"))
        if lr is None or hr is None:
            continue
        up_w, up_h = lr.shape[1] * 4, lr.shape[0] * 4
        cells = [_fit(veh, VEH_W, CELL_H),
                 _fit(cv2.resize(lr, (up_w, up_h), interpolation=cv2.INTER_NEAREST), CELL_W, CELL_H, nearest=True),
                 _fit(cv2.resize(lr, (up_w, up_h), interpolation=cv2.INTER_CUBIC), CELL_W, CELL_H)]
        if single:
            cells.append(_fit(single.upscale(lr), CELL_W, CELL_H))
        if multi:
            frames = [cv2.imread(str(root / f"{r['mfsr_pair']}_f{k}_lr.png")) for k in range(multi.frames)]
            frames = [f for f in frames if f is not None]
            out = multi.upscale(frames) if frames else None
            cells.append(_fit(out, CELL_W, CELL_H) if out is not None else np.full((CELL_H, CELL_W, 3), 255, np.uint8))
        cells.append(_fit(cv2.resize(hr, (up_w, up_h)), CELL_W, CELL_H))
        row = np.full((CELL_H + 26, total_w, 3), 255, np.uint8)
        x = GAP
        for c in cells:
            row[:CELL_H, x:x + c.shape[1]] = c
            x += c.shape[1] + GAP
        tag = f"{r['tier']}  plate {float(r['plate_px']):.0f} px{'  night' if r['night'] == '1' else ''}" \
              f"{'  glare' if r.get('glare') == '1' else ''}   truth: {r['full_text']}"
        cv2.putText(row, tag, (GAP, CELL_H + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 190), 1)
        tiles.append(row)
    header = np.full((30, total_w, 3), 245, np.uint8)
    x = GAP
    for hd, wd in zip(heads, widths):
        cv2.putText(header, hd, (x, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        x += wd + GAP
    sheet = np.vstack([header] + tiles)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(args.out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"wrote {args.out}: {len(tiles)} rows, columns: {' | '.join(heads)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
