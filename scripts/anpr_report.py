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


def _grammar_scorer():
    """The engine's own plate grammar, if it can be imported.

    Returns a callable text -> prior, or None. Imported lazily and by path
    because this script runs on the host while the grammar lives with the edge
    worker; a missing import must degrade to an unfiltered report with a
    warning, not to a crash at report time.
    """
    worker = Path(__file__).resolve().parent.parent / "services" / "edge-worker"
    if str(worker) not in sys.path:
        sys.path.insert(0, str(worker))
    try:
        from anpr.plate_grammar import normalise, score_string
    except Exception:
        return None

    def prior(text: str) -> float:
        try:
            return float(score_string(normalise(text)).prior)
        except Exception:
            return 0.0

    return prior


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
    ap.add_argument(
        "--min-confidence", type=float, default=0.10,
        help="drop readings the engine itself scored below this (0 keeps all). "
             "Grammar plausibility alone does not separate a real read from "
             "noise: GJ232212 (0.651, genuine) and GJ170156 (0.000, noise) "
             "score the same 0.150 prior, because both are shaped like a "
             "registration. Confidence is what tells them apart.",
    )
    ap.add_argument(
        "--min-grammar-prior", type=float, default=0.12,
        help="drop readings whose plate grammar prior is below this (0 keeps all). "
             "Rows written before the edge-side floor existed can name a state or "
             "district that does not exist; this keeps them out of the report.",
    )
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

    # The edge drops implausible readings before they are ever sent, but rows
    # stored before that floor existed are still in the registry. A report is
    # the one artefact that must not carry an invented registration, so it is
    # filtered here too rather than trusting the write path alone.
    dropped: list[str] = []

    if a.min_confidence > 0:
        kept = []
        for r in rows:
            try:
                conf = float(r.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf >= a.min_confidence:
                kept.append(r)
            else:
                dropped.append(f"{r.get('plate_text','?')} (conf {conf:.3f})")
        rows = kept

    if a.min_grammar_prior > 0:
        scorer = _grammar_scorer()
        if scorer is None:
            print("  note: plate grammar unavailable; report not filtered", file=sys.stderr)
        else:
            kept = []
            for r in rows:
                text = (r.get("plate_text") or "").strip()
                prior = scorer(text) if text else 0.0
                if prior >= a.min_grammar_prior:
                    kept.append(r)
                else:
                    dropped.append(f"{text} ({prior:.3f})")
            rows = kept

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
        (f"{len(dropped)} reading(s) withheld as implausible: "
         + ", ".join(dropped) + "." if dropped else ""),
        "" if dropped else None,
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
    out.with_suffix(".md").write_text("\n".join(l for l in lines if l is not None) + "\n", encoding="utf-8")

    print(f"  {len(rows)} reading(s) -> {out.with_suffix('.md')} and {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
