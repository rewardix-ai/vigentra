#!/usr/bin/env python3
"""Replay the crop banks of a benchmark run through the reading stage only.

    python tools/anpr_replay.py --from baseline --tag reading_v2 [--clips rec_cam06 ...] [--config-dir DIR]

Detection, tracking and banking are taken as recorded by `anpr_benchmark.py run --tag <from>`
($ANPR_BENCH_BANKS/<from>/<camera>/*.pkl). Each bank goes through this checkout's reading and decision
code, in the order the run closed them: `ANPRPipeline._finalise_track`, then `flush()`, which does the
retroactive sign check and the fragment merge. `AnprEngine`'s own emission rules then decide what would
be sent.

A change to reading, fusion or the confirm rules can be measured this way in minutes. A change to
detection, tracking, sampling or banking cannot: it needs a full `run`.

Writes reports/anpr_benchmark/<tag>/<camera>/{summary.json, tracks.csv, records.json} in the shape
`anpr_benchmark.py report` reads. Detection-stage fields are copied from the source run.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import sys
import time
from collections import Counter
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent
BANKS = Path(os.getenv("ANPR_BENCH_BANKS", str(Path.home() / "Downloads" / "ANPR_BENCH")))
OUT = WORKER / "reports" / "anpr_benchmark"


def replay(camera: str, src_tag: str, tag: str, device: str) -> dict:
    from anpr.read.crnn import CRNNReader
    from app.anpr_engine import AnprEngine, _track_number

    src = json.load(open(OUT / src_tag / camera / "summary.json"))
    src_tracks = {r["vehicle_id"]: r for r in csv.DictReader(open(OUT / src_tag / camera / "tracks.csv"))}
    bank_dir = BANKS / src_tag / camera
    ocr = Counter()
    orig = CRNNReader.probs

    def probs(self, batch):
        ocr["images"] += len(batch)
        return orig(self, batch)

    CRNNReader.probs = probs
    try:
        engine = AnprEngine(camera_id=camera)
        pipe = engine._pipeline
        st = bank_dir / "static_text.pkl"
        if st.exists():
            pipe.static_text = pickle.load(open(st, "rb"))
        t0 = time.perf_counter()
        for f in sorted(bank_dir.glob("[0-9]*_*.pkl")):
            pipe._finalise_track(pickle.load(open(f, "rb")))
        sightings = engine.finish()
        wall = time.perf_counter() - t0
    finally:
        CRNNReader.probs = orig
    records = list(pipe.records)
    emitted = {int(s.track_id): s for s in sightings}
    rows = []
    for r in records:
        s0 = src_tracks.get(r["track_id"], {})
        s = emitted.get(_track_number(r["track_id"]))
        rows.append({**s0, "OCR_result": r.get("plate") or "", "OCR_confidence": r.get("confidence"),
                     "status": r.get("status"), "reason": r.get("reason") or "",
                     "valid_format": int(bool(r.get("valid_format"))), "emitted": int(s is not None),
                     "emitted_confirmed": int(bool(s and s.confirmed)),
                     "final_result": s.text if s else ("UNREADABLE" if s0.get("number_of_plate_candidates", "0") != "0"
                                                       else "NO_PLATE_DETECTED")})
    out = OUT / tag / camera
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tracks.csv", "w", newline="") as fh:
        keys = list(rows[0].keys()) if rows else ["vehicle_id"]
        w = csv.DictWriter(fh, keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    json.dump([{k: v for k, v in r.items() if not k.startswith("_")} for r in records],
              open(out / "records.json", "w"), indent=1, default=str)
    summary = dict(src)
    summary.update({
        "replayed_from": src_tag, "replay_wall_s": round(wall, 1), "ocr_images": ocr["images"],
        "unique_vehicle_tracks": len(records),
        "tracks_with_ocr_result": sum(1 for r in records if r.get("plate")),
        "unique_ocr_results": len({r["plate"] for r in records if r.get("plate")}),
        "status_counts": dict(Counter(r.get("status") for r in records)),
        "confirmed_plates": sorted({r["plate"] for r in records if r.get("status") == "CONFIRMED"}),
        "emitted": [{"plate": s.text, "confirmed": s.confirmed, "score": round(s.confidence, 3), "track": s.track_id}
                    for s in sightings],
        "unreadable_reasons": dict(Counter((r.get("reason") or "").split(":")[0] for r in records
                                           if r.get("status") == "UNREADABLE")),
    })
    json.dump(summary, open(out / "summary.json", "w"), indent=1, default=str)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--clips", nargs="*")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--config-dir")
    ap.add_argument("--models-dir")
    a = ap.parse_args()
    os.environ["ANPR_MODELS_DIR"] = a.models_dir or str(WORKER / "models")
    os.environ["ANPR_CONFIG_DIR"] = a.config_dir or str(WORKER / "config")
    os.environ["ANPR_DEVICE"] = a.device
    os.environ.setdefault("ANPR_REVIEW_SCORE", "0.35")
    os.environ.setdefault("ANPR_EMIT_UNCONFIRMED", "false")
    sys.path.insert(0, str(WORKER))
    os.chdir(WORKER)
    cams = [c[4:] if c.startswith("rec_") else c for c in a.clips] if a.clips else \
        sorted(d.name for d in (OUT / a.src).iterdir() if (d / "summary.json").exists())
    for cam in cams:
        if not (BANKS / a.src / cam).is_dir():
            print(f"{cam}: no banks under {BANKS / a.src / cam}")
            continue
        s = replay(cam, a.src, a.tag, a.device)
        print(f"{cam}: {len(s['confirmed_plates'])} confirmed {s['confirmed_plates']}, "
              f"{len(s['emitted'])} emitted, {s['replay_wall_s']} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
