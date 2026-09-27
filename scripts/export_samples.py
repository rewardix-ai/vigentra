#!/usr/bin/env python3
"""Export the sample artefacts the reference models ask for, from the running platform's database.

    python3 scripts/export_samples.py [--out deliverables/samples]

Model 1 asks for a sample onboarded camera-metadata dataset and a sample gap-analysis report;
Model 3 for a sample federated analytics report. The dashboard shows all three live; these are the
same figures as files a reader can open without the platform. Read-only SQL through the Postgres
container of `docker compose` (no sign-in, nothing written to the database).
"""
from __future__ import annotations

import argparse
import csv
import io
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

CONTAINER = "vigentra-postgres-1"


def sql(query: str) -> list[dict]:
    out = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "sentinel", "-d", "sentinel", "-c",
                          f"COPY ({query}) TO STDOUT WITH CSV HEADER"], check=True, capture_output=True, text=True)
    return list(csv.DictReader(io.StringIO(out.stdout)))


REGISTRY = """
select camera_id, external_camera_id, source_system, name, camera_type, owning_department, owning_unit,
       district, city, road_or_junction, latitude, longitude, vms_name, vms_vendor, resolution, fps, codec,
       retention_days, installation_date, installation_status, approval_status, health_status,
       last_heartbeat_utc, video_access_enabled, sync_status, last_metadata_sync_utc
from cameras order by source_system, camera_id"""

PER_SOURCE = """
select c.source_system, s.display_name as source, s.adapter as reached_through, s.department,
       count(*) filter (where c.installation_status <> 'DECOMMISSIONED') as cameras_in_service,
       count(*) filter (where lower(c.health_status) = 'online' and c.installation_status <> 'DECOMMISSIONED') as online,
       coalesce(sum(d.n), 0) as detections, coalesce(sum(d.plates), 0) as detections_with_plate,
       coalesce(sum(p.n), 0) as plate_sightings, coalesce(sum(i.n), 0) as incidents,
       coalesce(sum(a.n), 0) as watchlist_alerts,
       min(d.first) as first_detection_utc, max(d.last) as last_detection_utc
from cameras c join sources s on s.source_system_id = c.source_system
left join (select camera_id, count(*) n, count(plate_text) plates, min(timestamp_utc) first, max(timestamp_utc) last
           from detections group by 1) d on d.camera_id = c.camera_id
left join (select camera_id, count(*) n from plate_sightings group by 1) p on p.camera_id = c.camera_id
left join (select camera_id, count(*) n from incidents group by 1) i on i.camera_id = c.camera_id
left join (select camera_id, count(*) n from watchlist_alerts group by 1) a on a.camera_id = c.camera_id
group by 1, 2, 3, 4 order by 1"""

CLASSES = """
select c.source_system, d.class_name, count(*) n from detections d join cameras c using (camera_id)
group by 1, 2 order by 1, 3 desc"""

INCIDENTS = """
select c.source_system, i.kind, count(*) n from incidents i join cameras c using (camera_id)
group by 1, 2 order by 1, 3 desc"""

PER_CAMERA = """
select c.source_system, c.camera_id, c.name, c.owning_department, c.district, c.health_status,
       (select count(*) from detections d where d.camera_id = c.camera_id) detections,
       (select count(*) from plate_sightings p where p.camera_id = c.camera_id) plate_sightings,
       (select count(distinct plate_normalised) from plate_sightings p where p.camera_id = c.camera_id) distinct_plates,
       (select count(*) from incidents i where i.camera_id = c.camera_id) incidents,
       (select count(*) from watchlist_alerts a where a.camera_id = c.camera_id) watchlist_alerts
from cameras c where c.installation_status <> 'DECOMMISSIONED' order by 1, 2"""


