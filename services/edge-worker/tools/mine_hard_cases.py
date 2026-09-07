"""Mine the worst cases out of the analysed feeds.

Random frames are nearly useless for this problem. On a junction camera most
frames hold empty road or one large obvious plate, so a random draw produces a
dataset whose easy cases drown the hard ones - and the model that comes out of
it is exactly the model we already have: fine on large plates, blind on small
ones.

This selects deliberately. For every difficulty category the samples are ranked
by *how extreme* they are on the axis that defines the category (smallest
first for `tiny`, blurriest first for `blur`, and so on) and the worst are
taken. Categories that cannot be measured from pixels are not mined; they are
routed to human review instead.

Two selections that are not per-plate:

**Vehicle without a visible plate.** Frames where the vehicle detector found a
plate-bearing vehicle and the plate detector found nothing on it. This is the
"plate too small" bug's own signature, and these frames are the single most
valuable thing in the corpus - they are where the detector is silently failing.

**Temporal neighbourhoods.** Around every selected sample, the neighbouring
frames of the same feed are pulled in too, so the same vehicle is present
across several looks. A detector trained only on isolated frames never learns
that a plate too small in one frame is legible two frames later.

Usage
-----
    python tools/mine_hard_cases.py
    python tools/mine_hard_cases.py --per-category 150 --neighbours 2
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _corpus import (  # noqa: E402
    DifficultyTag, NEEDS_HUMAN, PHYSICAL_FLOOR_PX, Readability,
    frame_number_of, load_json, percentiles, write_json,
)

log = logging.getLogger("mine_hard_cases")

#: How to rank within each category: the key, and whether smaller is worse.
#:
#: Every category is ranked by the quantity that DEFINES it rather than by a
#: generic difficulty score. A blurred plate and a tiny plate are hard for
#: unrelated reasons, and a single blended score would let one mask the other.
RANKERS: dict[str, tuple[str, bool]] = {
    DifficultyTag.TINY.value:           ("plate_width_px", True),
    DifficultyTag.BLUR.value:           ("blur_score", True),
    DifficultyTag.LOW_LIGHT.value:      ("brightness", True),
    DifficultyTag.OVEREXPOSED.value:    ("brightness", False),
    DifficultyTag.GLARE.value:          ("glare", False),
    DifficultyTag.LOW_CONTRAST.value:   ("contrast", True),
    DifficultyTag.LOW_CONFIDENCE.value: ("detection_confidence", True),
    DifficultyTag.ANGLED.value:         ("_abs_skew", False),
    DifficultyTag.PARTIAL.value:        ("crop_quality", True),
    DifficultyTag.MULTI_VEHICLE.value:  ("vehicles_in_frame", False),
    DifficultyTag.UNREADABLE.value:     ("plate_width_px", False),
}


def _rank_value(sample: dict, key: str) -> float:
    if key == "_abs_skew":
        skew = sample.get("skew_deg")
        return abs(float(skew)) if skew is not None else 0.0
    value = sample.get(key)
    return float(value) if value is not None else 0.0


def select_by_category(samples: list[dict], per_category: int) -> dict[str, list[dict]]:
    """Take the most extreme *per_category* samples in each difficulty class."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        for tag in sample.get("difficulty", []):
            if tag in RANKERS:
                buckets[tag].append(sample)

    chosen: dict[str, list[dict]] = {}
    for tag, rows in buckets.items():
        key, smaller_is_worse = RANKERS[tag]
        rows = sorted(rows, key=lambda s: _rank_value(s, key),
                      reverse=not smaller_is_worse)
        chosen[tag] = rows[:per_category]
    return chosen


def select_unreadable_but_present(samples: list[dict], limit: int) -> list[dict]:
    """Plates that were FOUND but could not be read.

    The most important category in this whole exercise, and the one a naive
    pipeline throws away. Split by cause, because the two have opposite
    lessons: too-small is physics and the detector should still find it;
    quality-unreadable means the pixels were there and something else failed.
    """
    rows = [s for s in samples
            if s.get("readability") in (Readability.UNREADABLE_QUALITY.value,
                                        Readability.UNREADABLE_TOO_SMALL.value)]
    rows.sort(key=lambda s: (
        # Quality failures first - a plate with enough pixels that still would
        # not read is the more informative case.
        0 if s.get("readability") == Readability.UNREADABLE_QUALITY.value else 1,
        -float(s.get("plate_width_px") or 0.0),
    ))
    return rows[:limit]


def select_vehicles_without_plates(analysis: dict, limit: int) -> list[dict]:
    """Feeds where plate-bearing vehicles far outnumber plate detections.

    Reported per feed rather than per frame because the per-frame join is not
    available from the analysis output alone; a feed with 80 vehicles and 3
    plates is nonetheless pointing at exactly the frames worth capturing, and
    the ratio is what tells a person which camera to look at first.
    """
    rows = []
    for camera in analysis.get("per_camera", []):
        funnel = camera.get("funnel", {})
        vehicles = funnel.get("vehicles_plate_bearing", 0)
        plates = funnel.get("plates_after_nms", 0)
        if vehicles < 3:
            continue
        miss_rate = 1.0 - (plates / float(vehicles)) if vehicles else 0.0
        rows.append({
            "feed_id": camera.get("feed_id"),
            "plate_bearing_vehicles": vehicles,
            "plate_boxes": plates,
            "plate_miss_rate": round(miss_rate, 3),
            "frames_analysed": camera.get("frames_analysed"),
            "difficulty": [DifficultyTag.VEHICLE_WITHOUT_VISIBLE_PLATE.value],
        })
    rows.sort(key=lambda r: -r["plate_miss_rate"])
    return rows[:limit]


