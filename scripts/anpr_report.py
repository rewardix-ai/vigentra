#!/usr/bin/env python3
"""The ANPR output report: every plate the network read, with its timestamp.

The challenge asks for "an output report showing detected vehicles or number
plates with corresponding timestamps" alongside the government-feed
demonstration. This produces it from the platform's own API rather than from
the database, so the report is evidence that the API works as well as evidence
of what was read.

    python scripts/anpr_report.py --since-hours 24 --out reports/anpr_report

Writes <out>.md and <out>.csv. Every row carries the frame count and whether
the reading was confirmed, because one frame is a guess and twelve frames
agreeing is a reading - and an operator is entitled to know which they are
looking at.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def call(base: str, path: str, token: str | None = None, payload: dict | None = None):
    req = urllib.request.Request(base.rstrip("/") + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=os.getenv("CENTRAL_API_URL", "http://localhost:8000"))
    ap.add_argument("--username", default=os.getenv("REPORT_USERNAME", "traffic.ops"))
    ap.add_argument("--password", default=os.getenv("REPORT_PASSWORD", ""))
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--camera", default=None)
    ap.add_argument("--out", default="reports/anpr_report")
    a = ap.parse_args()

    if not a.password:
        print("Set REPORT_PASSWORD (or pass --password) for the reporting account.", file=sys.stderr)
        return 2

    try:
        auth = call(a.base, "/api/v1/auth/login", payload={"username": a.username, "password": a.password})
    except urllib.error.HTTPError as exc:
        print(f"sign-in failed: {exc.code} {exc.reason}", file=sys.stderr)
        return 2
    token = auth.get("access_token") or auth.get("token")

    query = f"/api/v1/sightings?since_hours={a.since_hours}&limit=1000"
    if a.camera:
        query += f"&camera_id={a.camera}"
    rows = call(a.base, query, token)
    if not isinstance(rows, list):
        rows = rows.get("items", [])

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    fields = ["timestamp_utc", "plate_text", "camera_id", "confidence", "observations", "confirmed", "reader"]
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            prov = r.get("provenance") or {}
            w.writerow({
                "timestamp_utc": r.get("timestamp_utc"),
                "plate_text": r.get("plate_text"),
                "camera_id": r.get("camera_id"),
                "confidence": r.get("confidence"),
                "observations": r.get("observations") or prov.get("plate_observations"),
                "confirmed": prov.get("plate_confirmed"),
                "reader": r.get("reader"),
            })

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    cameras = sorted({r.get("camera_id") for r in rows if r.get("camera_id")})
    lines = [
        "# ANPR output report",
        "",
        f"Generated {generated} from `{a.base}/api/v1/sightings` "
        f"(last {a.since_hours}h). {len(rows)} reading(s) across {len(cameras)} camera(s).",
        "",
        "Every reading carries the number of frames that agreed and whether the",
        "track's vote settled. An unconfirmed reading is evidence to look at, not",
        "a plate to act on. Readings whose grammar prior showed an impossible",
        "state or district are not emitted at all.",
        "",
        "| Timestamp (UTC) | Plate | Camera | Confidence | Frames | Confirmed |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        prov = r.get("provenance") or {}
        lines.append(
            f"| {r.get('timestamp_utc','')} | `{r.get('plate_text','')}` | {r.get('camera_id','')} "
            f"| {r.get('confidence','')} | {r.get('observations') or prov.get('plate_observations','')} "
            f"| {'yes' if prov.get('plate_confirmed') else 'no'} |"
        )
    if not rows:
        lines.append("| _no readings in this window_ | | | | | |")
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"  {len(rows)} reading(s) -> {out.with_suffix('.md')} and {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