def table(rows: list[dict], cols: list[str]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r.get(c) or "") for c in cols) + " |" for r in rows]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="deliverables/samples")
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    # --- Model 1: the onboarded camera-metadata dataset ------------------------------------
    cams = sql(REGISTRY)
    with open(out / "camera_registry_sample.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cams[0]))
        w.writeheader()
        w.writerows(cams)

    # --- Model 1: the gap-analysis report (same rules as GET /api/v1/reports/gap-analysis) --
    active = [c for c in cams if c["installation_status"] != "DECOMMISSIONED"]
    districts: dict = {}
    for c in active:
        d = districts.setdefault(c["district"] or "unknown", {"district": c["district"] or "unknown", "cameras": 0,
                                                              "online": 0, "degraded": 0, "offline": 0, "unavailable": 0,
                                                              "departments": set()})
        d["cameras"] += 1
        d["departments"].add(c["owning_department"])
        key = (c["health_status"] or "").lower()
        if key in d:
            d[key] += 1
    for d in districts.values():
        d["thin"] = "yes" if d["cameras"] < 3 else ""
        d["departments"] = ", ".join(sorted(x for x in d["departments"] if x))
    rows = sorted(districts.values(), key=lambda d: -d["cameras"])
    ageing = []
    for c in active:
        try:
            years = (date.today() - date.fromisoformat((c["installation_date"] or "")[:10])).days / 365.25
        except ValueError:
            continue
        if years >= 5:
            ageing.append({**c, "age_years": f"{years:.1f}"})
    decommissioned = len(cams) - len(active)
    offline = [c for c in active if (c["health_status"] or "").lower() not in ("online", "degraded")]
    (out / "gap_analysis_sample.md").write_text(f"""# Sample gap-analysis report

Generated {stamp} from the running Vigentra registry (`scripts/export_samples.py`); the dashboard's
*Reports → Gap analysis* page shows the same figures live, scoped to the viewer's departments.

{len(active)} cameras in service across {len(districts)} districts, {decommissioned} decommissioned and kept
on the register for the audit trail. A district is **thin** when fewer than 3 cameras in it are in
service; a camera is **ageing** at 5 years from installation.

## Coverage by district

{table(rows, ["district", "cameras", "online", "degraded", "offline", "unavailable", "thin", "departments"])}

## Cameras not online now

{table(offline, ["camera_id", "name", "owning_department", "district", "health_status", "last_heartbeat_utc"]) if offline else "None: every camera in service is online or degraded."}

## Ageing infrastructure (5 years or more)

{table(ageing, ["camera_id", "name", "owning_department", "district", "installation_date", "age_years"]) if ageing else "None of the cameras in service is recorded as installed 5 or more years ago."}

## Reading it

- Thin districts are where coverage planning starts: a single camera is a blind spot the day it fails.
- Cameras offline now are listed with their last heartbeat, so a maintenance agency can be sent to one.
- Installation dates come from each department's installation register; grid cameras have none, so they
  cannot age out of this report and are listed by their catalogue entry instead.
""")

    # --- Model 3: the federated analytics report -------------------------------------------
    per_source = sql(PER_SOURCE)
    classes, kinds = sql(CLASSES), sql(INCIDENTS)
    top = {}
    for r in classes:
        top.setdefault(r["source_system"], []).append(f"{r['class_name']} {int(r['n']):,}")
    kind = {}
    for r in kinds:
        kind.setdefault(r["source_system"], []).append(f"{r['kind'].lower().replace('_', ' ')} {r['n']}")
    for r in per_source:
        r["top classes"] = ", ".join(top.get(r["source_system"], [])[:4])
        r["incidents by kind"] = ", ".join(kind.get(r["source_system"], [])) or "none"
    per_camera = sql(PER_CAMERA)
    with open(out / "federated_analytics_by_camera.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(per_camera[0]))
        w.writeheader()
        w.writerows(per_camera)
    (out / "federated_analytics_sample.md").write_text(f"""# Sample federated analytics report

Generated {stamp} from the running Vigentra platform (`scripts/export_samples.py`). Each row is one
federated source system - a departmental VMS or the Sentinel grid - reached through its own adapter;
the analytics above the adapters are the same for all of them. Per-camera figures:
`federated_analytics_by_camera.csv`.

{table(per_source, ["source", "reached_through", "department", "cameras_in_service", "online", "detections", "plate_sightings", "incidents", "watchlist_alerts"])}

`sentinel_grid_adapter` is the Sentinel government grid (its cameras are registered to the department that
owns them); `traffic_adapter` is the Traffic Police's own VMS, carrying our Delhi test clip and 19 public
TfL JamCam feeds (Powered by TfL Open Data).

## What each source produced

{table(per_source, ["source", "department", "top classes", "incidents by kind", "first_detection_utc", "last_detection_utc"])}

## Reading it

- **Detections** are per-frame boxes, not unique vehicles: a vehicle crossing a view is detected in many
  frames. Unique vehicles are counted per clip in the ANPR evaluation (`docs/anpr-optimisation.md`).
- **Plate sightings** are settled readings, one per vehicle pass; **watchlist alerts** are sightings
  matched against the watchlist at ingest.
- The sources differ in dialect (field names, timestamps, authentication) and in capability (the grid has
  no archive and no installation register); none of that reaches this report, which is the point of the
  federation layer.
""")
    print(f"wrote {out}/: camera_registry_sample.csv ({len(cams)} cameras), gap_analysis_sample.md, "
          f"federated_analytics_sample.md, federated_analytics_by_camera.csv ({len(per_camera)} cameras)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
