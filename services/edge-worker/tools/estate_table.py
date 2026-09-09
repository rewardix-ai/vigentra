"""Per-camera verdict table from a compare_on_footage report.

For every feed: vehicles seen, plates boxed, attached to a vehicle, plate
width p50/p90, reads and confirmed reads, and a verdict:

    READABLE   p50 plate width >= 30 px: most vehicles pass through the band
               the reader and enhancer work in
    MARGINAL   p90 >= 30 px but p50 below: only the nearest vehicles are
               readable; a zoom preset on the stop line would fix it
    RE-AIM     p90 < 30 px: no vehicle ever comes close enough; this camera
               cannot yield reads from software
    NO TRAFFIC no vehicles in the sampled frames

    python tools/estate_table.py reports/footage_estate.json [--run LABEL] [--md reports/estate_table.md]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

READABLE_PX = 30.0


def verdict(cam: dict) -> str:
    if not cam.get("vehicles"):
        return "NO TRAFFIC"
    w = cam.get("plate_width_px") or {}
    p50, p90 = w.get("p50"), w.get("p90")
    if not cam.get("plate_boxes"):
        return "RE-AIM"
    if p50 is not None and p50 >= READABLE_PX:
        return "READABLE"
    if p90 is not None and p90 >= READABLE_PX:
        return "MARGINAL"
    return "RE-AIM"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report")
    ap.add_argument("--run", default=None, help="run label (default: last run in the report)")
    ap.add_argument("--md", default=None, help="also write a markdown table here")
    args = ap.parse_args()
    d = json.load(open(args.report, encoding="utf-8"))
    label = args.run or list(d["runs"])[-1]
    run = d["runs"][label]
    rows = []
    for cam in run["per_camera"]:
        w = cam.get("plate_width_px") or {}
        rows.append({
            "feed": cam["feed_id"], "frames": cam["frames"], "vehicles": cam["vehicles"],
            "plates": cam["plate_boxes"], "attached": cam["attached_to_vehicle"],
            "p50": w.get("p50"), "p90": w.get("p90"),
            "reads": cam["reads"], "confirmed": cam["reads_confirmed"], "verdict": verdict(cam),
        })
    rows.sort(key=lambda r: (r["verdict"], -(r["p50"] or 0)))
    head = f"{'feed':<16}{'frames':>7}{'vehicles':>9}{'plates':>7}{'attached':>9}{'p50':>7}{'p90':>7}{'reads':>6}{'conf':>5}  verdict"
    print(f"run: {label}")
    print(head); print("-" * len(head))
    for r in rows:
        print(f"{r['feed']:<16}{r['frames']:>7}{r['vehicles']:>9}{r['plates']:>7}{r['attached']:>9}"
              f"{(r['p50'] or 0):>7.0f}{(r['p90'] or 0):>7.0f}{r['reads']:>6}{r['confirmed']:>5}  {r['verdict']}")
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    t = run["totals"]
    print("-" * len(head))
    print(f"feeds: {len(rows)}  " + "  ".join(f"{k}: {v}" for k, v in sorted(c.items())))
    print(f"totals: frames {t.get('frames',0)}  vehicles {t.get('vehicles',0)}  plates {t.get('plate_boxes',0)}  "
          f"attached {t.get('attached_to_vehicle',0)}  reads {t.get('reads',0)}  confirmed {t.get('reads_confirmed',0)}  "
          f"fps {run.get('fps')}")
    if args.md:
        lines = [f"| feed | frames | vehicles | plates | attached | plate p50 px | p90 px | reads | confirmed | verdict |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r['feed']} | {r['frames']} | {r['vehicles']} | {r['plates']} | {r['attached']} | "
                         f"{(r['p50'] or 0):.0f} | {(r['p90'] or 0):.0f} | {r['reads']} | {r['confirmed']} | {r['verdict']} |")
        Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
