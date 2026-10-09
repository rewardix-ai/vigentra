#!/usr/bin/env python3
"""Map pins for grid cameras the survey has never seen (event day: the grid grows from 30 to 50).

New grid cameras federate on their own (central adapters/grid_adapter.py claims any camera the survey
does not list), but the grid's catalogue sends only an id and a name, so they arrive with no
coordinates and every sighting at them falls off the traced route's map. This finds them and proposes
a pin for each from its name, geocoded by OpenStreetMap Nominatim (free; one request a second, as its
usage policy asks), marked `geo_confidence: geocoded-unverified` so the map says how far to trust it.

    python scripts/survey_new_grid_cameras.py                # propose: data/reference/grid_cameras_new.json
    (check each proposed pin against the camera's view, fix or drop rows)
    python scripts/survey_new_grid_cameras.py --apply        # merge into grid_cameras.json
    docker compose up -d --no-deps central-api               # the adapter reads the survey at start

Nothing about the camera leaves this machine except its public place name.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SURVEY = REPO / "data" / "reference" / "grid_cameras.json"
PROPOSED = REPO / "data" / "reference" / "grid_cameras_new.json"
PG = ["docker", "exec", "-i", "vigentra-postgres-1", "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F"|"']


def unplaced() -> list[tuple[str, str, str]]:
    """(grid id, name, district) of every grid camera in the registry without coordinates."""
    sql = ("SELECT external_camera_id, name, coalesce(district, '') FROM cameras "
           "WHERE external_camera_id LIKE 'GRID-%' AND (latitude IS NULL OR longitude IS NULL) ORDER BY 1;")
    out = subprocess.run(PG, input=sql, capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        ext, name, district = (line.split("|") + ["", ""])[:3]
        rows.append((ext.replace("GRID-", ""), name, district))
    return rows


def geocode(name: str, district: str) -> tuple[dict | None, str]:
    """The best pin for a camera name, and how good it is: the full name, the name without its
    bracketed part, its first three words, the town it starts with ("Gandhidham Rambaugh p2"), and
    last the district's centre (local road names are often missing from OpenStreetMap)."""
    import re

    base = re.sub(r"\(.*?\)", "", name).strip(" ,-")
    tries = [(name, "geocoded-unverified"), (base, "geocoded-unverified"),
             (" ".join(base.split()[:3]), "geocoded-unverified"),
             (base.split()[0] if base.split() else "", "town-centre"),   # names often start with the town
             ("", "district-centre")]
    seen = set()
    for q, quality in tries:
        if (q, quality) in seen or (not q and not district):
            continue
        seen.add((q, quality))
        hit = _nominatim(", ".join(p for p in (q, district, "Gujarat", "India") if p))
        if hit:
            return hit, quality
    return None, "unplaced"


def _nominatim(q: str) -> dict | None:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "vigentra-event-survey/1.0 (hackathon camera registry)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        hits = json.load(resp)
    time.sleep(1.1)   # Nominatim: at most one request a second
    return hits[0] if hits else None


def load_survey() -> tuple[object, list[dict]]:
    data = json.loads(SURVEY.read_text())
    return data, (data["cameras"] if isinstance(data, dict) else data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="merge the checked proposals into the survey")
    args = ap.parse_args()
    data, rows = load_survey()
    known = {str(r.get("grid_id") or r.get("camera_no") or r.get("id")) for r in rows}
    if args.apply:
        new = [r for r in json.loads(PROPOSED.read_text()) if str(r["grid_id"]) not in known]
        rows.extend(new)
        SURVEY.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"merged {len(new)} cameras into {SURVEY.relative_to(REPO)}; restart central-api to load them")
        return 0
    proposals = []
    for grid_id, name, district in unplaced():
        if grid_id in known:
            continue
        hit, quality = geocode(name, district)
        row = {"grid_id": grid_id, "site_name": name, "district": district or None,
               "road_or_junction": name, "camera_type": "fixed", "installation_purpose": "traffic monitoring",
               "geo_confidence": quality,
               "geo_source": "OpenStreetMap Nominatim from the camera's name; check against the view"}
        if hit:
            row.update(latitude=hit["lat"], longitude=hit["lon"], geo_source=row["geo_source"] + f" ({hit.get('display_name', '')[:120]})")
        proposals.append(row)
        print(f"{grid_id:8s} {name[:50]:50s} -> {quality}: {(hit or {}).get('lat', '-')}, {(hit or {}).get('lon', '')}")
    PROPOSED.write_text(json.dumps(proposals, indent=2, ensure_ascii=False) + "\n")
    print(f"{len(proposals)} proposed in {PROPOSED.relative_to(REPO)}: check each pin, then --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