def expand_temporal(chosen: dict[str, list[dict]], neighbours: int) -> list[dict]:
    """Add the frames either side of each selected sample, same feed.

    A vehicle is a sequence, not a snapshot. Keeping its neighbours lets the
    dataset carry the case where a plate is unreadable in one frame and
    readable in the next, which is what the consensus layer exists to exploit -
    and what a frame-shuffled dataset destroys.
    """
    wanted: set[tuple[str, int]] = set()
    for rows in chosen.values():
        for sample in rows:
            feed = sample.get("feed_id", "")
            frame = int(sample.get("frame_number") or 0)
            for offset in range(-neighbours, neighbours + 1):
                wanted.add((feed, frame + offset))
    return sorted(wanted)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", default="reports/plate_samples.json",
                        help="Output of analyze_feeds.py --samples-out")
    parser.add_argument("--analysis", default="reports/feed_analysis.json")
    parser.add_argument("--per-category", type=int, default=200,
                        help="Worst-N to keep per difficulty category.")
    parser.add_argument("--neighbours", type=int, default=2,
                        help="Frames either side of each pick to retain.")
    parser.add_argument("--out", default="reports/hard_cases.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    samples_doc = load_json(Path(args.samples))
    samples = samples_doc.get("samples", [])
    if not samples:
        raise SystemExit(f"No samples in {args.samples}. Run analyze_feeds.py first.")
    analysis = load_json(Path(args.analysis)) if Path(args.analysis).exists() else {}

    chosen = select_by_category(samples, args.per_category)
    unreadable = select_unreadable_but_present(samples, args.per_category)
    if unreadable:
        chosen[DifficultyTag.UNREADABLE.value] = unreadable
    missing = select_vehicles_without_plates(analysis, 50)
    neighbourhood = expand_temporal(chosen, args.neighbours)

    # A sample can be picked by several categories; the dataset wants each crop
    # once, carrying every reason it was chosen.
    unique: dict[str, dict] = {}
    for tag, rows in chosen.items():
        for row in rows:
            entry = unique.setdefault(row["image_id"], dict(row))
            entry.setdefault("mined_for", [])
            if tag not in entry["mined_for"]:
                entry["mined_for"].append(tag)

    # Anything that might carry a human-only tag is flagged here rather than
    # labelled. Low confidence plus an odd aspect ratio is the signature of
    # both a false positive and a genuinely non-standard plate, and pixels
    # cannot separate those two - a person has to look.
    for entry in unique.values():
        aspect = float(entry.get("plate_aspect") or 0.0)
        confidence = float(entry.get("detection_confidence") or 0.0)
        odd_shape = aspect > 0 and (aspect < 1.6 or aspect > 7.0)
        if odd_shape or confidence < 0.30:
            entry["needs_human_review"] = True
            entry["review_reason"] = (
                "aspect ratio outside the plausible range for a plate"
                if odd_shape else "detector confidence too low to trust unreviewed")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_samples": args.samples,
        "size_bands": samples_doc.get("size_bands", {}),
        "per_category_limit": args.per_category,
        "neighbours_kept": args.neighbours,
        "counts": {
            "samples_considered": len(samples),
            "unique_hard_cases": len(unique),
            "by_category": {tag: len(rows) for tag, rows in sorted(chosen.items())},
            "flagged_for_human_review": sum(
                1 for e in unique.values() if e.get("needs_human_review")),
            "temporal_neighbourhood_frames": len(neighbourhood),
        },
        "extremes": {
            "smallest_plate_px": min(
                (s["plate_width_px"] for s in samples), default=None),
            "plate_width_of_mined": percentiles(
                [e["plate_width_px"] for e in unique.values()]),
        },
        "feeds_losing_plates": missing,
        "hard_cases": sorted(unique.values(),
                             key=lambda e: e.get("plate_width_px", 0.0)),
        "temporal_neighbourhood": [
            {"feed_id": f, "frame_number": n} for f, n in neighbourhood],
    }
    write_json(Path(args.out), report)

    print("=" * 70)
    print(f"HARD CASES MINED: {len(unique)} unique from {len(samples)} samples")
    print("=" * 70)
    for tag, rows in sorted(chosen.items(), key=lambda kv: -len(kv[1])):
        print(f"    {tag:<32} {len(rows):>6}")
    print(f"\n    flagged for human review        "
          f"{report['counts']['flagged_for_human_review']:>6}")
    print(f"    temporal neighbourhood frames   {len(neighbourhood):>6}")
    if missing:
        print("\nFeeds losing the most plates (vehicles found, plates not):")
        for row in missing[:8]:
            print(f"    {row['feed_id']:<18} vehicles={row['plate_bearing_vehicles']:>4} "
                  f"plates={row['plate_boxes']:>4} miss={row['plate_miss_rate']:.0%}")
    print(f"\nWritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
