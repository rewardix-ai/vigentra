# ANPR baseline: every camera clip, before any change

- **Measured:** 17 Sep 2026.
- **Code:** the edge worker's engine as committed at `1e231ff`.
- **Hardware:** an Apple M1 (8 cores, 16 GB, MPS). The target is an RTX 3050 (4 GB) with 8 GB RAM, so the speed and memory figures below are indicative only.
- **Tools:**
  - `services/edge-worker/tools/anpr_benchmark.py` (run and report);
  - `anpr_profile_cameras.py`;
  - `anpr_gt_plates.py` and `anpr_gt_sheets.py` (ground truth).
- **Raw outputs:** `services/edge-worker/reports/anpr_benchmark/baseline/`.
  - `REPORT.md` and `CAMERA_PROFILES.md`.
  - Per clip: `summary.json`, `tracks.csv` (one row per vehicle track) and `vehicles.log` (the per-vehicle log).

## How it was run

- **Frame path.** The benchmark drives the engine exactly as `app/worker.py` drives a clip:
  - every frame is decoded;
  - `AdaptiveSampler` chooses frames (stride 5, 24-frame bursts);
  - `FrameQualityRouter` enhances dark frames;
  - `AnprEngine.process` runs per frame, and `finish()` runs at the end.
- **Scope.** The whole clip is processed. The deployed compose pass differs (see the finding on coverage) and is measured separately as `baseline_live`.
- **Plate settings.** Emission uses the compose defaults: confirmed plates only, review score 0.35.

**Ground truth** (`data/eval/anpr_gt/<camera>.csv`) is used only for scoring.

| Clips | Ground truth |
|---|---|
| Delhi | The by-eye inventory already verified for this clip: 20 readable plates, 11 partly readable. |
| cam06_1080p | The 1080p recording of grid cam06 from the research repo, with its by-eye inventory: 5 readable. |
| Grid clips | Every track of an independent YOLO11s + ByteTrack pass was checked by eye on its best plate proposal (≥ 22 px) in its 4 widest frames. |
| TfL clips (352×288, UK plates) | No plate is legible at that size. |
| cam09 | A night clip whose vehicles are only headlights; no plate is legible. |

On the grid clips, 1 plate is legible in full (cam07 GJ32AG0416) and a few only in part.

A read that fits a partly legible plate is counted as unverifiable, not as false.

## Camera inventory

Camera sites are from `data/reference/grid_cameras.json`. Resolution and fps are measured. Lighting is classed by median luma: dark < 50, dim < 80, otherwise bright.

