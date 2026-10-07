"""Find vehicles read on more than one grid camera, ready to trace.

Two readings are the same vehicle when they come from different cameras showing the same moment
(their video times within --window minutes) and their plates match within --max-distance, priced
by the platform's own confusion-weighted distance (B/8, O/0 ... 0.35; anything else 1.0), the
same matcher the watchlist uses. Readings are linked into groups, and each group is printed as a
route in video time with the leg distances and implied speeds, impossible legs flagged.

    /Users/uchit/Downloads/ANPR/.venv/bin/python scripts/cross_camera.py [--min-confidence 0.3]
    /Users/uchit/Downloads/ANPR/.venv/bin/python scripts/cross_camera.py --plate GJ01AB1234   # one plate

Readings come from the per-camera tables (scripts/camera_plates.sql) and need read_at_video.
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "central-api"))
from app.services.plate_matching import plate_distance  # noqa: E402

PG = ["docker", "exec", "-i", "vigentra-postgres-1", "psql", "-U", "sentinel", "-d", "sentinel", "-At", "-F", "|"]


def rows(sql: str) -> list:
    out = subprocess.run(PG + ["-c", sql], capture_output=True, text=True, check=True).stdout
    return [line.split("|") for line in out.splitlines() if line]


def km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2, dl = math.radians(la1), math.radians(la2), math.radians(lo2 - lo1)
    return 6371 * 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-confidence", type=float, default=0.3)
    ap.add_argument("--max-distance", type=float, default=1.0)
    ap.add_argument("--window", type=float, default=30, help="minutes of video time between two sightings")
    ap.add_argument("--plate", help="trace this plate only (near misreads included)")
    args = ap.parse_args()

    tables = [t for (t,) in rows("SELECT table_name FROM information_schema.tables WHERE table_schema = 'camera_plates' AND table_name ~ '^cam[0-9]+$'")]
    union = " UNION ALL ".join(f"SELECT '{t}', plate, read_at_video, confidence, frames, confirmed FROM camera_plates.{t} WHERE read_at_video IS NOT NULL" for t in tables)
    reads = [dict(cam=c, plate=p, video=v, conf=float(cf or 0), frames=int(fr or 0), confirmed=co == "t")
             for c, p, v, cf, fr, co in rows(union)]
    where = {f"cam{int(e.split('cam')[1]):02d}": (float(la), float(lo), name)
             for e, la, lo, name in rows("SELECT external_camera_id, latitude, longitude, name FROM cameras WHERE external_camera_id LIKE 'GRID-%'")}
    import datetime as dt
    for r in reads:
        r["t"] = dt.datetime.fromisoformat(r["video"])

    if args.plate:
        hits = sorted((r for r in reads if plate_distance(args.plate.upper(), r["plate"]) <= args.max_distance), key=lambda r: r["t"])
        groups = [hits] if hits else []
    else:
        pool = [r for r in reads if r["conf"] >= args.min_confidence or r["confirmed"]]
        parent = list(range(len(pool)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, a in enumerate(pool):
            for j in range(i + 1, len(pool)):
                b = pool[j]
                if abs((a["t"] - b["t"]).total_seconds()) <= args.window * 60 and plate_distance(a["plate"], b["plate"]) <= args.max_distance:
                    parent[find(i)] = find(j)
        clusters = {}
        for i, r in enumerate(pool):
            clusters.setdefault(find(i), []).append(r)
        groups = [sorted(g, key=lambda r: r["t"]) for g in clusters.values() if len({r["cam"] for r in g}) > 1]
        groups.sort(key=lambda g: (-len({r["cam"] for r in g}), -max(r["conf"] for r in g)))

    if not groups:
        print("no vehicle read on more than one camera yet")
        return 0
    for g in groups:
        best = max(g, key=lambda r: (r["confirmed"], r["conf"], r["frames"]))
        print(f"\n{best['plate']}: {len({r['cam'] for r in g})} cameras, {len(g)} readings")
        prev = None
        for r in g:
            lat, lon, name = where.get(r["cam"], (0, 0, r["cam"]))
            leg = ""
            if prev and prev["cam"] != r["cam"] and r["cam"] in where and prev["cam"] in where:
                d = km(where[prev["cam"]][:2], (lat, lon))
                hours = (r["t"] - prev["t"]).total_seconds() / 3600
                speed = d / hours if hours > 0 else float("inf")
                leg = f"  <- {d:.1f} km in {hours * 60:.1f} min" + (f", {speed:.0f} km/h" if hours > 0 else "") + ("  IMPOSSIBLE" if speed > 200 else "")
            print(f"  {r['t']:%d %b %H:%M:%S}  {r['cam']} {name[:40]:40}  {r['plate']:11} conf {r['conf']:.2f} {'confirmed' if r['confirmed'] else 'review'}{leg}")
            prev = r
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
