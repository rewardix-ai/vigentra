# Sample federated analytics report

Generated 27 Sep 2026 16:37 UTC from the running Vigentra platform (`scripts/export_samples.py`). Each row is one
federated source system - a departmental VMS or the Sentinel grid - reached through its own adapter;
the analytics above the adapters are the same for all of them. Per-camera figures:
`federated_analytics_by_camera.csv`.

| source | reached_through | department | cameras_in_service | online | detections | plate_sightings | incidents | watchlist_alerts |
|---|---|---|---|---|---|---|---|---|
| Municipal Corporation VMS | sentinel_grid_adapter | Municipal Corporation | 7 | 7 | 30712 | 62 | 37 | 0 |
| Traffic Police VMS | sentinel_grid_adapter | Traffic Police | 23 | 23 | 219026 | 515 | 601 | 0 |
| Traffic Police local VMS | traffic_adapter | Traffic Police | 20 | 20 | 129908 | 537 | 31 | 0 |

`sentinel_grid_adapter` is the Sentinel government grid (its cameras are registered to the department that
owns them); `traffic_adapter` is the Traffic Police's own VMS, carrying our Delhi test clip and 19 public
TfL JamCam feeds (Powered by TfL Open Data).

## What each source produced

| source | department | top classes | incidents by kind | first_detection_utc | last_detection_utc |
|---|---|---|---|---|---|
| Municipal Corporation VMS | Municipal Corporation | person 10,219, car 7,904, truck 5,148, bus 3,976 | wrong way 27, sudden stop 9, collision candidate 1 | 2026-09-11 06:07:10+00 | 2026-09-27 07:21:58+00 |
| Traffic Police VMS | Traffic Police | car 112,032, truck 67,146, motorcycle 21,029, bus 12,975 | sudden stop 332, wrong way 238, stopped in lane 17, collision candidate 14 | 2026-09-11 06:07:09+00 | 2026-09-27 07:23:28+00 |
| Traffic Police local VMS | Traffic Police | car 65,771, motorcycle 43,329, truck 10,206, bus 6,976 | wrong way 15, sudden stop 14, collision candidate 2 | 2026-09-14 13:48:20+00 | 2026-09-27 16:37:13+00 |

## Reading it

- **Detections** are per-frame boxes, not unique vehicles: a vehicle crossing a view is detected in many
  frames. Unique vehicles are counted per clip in the ANPR evaluation (`docs/anpr-optimisation.md`).
- **Plate sightings** are settled readings, one per vehicle pass; **watchlist alerts** are sightings
  matched against the watchlist at ingest.
- The sources differ in dialect (field names, timestamps, authentication) and in capability (the grid has
  no archive and no installation register); none of that reaches this report, which is the point of the
  federation layer.