| Camera | Source | Resolution | FPS | Site / scene | Camera type | Lighting | Tracks/min | Widest plate per track, p25/p50/p90 (px) | Mostly sees | Class |
|---|---|---|---:|---|---|---|---:|---|---|---|
| cam01 | rec_cam01.mp4 (grid) | 1280x720 | 25 | Chimanbhai Patel Bridge, RTO Circle | PTZ | bright | 259 | 20/24/69 | rear | DIFFICULT |
| cam02 | rec_cam02.mp4 | 1280x720 | 25 | Janpath T-Junction | PTZ | bright | 172 | 14/22/42 | both | DIFFICULT |
| cam03 | no recording | – | – | O.N.G.C. Office (municipal) | bullet | – | – | – | – | – |
| cam04 | rec_cam04.mp4 | 1280x720 | 25 | Paldi Junction | fixed | bright | 275 | 16/27/95 | rear | DIFFICULT |
| cam05 | rec_cam05.mp4 | 1280x720 | 25 | Visat Three-Way Junction | PTZ | bright | 295 | 18/20/30 | both | DIFFICULT |
| cam06 | rec_cam06.mp4 | 1280x720 | 25 | Madhuram Bypass Road | fixed | bright | 65 | 35/46/159 | front | MEDIUM |
| cam06_1080p | research repo sandbox | 1920x1080 | 23.2 | same camera, other recording | fixed | bright | – | – | front | – |
| cam07 | rec_cam07.mp4 | 1280x720 | 25 | Hero Showroom, Bhavani Char Rasta | fixed | dark | 16 | 54 (1 track) | rear | DIFFICULT |
| cam08 | rec_cam08.mp4 | 1280x720 | 25 | Majevadi Gate | PTZ | dim | 179 | 18/31/195 | rear | DIFFICULT |
| cam09 | rec_cam09.mp4 | 1280x720 | 25 | New Bypass, 66KV Substation | fixed | dark (luma 8) | 0 | – | front | EXTREME |
| cam10 | rec_cam10.mp4 | 1280x720 | 25 | Char Chowk | fixed | dim | 208 | 16/23/64 | rear | DIFFICULT |
| cam11 | rec_cam11.mp4 | 1280x720 | 25 | Dolatpara Gate | PTZ | bright | 317 | 12/17/51 | rear | VERY_DIFFICULT |
| cam12 | rec_cam12.mp4 | 1280x720 | 25 | Adalaj Toll Plaza lane 9 | ANPR-capable | bright | 9 | – | front | EXTREME |
| cam13 | rec_cam13.mp4 | 1280x720 | 25 | C.N. Vidyalaya Junction | RLVD | bright | 384 | 16/20/41 | rear | DIFFICULT |
| cam14 | rec_cam14.mp4 | 1280x720 | 25 | Delight Circle | RLVD | bright | 195 | 11/14/28 | both | VERY_DIFFICULT |
| cam15 | rec_cam15.mp4 | 1280x720 | 25 | Suvidha Park Junction | RLVD | bright | 146 | 15/19/34 | rear | VERY_DIFFICULT |
| cam16 | rec_cam16.mp4 | 1280x720 | 25 | Visat T-Junction | RLVD | bright | 433 | 14/20/64 | rear | DIFFICULT |
| delhi_1080p | delhi_1080p.mp4 (= traffic_01.mp4) | 1920x1080 | 29.74 | Delhi street, handheld | – | bright | 434 | 31/64/133 | front | GOOD |
| tfl_01–19 | tfl_*.mp4 | 352x288 | 25 | London JamCams (UK plates) | – | bright | – | ≤ 14 | – | EXTREME |

Notes on the table:
- **Grid bitrate.** The grid recordings run at 250–1200 kbps, against 25.7 Mbps for Delhi.
- **Timestamps.** Every grid overlay reads about 21:00, but most scenes are street-lit bright.
- **cam17–cam30** have no recording in the repository.
- **"Mostly sees"** comes from how vehicle boxes change size along a track: growing boxes mean approaching vehicles (front plates), shrinking boxes mean receding ones (rear plates).

## Baseline (whole clip, stride 5)

| Camera | Tracks | With plate candidate | Plate detections | OCR images | GT readable | Correct confirmed | False confirmed / emitted | Unconfirmed valid reads correct / wrong | Processed fps | CPU % | RAM MB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cam01 | 88 | 26 | 911 | 1481 | 0 | 0 | 0/0 | 0/7 | 1.61 | 105 | 1640 |
| cam02 | 49 | 24 | 267 | 567 | 0 | 0 | 0/0 | 0/4 | 2.53 | 91 | 1233 |
| cam04 | 143 | 47 | 1557 | 3414 | 0 | 0 | 0/0 | 0/15 | 1.42 | 151 | 2842 |
| cam05 | 78 | 10 | 103 | 222 | 0 | 0 | 0/0 | 0/2 | 2.77 | 81 | 1807 |
| cam06 | 27 | 19 | 637 | 1400 | 0 | 0 | 0/0 | 0/10 | 1.91 | 111 | 1974 |
| cam07 | 2 | 1 | 24 | 66 | 1 | 0 | 0/0 | 0/1 | 3.83 | 57 | 1549 |
| cam08 | 63 | 20 | 808 | 1419 | 0 | 0 | 0/0 | 0/4 | 1.77 | 121 | 2031 |
| cam09 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0/0 | 4.50 | 43 | 1339 |
| cam10 | 71 | 19 | 1441 | 824 | 0 | 0 | 0/0 | 0/1 | 2.19 | 108 | 2185 |
| cam11 | 200 | 48 | 4117 | 2068 | 0 | 0 | 0/0 | 0/3 | 2.15 | 106 | 3254 |
| cam12 | 1 | 0 | 0 | 0 | 0 | 0 | 0/0 | 0/0 | 5.95 | 47 | 1646 |
| cam13 | 149 | 57 | 1130 | 1605 | 0 | 0 | 0/0 | 0/6 | 2.83 | 128 | 2645 |
| cam14 | 50 | 17 | 422 | 103 | 0 | 0 | 0/0 | 0/1 | 4.30 | 57 | 2013 |
| cam15 | 56 | 35 | 2193 | 1025 | 0 | 0 | 0/0 | 0/1 | 3.81 | 74 | 3022 |
| cam16 | 78 | 14 | 349 | 706 | 0 | 0 | 0/0 | 0/1 | 2.99 | 128 | 1948 |
| delhi_1080p | 254 | 144 | 6857 | 16445 | 20 | **7** | 0/0 | 9/39 | 0.42 | 167 | 3218 |
| tfl_01–19 | 173 | 3 | 6 | 0 | 0 | 0 | 0/0 | 0/0 | ~3.8 | ~21 | ~1400 |
| **All** | **1482** | **484** | **20822** | **31345** | **21** | **7** | **0/0** | **9/95** | | | |

**Performance**
- MPS memory stayed at 1.2–2.3 GB.
- On 720p, each processed frame took 150–700 ms with other jobs sharing the GPU during the run, so these speeds are pessimistic.
- On the Delhi clip, the reading stage alone (at track close) took a large share of the 32 minutes.

**Vehicle-level result:** 7 of 21 readable plates correctly read (33 %), all on the Delhi clip. No false plate was confirmed or emitted.

**Unconfirmed valid-format reads:** 95 of these are wrong strings. They are not emitted with the compose defaults, but they appear in records. The dashboard shows CANDIDATE reads when unconfirmed emission is turned on.

**Delhi misses:**
- DL11SD3385, DL6SBE6415 (two-row scooters);
- DL1CW0942, DL1LAB9684, DL1LT1087, DL1RTA5056;
- DL5SAR5109, DL5SBW7737, DL6SAS6524, DL8CAP4175;
- UP13AY3893, UP14EC6398, UP78FH9291.

**cam07** misses its one readable plate: the engine kept only 2 vehicle tracks on this dark clip, where the independent tracker found 12.

## Findings that drive the next changes

1. **Coverage decides what the live system can read at all.**
   - The compose worker processes 25 frames per pass at stride 20, cycles through ≥ 23 cameras, and sleeps 120 s between cycles.
   - Every track is closed at the end of a pass.
   - The whole-clip numbers above are therefore an upper bound for the deployed service. The `baseline_live` run measures the deployed pass.
2. **Tracking loses vehicles on dark and low-frame-rate input.**
   - cam07: 2 tracks against 12.
   - At stride 5 a vehicle has to be matched on two processed frames before it exists (see `docs/anpr-audit.md` A7).
3. **Most grid plates are below the readable size.**
   - The median widest plate per track is 14–27 px on 11 of the 15 grid clips.
   - Only cam06 and cam07 reach 46–54 px.
   - At these sizes and bitrates a person cannot read them either, so the ceiling on the grid recordings is set by the footage, not by the reader. The engine must not guess them: it confirmed none, correctly.
4. **Reading and decisions leave plates behind on the one clip where plates are legible.**
   - Delhi: 7 of 20.
   - The research repo's decision rules replayed on the same banks give 9 of 20 with none wrong. This needs near backing turned off: with it on, `DL6SAS6522` was confirmed against the true `DL6SAS6524`.
5. **Plate-candidate junk.** Signboards ("CHITRA"), the burned-in site names and timestamps, and lamps dominate the proposals on several grid clips. The retro proposer is on and there is no score floor after the geometry prior (audit B2).
