# ANPR system audit

Audit date: 2026-09-17. Code state: commit `9174c92` ("Require glyphs in the pixels before the edge
worker confirms a plate"); tracked files were clean when the audit started.

**Scope and method.** This was read-only. No code was changed, no pipeline or Docker command was run,
and no environment file was read.
- Every statement below comes from the code, the configuration, the git history or the documents,
  and carries a `file:line` reference.
- Five facts were measured directly:
  - video metadata and frame statistics, taken with plain OpenCV (A4, B5);
  - ONNX reader input shapes and metadata, read with onnxruntime;
  - YOLO checkpoint metadata, read with torch;
  - grammar priors, computed by calling `score_string` (A15);
  - file hashes.
- Ultralytics tracker behaviour was read in the research venv's **ultralytics 8.4.147**. The Docker
  image pins **8.4.123** (`EW/requirements-yolo.txt:9`), so tracker claims assume the two versions
  behave alike.
- Numbers attributed to "research LOOP_LOG" are that repo's own measurements. They were not re-run
  here.

**Where effective values could differ.** Compose values such as `EDGE_MAX_FRAMES` and
`ANPR_EMIT_UNCONFIRMED` can be overridden in `.env`, which was not read. This audit quotes the
defaults in `docker-compose.yml`.

**Work in progress during the audit.** Untracked files appeared while the audit ran:
`EW/tools/anpr_benchmark.py`, `anpr_gt_sheets.py`, `anpr_gt_reconcile.py`,
`anpr_profile_cameras.py` and `data/eval/`. An `anpr_benchmark.py run --tag baseline` process was
running at the time. None of these is reviewed here.

Path prefixes used throughout:

| Prefix | Path |
|---|---|
| `EW/` | `services/edge-worker/` |
| `CA/` | `services/central-api/app/` |
| `DB/` | `services/dashboard/` |
| `R/` | research repo `/Users/uchit/Downloads/ANPR/` |
| `compose` | `docker-compose.yml` |

---

## Part A — the system as it is

### A1. Architecture

Five runtime pieces touch ANPR:

| Piece | Definition | What runs | ANPR |
|---|---|---|---|
| `edge-worker` service (profile `anpr`) | `compose:193-247`; image target `anpr` (`EW/Dockerfile:75-106`) | `python -m app.worker --all-cameras --forever --max-frames 25 --sample-interval 20 --cycle-seconds 120` (`compose:241-245`); CPU-only torch (`EW/Dockerfile:54`); models mounted read-only (`compose:235-236`) | yes (`ANPR_ENABLE=true`, `compose:216`) |
| `grid-snapshots` | `compose:258-289`, target `yolo` | `tools/snapshot_wall.py --detect --vms-url …` (`compose:280-283`); the `yolo` target has no `anpr/` or `config/` | no |
| `detector-traffic`, `detector-municipal` | `compose:28-58, 302-316` | the same worker with YOLO11n only; `ANPR_ENABLE` is unset, so `build_engine()` returns `None` (`EW/app/anpr_engine.py:401-403`) | no |
| Host readers (Mac) | not in the repo | per operator notes, a scratchpad launcher (`mps_worker.py`) runs the same worker supervisor with `ANPR_DEVICE=mps`, one process per AI account, `--cycle-seconds 120`; it is lost on reboot. The only in-repo host launcher is `scripts/edge-worker.sh`, whose `.venv` has no ultralytics installed (checked) | yes |
| Host live wall | `EW/tools/snapshot_wall.py --anpr <ids>` (not in compose) | the full engine on chosen feeds, drawn onto the served JPEGs | yes, display only |

The `edge-worker` service signs in as `traffic.ai`, as `detector-traffic` does, so Traffic cameras are
processed twice (`compose:194-197`).

Code layout under `EW/`:

- **`app/`** — the Vigentra side:
  - `worker.py`: CLI, per-pass loop, supervisor, central-API client.
  - `anpr_engine.py`: adapter over the engine, `PlateSighting`, `EngineCache`.
  - `frame_quality.py`, `grid.py` (RTSP capture), `detectors.py` (plain YOLO or mock).
  - `plates.py`: `PLATE_BEARING_CLASSES`.
- **`anpr/`** — the engine vendored from `R/anpr/` (drift in A21):
  - `pipeline.py` orchestrates everything.
  - `detect/`: `vehicle`, `rtdetr_vehicle`, `plate`, `tiling`, `corners`, `overlay_mask`,
    `static_text`, `char_evidence`.
  - `track/crop_bank.py`.
  - `enhance/`: `quality`, `rectify`, `register`, `fuse`, `glare`, `denoise`, `deblur`, `sr`,
    `binarize`.
  - `read/`: `crnn`, `parseq`, `awiros`, `ensemble`, `beam_grammar`, `rows`.
  - `fuse/rover.py`, `plate_grammar.py`, `evidence.py`.
  - `sources/frame_source.py`: only its `Frame` dataclass is used (`EW/app/anpr_engine.py:162`).
  - Edge-only modules: `sampling.py` and `incidents.py`.
- **`config/`** — what the code reads, and what it does not:
  - `thresholds.yaml`: engine thresholds.
  - `bytetrack.yaml`: tracker settings.
  - `roi.yaml`: overlay masks.
  - `india_codes.yaml`: grammar data.
  - `cameras.yaml`, `profiles.yaml`, `plate_kinds.yaml`: no edge code reads them (A17).
- **`config.yaml`** at the service root belongs to the previous engine (PaddleOCR,
  `plate_detector.pt`, BoT-SORT). No code loads it, and the Dockerfile does not copy it.
- **`models/`, `tools/`, `tests/`** — covered in B3 and A20.

### A2. Inference entry points

| Entry point | Where | Frames and clock | Sampling | Engine lifecycle | Output |
|---|---|---|---|---|---|
| Worker, live grid camera (Docker `edge-worker`, host readers) | `EW/app/worker.py:1093` `main` → `supervise` (936-1090) → `run` (528-858) | `iter_grid_frames` (146-171) over `grid.ReconnectingCapture`; stream PTS in seconds (168) | `AdaptiveSampler(stride=--sample-interval)` (626-630); every delivered frame is examined up to `max_frames × stride` (647-648); the pass ends after `--max-frames` processed frames (665-666) | `EngineCache` per camera (984; `EW/app/anpr_engine.py:438-466`); a cached engine gets `new_stream()` (449); `finish()` runs at pass end (792-799) | detections in batches of 50 (787-790); plates only at pass end (433-436) |
| Worker `--clip` | same | `iter_clip_frames` decodes every frame (88-118, 640); timestamp is `time.time()` (392) | same sampler | a one-shot run builds its own engine (`build_engine()`, 558) | same (`--dry-run` sends nothing) |
| Worker, brokered session (non-grid camera) | `iter_session_frames` (174-194) | downloads the stream to a temp file, then takes the clip path; wall clock (652) | frames are already strided (652), so a burst processes consecutive *yielded* frames, which are still a stride apart | as above | as above |
| Host readers | not in the repo (A1) | as the first row | as the first row | as the first row | as the first row |
| Live-wall ANPR | `EW/tools/snapshot_wall.py:345-447` | one decoder per camera: a newest-frame grabber thread for live feeds (391-404), a paced reader for VMS recordings (414-423); `captured_at=time.time()` (424-426); never passes `discontinuity` | none: whatever the engine keeps up with | one `AnprEngine` per camera for the life of the process (376); `finish()`, `flush()`, `reset()` and `new_stream()` are never called | labels drawn on frames (431-438); nothing ingested |
| Demo renderer | `EW/tools/annotate_video.py:73-193` | file decode; PTS from `CAP_PROP_POS_MSEC` (171) | `--stride`, default 1 (80) | one engine; `pipe.flush()` at the end (180) | MP4 plus a stdout summary (190-192) |
| Research CLI | `R/run_anpr.py` | the research `FrameSource` classes | `frame_stride` | own | JSON dump, evidence packs (not audited) |

### A3. Video decoding, RTSP and timestamps

**Transport**
- `open_capture` probes the RTSP port: 5 s connect timeout, and a handshake slower than 3 s counts as
  unusable (`EW/app/grid.py:336-365, 561-579`).
- There is no HLS fallback; the function raises instead (581-590). The module docstring still says it
  falls back to HLS (29-31).
- FFmpeg is pinned to TCP with a 10 s socket timeout, set through `OPENCV_FFMPEG_CAPTURE_OPTIONS`
  before the first capture (106-132).

**Reconnects**
- `ReconnectingCapture` tolerates 25 failed reads after a connect, then reconnects with backoff
  from 2 s to 30 s (398-402, 444-450).
- The worker gives a feed 90 s without a frame (`EDGE_GRID_STALL_SECONDS`, `EW/app/worker.py:69`),
  then `CaptureStalled` is raised (`grid.py:486-491`).
- `CaptureStalled` is a `RuntimeError`, and `run()` catches only `DetectorError` (`worker.py:808`).
  A stall therefore skips `_finish_pass`: detections not yet batched are dropped. The engine's open
  tracks are settled at the next `EngineCache.get` → `new_stream()` (`anpr_engine.py:229-236, 449`),
  but only if the engine is still cached.

**Timestamps and discontinuities**
- The PTS is `CAP_PROP_POS_MSEC` (`grid.py:503`).
- A backwards step of more than 5 s, or one landing below 1.5 s, is treated as the recording looping
  and flags `discontinuity` (403-414, 507-516).
- A smaller backwards step is counted as jitter: no reset, and the frame carries no `dt` (517-523).
- `_last_pts` is not cleared on reconnect, so a reconnect is flagged only when its PTS meets the same
  rule.
- Grid feeds are looping recordings (`grid.py:403-414, 510-512`). The same vehicles reappear on every
  loop.
- A flagged frame bypasses the sampler (`worker.py:669`). The engine then settles and resets the
  tracker (`anpr_engine.py:248-249`), and so does the pipeline (`EW/anpr/pipeline.py:246-249`).

**Wall-clock paths.** The clip path (`worker.py:383-392`) and the live wall
(`snapshot_wall.py:424-426`) pass `time.time()` as the capture time. This has three effects:
- The fragment-merge window of 3 s (`pipeline.py:375-384`) is measured in processing time, not
  video time.
- `captured_at_pts` and the detection-id seed (`worker.py:477, 506`) carry wall-clock seconds.
  Replaying the same clip therefore does not collapse to the same ids, which contradicts the promise
  at `worker.py:471-475`.
- Incident speeds are computed on wall time (`worker.py:774`).

The research `FrameSource` has two-frame hysteresis on PTS jumps (`EW/anpr/sources/frame_source.py:61-107`).
The worker does not use it.

### A4. Frame skipping: AdaptiveSampler and FrameQualityRouter

**AdaptiveSampler** (`EW/anpr/sampling.py:52-112`)
- In scan mode it processes every `stride`-th frame (88-90).
- A processed frame whose widest plate-bearing detection is at least 180 px wide (`0.25 × w ≥ 45`,
  lines 37, 42, 97) re-arms a burst of 24 consecutive frames (66, 98-102).
- The widths come from the engine's tracked vehicles (`worker.py:711-716`,
  `anpr_engine.py:264-281`).

**Budget**
- In compose, the stride is 20 and the pass budget is 25 processed frames (`compose:243-244`).
- On a grid camera the pass examines at most 25 × 20 = 500 delivered frames, about 20 s at 25 fps
  (`worker.py:648`).
- One 24-frame burst consumes almost the whole budget, so a pass that bursts ends about 1 s after the
  trigger.
- Every open track is closed at the end of each pass (`anpr_engine.py:286-290`,
  `pipeline.py:327-329`). A vehicle still approaching is judged on whatever was banked so far.

**Duty cycle.** Cameras run one after another, and the worker sleeps `--cycle-seconds 120` after the
whole list (`worker.py:1047-1084`). The `traffic.ai` account covers at least the 23 Traffic Police grid
cameras (`data/reference/grid_cameras.json`: 23 Traffic Police, 7 Municipal); department VMS cameras
may add more. Even if every pass took only its 20 s of video, one cycle would last at least
23 × 20 + 120 = 580 s. Each camera is therefore watched for at most about 3 % of wall time, and less
once inference time is counted.

**FrameQualityRouter** (`EW/app/frame_quality.py:77-292`) works on whole-frame statistics only. It is
built with its defaults (`worker.py:547`); nothing is set per camera.

| Condition | Result | Frame given to ANPR |
|---|---|---|
| mean luma ≥ 200, or ≥ 18 % of pixels ≥ 250 (line 191) | OVEREXPOSED; skipped only when clipped ≥ 0.85 (199) | the original |
| mean luma ≤ 60 (209) | LOW_LIGHT | CLAHE (clip 2.0, 8×8) applied to the LAB L channel of the whole frame (244-259); this enhanced frame reaches the engine (`worker.py:673, 697`) |
| well-exposed and Laplacian variance < 40 (225) | BLURRED; skipped below 12 (231) | the original |

**Measured on the evaluation clips.** Plain OpenCV was run on every 25th frame, re-implementing the
router's thresholds:
- No sampled frame of any `rec_cam*` clip or of `delhi_1080p` would be skipped.
- `rec_cam07` (median luma 41) and `rec_cam09` (median luma 10) are LOW_LIGHT on every sampled
  frame, and `rec_cam08` on 4 of 45. Every other clip is NORMAL.
- Median Laplacian variance ranges from 98 to 2212. The blur gate never fires on this footage and says
  nothing about blur at plate level.

### A5. Overlay mask and static text

**Manual rectangles**
- `roi.yaml` holds per-camera rectangles (`EW/anpr/detect/overlay_mask.py:40-63, 88-104`).
- Only `cam01_bridge_night`, a screen-recording id, has any (`EW/config/roi.yaml:22-30`).
- The engine's `camera_id` is the canonical registry id (`worker.py:558` → `anpr_engine.py:147` →
  `pipeline.py:180`). The live wall uses grid ids such as `cam06`. Every live camera therefore falls
  through to `default: exclude: []`.
- `lanes` is parsed (`overlay_mask.py:52`) but nothing uses it.

**Automatic mask**
- It is finalised after 40 stored frames, taking every third pushed frame (`roi.yaml:7`,
  `overlay_mask.py:110-118`). That is 118 pushed frames.
- Three rules build it:
  - rendered static text (131-146);
  - bright text that persists across the warm-up, with full-width OSD strips derived for the top and
    bottom 18 % bands (156-220);
  - a 6 px dilation (222-223).
- The road band is never auto-masked (227-228). An over-masking guard drops the auto mask above 12 %
  of the frame (231-235).
- Until the mask is ready, only the manual mask applies (84-85).

**Overlay by motion**
- During warm-up, the full-frame plate detector runs on every third frame, for up to 12 frames
  (`pipeline.py:255-258`).
- Boxes that repeat in 60 % of those frames, with IoU ≥ 0.8, inside the OSD bands, are masked
  (`overlay_mask.py:244-273`).

**Effect of the mask on detection**
- Masked pixels are zeroed before vehicle detection (`EW/anpr/detect/vehicle.py:57-59`).
- A vehicle more than 50 % masked is skipped (`pipeline.py:285`).
- A plate more than 15 % masked is skipped (`pipeline.py:299`, `thresholds.yaml:25`).

**Consequence in Docker.** At least 23 cameras rotate through an `EngineCache` of 4
(`anpr_engine.py:75`), so every lookup misses (docstring at 421-423). Each pass pushes at most 25
frames. In that deployment the auto mask can never finalise (118 frames are needed), and the warm-up
plate detector costs time on every pass. This is inferred from the code and config, not seen in logs.

**Static scene text**
- Every banked candidate is checked against a `StaticTextMap` (`pipeline.py:312-315`;
  `EW/anpr/detect/static_text.py:75-99`).
- A position is learned as a sign when three conditions hold:
  - the same frame box (IoU ≥ 0.6) has already been seen under another track;
  - that track's vehicle box has IoU < 0.7 with this one;
  - the thumbnails match (NCC ≥ 0.85).
- There is no minimum time gap between the two sightings; the research copy adds one (A21).
- Learned positions also demote finished records retroactively at flush (`pipeline.py:330-337`).
- The code defaults apply, because `thresholds.yaml` has no `static_text` section
  (`pipeline.py:226`).

### A6. Vehicle detector

**Model and settings**
- `VehicleTracker` (`vehicle.py:27-86`) loads `models/yolo11s.pt` (`anpr_engine.py:80`), a COCO
  model.
- Settings from `thresholds.yaml`:
  - `imgsz` 1920 (line 13);
  - `conf` 0.15 (line 7);
  - classes 2, 3, 5, 7 = car, motorcycle, bus, truck (lines 8-12; `vehicle.py:16`).
- NMS is class-agnostic (`vehicle.py:30, 73-75`). Its IoU is left at the Ultralytics default of 0.7,
  and `max_det` at the default of 300.
- `half` is stored (39) but never passed to `track()`, so inference runs in FP32.

**Input size.** Frames larger than `imgsz` are shrunk with INTER_AREA (65-69). A 1280×720 frame is
not, and Ultralytics' `LetterBox` (whose `scaleup` defaults to True, checked in 8.4.147) enlarges it
1.5× to 1920. `yolo11s` was trained at 640 (checkpoint `train_args`), so it runs here at 3× its
training size.

**Coverage.** There is no auto-rickshaw or bicycle class. The geometry prior's
`("motorcycle", "auto")` branch (`EW/anpr/detect/plate.py:92`) never receives "auto" from this
detector.

**RT-DETR backend.** An RT-DETR UVH-26 backend exists (`pipeline.py:169-172`,
`EW/anpr/detect/rtdetr_vehicle.py`). The adapter never selects it, and its weights and `third_party`
code are not under `EW/`.

**Device.** `"auto"` resolves to CUDA if available, otherwise CPU (`vehicle.py:89-96`); it never picks
MPS. `ANPR_DEVICE` overrides it (`anpr_engine.py:70, 202`).

**Separate detector.** The plain detector used when ANPR is off (`EW/app/detectors.py`) is a different
model: YOLO11n at conf 0.45, with automatic device selection that does include MPS
(`detectors.py:243, 271-283, 433-436`).

### A7. Tracker

**Configuration**
- Ultralytics ByteTrack is invoked as `model.track(persist=True, tracker="config/bytetrack.yaml")`
  (`vehicle.py:29, 73-75`). The path is relative to the working directory, which is `/app` in the
  image (`EW/Dockerfile:21, 96`).
- The effective settings (`EW/config/bytetrack.yaml:5-10`): `track_high_thresh` 0.30,
  `track_low_thresh` 0.05, `new_track_thresh` 0.20, `track_buffer` 45, `match_thresh` 0.8,
  `fuse_score` true.
- The `tracker:` block in `thresholds.yaml:29-35` (0.4 / 0.1 / 0.5) is never read, except
  `track_buffer`, which closes stale banks (`pipeline.py:324`).

**Behaviour in ultralytics 8.4.147**
- Only detections scoring at least `track_high_thresh` can start a track. New tracks come only from
  the high-score set (`trackers/byte_tracker.py:316-317, 480`), so `new_track_thresh: 0.20` does
  nothing below 0.30.
- **First association:** cost = 1 − IoU × score must be ≤ 0.8. A detection scoring 0.30 therefore
  needs IoU ≥ 0.67 with the predicted box.
- **Low-score detections** match only tracks that are still active, and need IoU ≥ 0.5
  (byte_tracker.py:424-431).
- **New tracks are hidden until confirmed.** A new track (except on the tracker's first frame) is
  output only after it matches again on a later update, with cost ≤ 0.7, i.e. IoU × score ≥ 0.3
  (108-109, 452-460, 494). A detection scoring 0.30 meets that only at IoU = 1.0; one scoring 0.5
  needs IoU ≥ 0.6.
- **Consequence:** a vehicle that is not associated on two processed frames never appears in
  `last_vehicles` and is never searched for a plate (`pipeline.py:282-290`).

**Timing.** The Kalman filter steps once per processed frame. The sampler alternates gaps of 20 frames
and 1 frame, so the motion model sees irregular time steps.

**IDs and resets**
- IDs have the form `s{session}_t{id}` (`vehicle.py:84`).
- `reset()` bumps the session number and resets the Ultralytics trackers (`vehicle.py:43-53`). That
  also resets Ultralytics' class-global id counter (`BaseTrack._count`), which every tracker in the
  process shares.
- Resets happen on every discontinuity, and at the start of every pass through `new_stream()`. The
  live wall never resets.
- The numeric id sent to central-api is the trailing digits (`anpr_engine.py:120-134`), so it repeats
  across sessions.

**Fragment merging** happens only after reading, in `_merge_fragments` (`pipeline.py:346-432`).
- Two records are merged when their final plates are within one edit and their last vehicle boxes
  either overlap in time with IoU ≥ 0.3, or follow within 3 s with IoU ≥ 0.2 (375-384).
- `merge_by_hypotheses` is not set, so it is off (370).
- Every member keeps its own record and receives the merged read (423-427). The adapter emits each
  one (`anpr_engine.py:301-350`), so a merged vehicle still produces one sighting per fragment.
- `_hyps` are removed at the end of every flush (428-432), so a record can never merge with one from
  a later flush. The "merge fragments seen either side of a pass boundary" rationale for
  `_RECORD_TAIL` (`anpr_engine.py:84-86`) does not hold.

**Bank closing.** A bank closes when its track has not been touched for more than 45 processed frames
(`EW/anpr/track/crop_bank.py:106-112`), on a discontinuity, or at flush.

### A8. Plate detector

**Model.** `PlateDetector` (`plate.py:116-207`) loads `plate_det_mix_n.pt`, a one-class YOLO11n. It runs
at `imgsz` 640 with conf 0.2 (`thresholds.yaml:14-15`); NMS IoU stays at the Ultralytics default of
0.7. There is one `predict` call per vehicle per frame, with no batching (`pipeline.py:284-290`,
`plate.py:153-157`).

**Region of interest**
- The search region is the vehicle box plus a 5 % margin (`plate.py:165-167`); crops under 8 px are
  skipped (168-169).
- The crop is upscaled with INTER_CUBIC until its long side reaches 640 (173-178,
  `thresholds.yaml:16`), then letterboxed to 640.
- Tiling is off (`tile_size: 0`; 143-151).
- The full frame is searched only during mask warm-up (A5).

**Skip for small vehicles.** `plate_search_min_vehicle_px: 96` skips plate search on narrower vehicles
(`pipeline.py:167, 288`; `thresholds.yaml:28`). Those vehicles are still tracked, and their banks are
still touched.

**Retro-reflective proposer** (on: `thresholds.yaml:26`)
- It proposes bright, low-saturation or yellow blobs with fill ≥ 0.45 and aspect 1-6.
- The proposal score is multiplied by 0.6 (`plate.py:37-71, 184-186`).

**Candidate filtering**
- Candidates narrower than 8 px or shorter than 3 px are dropped (192-193).
- **Geometry prior:** the score is multiplied by a prior (194-200, 74-113) with these penalties:

  | Test | Penalty |
  |---|---|
  | box aspect outside 1-6 | down to a floor of 0.1 |
  | height outside 0.04-0.25 of the vehicle (0.05-0.40 for motorcycles) | down to a floor of 0.2 |
  | box centre in the top 25 % of the vehicle | × 0.5 |
  | width more than 0.60 of the vehicle | × (0.60 / fraction)^6, floor 0.05 |

- A box with aspect below 2.6 is flagged `two_row` (82).
- No floor is applied after the prior; the prior only re-ranks candidates. The research copy adds one
  (A21).
- CNN and retro candidates go through class-agnostic NMS at IoU 0.4 (202-205). The top two by
  post-prior score are kept (207).
- The pipeline banks one per vehicle per frame (`plates_per_vehicle: 1`, `thresholds.yaml:27`;
  `pipeline.py:294-298`). That is whichever candidate scores highest after the prior, CNN or retro.

**Unused and missing pieces**
- `aspect_soft_band` and `plate_h_frac_of_vehicle` in `thresholds.yaml:19-24` are never read. The
  values actually used are constants in `EW/anpr/plate_grammar.py:213-221`.
- There is no one-plate-one-track rule: vehicle boxes that overlap can bank the same plate into several
  tracks (A21).

### A9. Corner estimation and rectification

**Crop.** The banked crop is the plate box plus a 12 % margin on width and 25 % on height
(`pipeline.py:302-307`).

**Corners.** `estimate_corners` (`EW/anpr/detect/corners.py:28-70`) works as follows:
- upscale to at least 120 px wide;
- Otsu threshold, then a 7×3 close;
- take the best contour covering ≥ 15 % of the area with a `minAreaRect` aspect of 1.2-6.5;
- confidence = fill × area fraction.

The pipeline uses these corners when confidence ≥ 0.2. Otherwise it falls back to the corners of the
un-margined box (`pipeline.py:316-320`). There is no learned corner model; the module says so
("no learned head yet", `corners.py:3`).

**Rectification**
- `rectify` warps the crop to 384×92 for a single row or 256×128 for two rows, with INTER_CUBIC and
  a replicated border (`EW/anpr/enhance/rectify.py:8-31`).
- After fusion, a Hough-based deskew corrects up to 12° (`rectify.py:34-51`; `pipeline.py:646`).

### A10. Crop bank and best-frame selection

**What each candidate stores** (`crop_bank.py:14-24`)
- the image (BGR, native resolution, with the margin);
- corners, the plate box in frame coordinates, frame index, `pts_ms`;
- `det_conf` (the score after the prior), `quality` (`CropQuality`), `two_row`.

It does not store the raw CNN score, the prior, the source (cnn or retro), the vehicle box in that
frame, the crop's OCR result or the preprocessing used.

**`CropQuality`** (`EW/anpr/enhance/quality.py:15-27, 102-135`) holds:

| Field | How it is measured |
|---|---|
| `width_px`, `height_px` | of the margined crop |
| `sharpness_lap`, `tenengrad` | on a copy resized to 128 px wide |
| `skew_deg` | from the corners; 0 with box corners |
| `local_contrast` | CLAHE standard deviation of the 15-85 % height band |
| `bloom_frac` | share of pixels ≥ 250 |
| `blur_extent`, `blur_angle_deg` | FFT anisotropy, marked "calibrated later" (86-87) |
| `dark_frac` | share of pixels ≤ 20 |
| `quality_score` | the weighted sum below |

**Quality score**

`q = 0.30·s_w + 0.25·s_sharp + 0.15·s_con + 0.10·s_bloom + 0.05·s_dark + 0.10·s_blur + 0.05·s_skew`
(133-134), where:
- `s_w = clip((w−12)/78)`, multiplied by 0.3 when h < 8;
- `s_sharp = lap/300`, `s_con = lc/45`;
- `s_bloom = 1−4·bloom`, `s_dark = 1−2·dark`;
- `s_blur = 1−ext/12`, `s_skew = 1−skew/30` (122-132).

**Width floors use the margined width.** `width_px` is the width of the margined crop, about 1.24× the
plate box when the frame edge does not clip it. Every width floor (gate 22 px, confirm 40 px) is
therefore applied to the margined width, which corresponds to plates of about 18 px and 32 px.

**Ranking.** `rank = quality × (0.4 + 0.6·det_conf)` (`crop_bank.py:56-61`). It drives `top_k`,
`best` and the fusion order.

**Capacity.** At most 64 crops per track (`thresholds.yaml:38`). On overflow the bank is sorted by
`quality_score`, not by rank, and truncated (`crop_bank.py:49-54`).

**Selection**
- `best` is the crop with the highest rank (`crop_bank.py:66-68`).
- Fusion uses the top 12 by rank (`thresholds.yaml:37`; `pipeline.py:502, 519`).
- The first 6 of those are also read individually (`reading.single_crops`, `thresholds.yaml:63`;
  `pipeline.py:664-677`).
- Because `read_filter` and `string_vote` are on, every banked crop (up to 64) is read on its own by
  both fast readers (`pipeline.py:505-507, 703-725`).
- The fusion pool is limited to crops whose own read is a valid registration, provided at least
  min(3, n) such crops exist (508-518).
- Glyphs are counted on the 8 best crops (596-597; `char_evidence.py:85-87`).
- There is no rule for spreading the selected frames over time.

**Leak.** Closed banks are never removed from `CropBankStore.banks`: nothing in `crop_bank.py:79-118`
or `pipeline.py` deletes them. Each one keeps up to 64 images for the life of the engine, and
`close_stale` scans them all on every frame.

### A11. Track close: legibility gate and static-text checks

These checks run in `_finalise_track` (`pipeline.py:453-499`):

- **Legibility gate** on the best crop: width ≥ 22, height ≥ 8, Laplacian ≥ 8.0, local contrast
  ≥ 25.0 (`thresholds.yaml:1-5`; `pipeline.py:435-451`). A failure gives UNREADABLE.
- **Static overlay text:** at least 5 crops, plate centre moving less than 3 px while the vehicle
  moves more than 30 px (477-482).
- **Learned static scene text** at the best box (484-485), and again retroactively at flush
  (330-337).
- **No reader loaded:** the track becomes CANDIDATE (495-500).

### A12. Enhancement (`_read_layout`, `pipeline.py:618-689`)

This runs once per crop group and once per row layout.

**1. Rectify.** Every crop in the top k is rectified; each gets weight max(quality, 0.05) (621-628).

**2. Fuse** (`EW/anpr/enhance/fuse.py:54-92`)
- Each crop is registered to the best one with Euclidean ECC at 2×, retried as translation-only when
  cc < 0.3 (`EW/anpr/enhance/register.py:82-92`).
- A crop is dropped if ECC does not converge, cc < 0.25, or bloom > 35 %.
- The kept crops are combined by weighted median when at least 3 remain, otherwise by weighted mean.
- **Multi-frame SR:** shift-and-add at 3× with 8 Richardson-Lucy iterations, whenever at least 3
  crops are kept (`thresholds.yaml:42`; `fuse.py:89-91, 95-126`). It runs ECC again on every crop.

**3. Enhance the fused image**
- glare suppression: MSR with σ 5/15/40, then CLAHE (2.0, grid 4×2) on the 12-88 % band
  (`EW/anpr/enhance/glare.py:22-37`);
- non-local-means denoise, h = 7 (`EW/anpr/enhance/denoise.py:20-25`);
- Wiener deblur when the best crop's blur extent is ≥ 1.5 (`EW/anpr/enhance/deblur.py:59-69`);
- deskew.

**4. Build the variants**

| Variant | Weight |
|---|---|
| `fused_gray` | 1.0 |
| `enhanced` | 1.0 |
| `mfsr` | 0.9 (`is_sr=False`) |
| `sr` | added only when a learned SR model exists; none does under `EW/models`, so the Lanczos upscale is computed and thrown away (`EW/anpr/enhance/sr.py:28-46`; `pipeline.py:659-662`) |
| `single0`-`single5` | 0.5 × quality; each is a rectified crop after glare suppression (664-672) |

**Options that are off**
- `fused_weight_by_registration` is not set, so the fused variants keep weight 1.0 however few crops
  registration kept (649-651).
- The photometric variants (`clahe`, `gamma` for dark or bright crops, `sharpen`; 82-97) exist, but
  `reading.single_variants` is not set, so none are produced (676).
- There is no binarised variant (654-656), although the documentation lists binarisation (A22).

**Two-row plates read both ways.** `two_row_both_ways` is on (`thresholds.yaml:62`), so a squat box
goes through this stage twice, once for each layout (`pipeline.py:533-541`).

**Night.** There is nothing plate-specific beyond MSR and CLAHE, plus the whole-frame CLAHE of A4.

### A13. OCR readers

**Loaded in the edge worker.** Two readers load:
- `reader_crnn.onnx`, named `crnn`;
- `reader_crnn_v6.onnx`, named `crnn_reader_crnn_v6`.

The adapter looks for `reading.extra_crnn_weights` in `ANPR_MODELS_DIR` and passes every file it finds
(`anpr_engine.py:186-192`, tested in `EW/tests/test_second_reader.py`). The pipeline gives extra CRNNs
their own names (`pipeline.py:194-202`).

**Model I/O** (read from the ONNX files)
- Both take `[batch,1,64,256]` input and return `[batch,64,37]`: 36 symbols plus the CTC blank.
- The primary carries no `two_row_mode` metadata, so it defaults to `split`. v6 is `side_by_side`
  (`EW/anpr/read/crnn.py:79-94`).
- Preprocessing: resize to 256×64 (INTER_AREA when shrinking, INTER_CUBIC when enlarging), then
  normalise to [−1, 1] (`crnn.py:63-67`).

**Two-row plates**
- **Split mode:** the plate is cut at the ink valley within 38-62 % of its height, with 6 % overlap
  (`EW/anpr/read/rows.py:9-29`). Each row is read, the rows' greedy strings are joined, and the result
  is kept when grammar-valid, with weight × 1.2. A beam search over the concatenated row
  probabilities is also run (`EW/anpr/read/ensemble.py:86-106`).
- **Side-by-side mode:** the two rows are placed next to each other with a 25 % gap and read as one
  line (`rows.py:32-47`; `ensemble.py:82-85`).

**Decoding**
- CTC prefix beam search: beam 16, top 12 symbols per step (`EW/anpr/read/beam_grammar.py:64-122`;
  `ensemble.py:43`).
- Prefixes that fit no template are pruned (40-52).
- 0.15 of each symbol's probability is spread to its confusable partners (71-82).
- Final score = log p + log(format prior); the top 3 strings are returned (125-140).
- Each hypothesis is weighted: variant weight × mean character probability × (1 for the top string,
  0.4/rank for the others) (`ensemble.py:112-115`).

**Not loaded**
- PARSeq: added only when `reader_weights` is None (`pipeline.py:182-191`), and there are no weights.
- Awiros PP-OCRv5:
  - the config lists only `readers: [crnn]` (`thresholds.yaml:71`);
  - the reader needs `models/awiros`, `third_party/PaddleOCR` and `paddle`, none of which are under
    `EW` (`EW/anpr/read/awiros.py:51-58`; `EW/requirements-anpr.txt:10-13`).
- Reader v10: exists only in research, where it was not adopted (`R/reports/LOOP_LOG.md:367-369`).

**Execution provider.** ONNX Runtime uses CUDA when available, otherwise CPU
(`EW/anpr/enhance/denoise.py:28-38`). On a Mac the CRNNs run on the CPU even with `ANPR_DEVICE=mps`.

### A14. Fusion and the confirm decision

**ROVER** (`EW/anpr/fuse/rover.py:126-189`)
- It votes on the single most-weighted string length (`n_lengths=1`).
- The vote is positional and weighted, aligned to an anchor string (63-123).
- By default each slot's winner is assembled into a string (`supported_strings_only` is off). The
  result can be a string no hypothesis produced (comment at 66-68).
- Agreement = share of weight within one edit of the winner (155-156).
- `raw = mean character share × (0.5 + 0.5·prior) × agreement`.
- A disagreeing SR read costs × 0.6 (176-177).
- Calibration uses temperature 0.3 (`thresholds.yaml:47`), applied to the logit (192-196).
- Alternates are strings within two edits.
- A winning string that looks like overlay text is blanked (`pipeline.py:685-687`).

**Per-crop string vote** (`read_filter` and `string_vote` on, `thresholds.yaml:79-80`)
- `_crop_reads` keeps a crop's top read only if it has at least 8 characters and a mean character
  probability ≥ 0.5, and is a valid registration of a known state that is not barred by
  `vote_reject` (`pipeline.py:692-725`; `thresholds.yaml:100`).
- `_vote_entries` weights each read by rank × probability. The winning string needs at least 2
  distinct crops.
- Confidence = share × (1 − (1 − mean p)^n) (744-774).

**Second reader as fallback** (`thresholds.yaml:94-95`; `pipeline.py:552-569`)
- The primary reader votes alone first.
- If that vote fails `_decide`, the pooled vote of both readers is taken. When its winner differs,
  it replaces the primary's string and is tagged `string_vote_secondary`.
- Such a read is never confirmed, because `secondary_confirm_min_glyphs` is not set (802-808).

**`_decide`** (776-849) applies these rules in order:
- The reader-agreement options `confirm_min_reader_agreement` and `confirm_require_primary` are not
  set, so those checks are inactive.
- Glyph floor: at least 3 (799-801; `thresholds.yaml:86`).
- **String-vote reads:** at least 4 crops read the string exactly, vote share ≥ 0.4, best width
  ≥ 40, runner-up < 0.5 × winner (809-821; `thresholds.yaml:65, 88`).
- **ROVER reads:** all of the following:
  - confidence ≥ 0.75, width ≥ 40;
  - at least 6 hypotheses;
  - weakest character share ≥ 0.5;
  - at least 3 frames, where frames = max(n_used, n_agree);
  - runner-up < 0.5 × winner;
  - at least 2 single crops agreeing (822-849).
- Merged fragments are decided again by the same rules (A7).

**Defect 1 — the fallback decision always sees 0 glyphs**
- `pipeline.py:559` calls `_decide(sv[0], n_used, sv[1], best.quality.width_px, sv[2])` without the
  `glyphs` argument, and glyphs are counted only later (596-597).
- With `confirm_min_glyphs: 3`, that call always fails (`no_glyph_evidence:0`).
- So whenever the pooled vote's winner differs from the primary's, the primary's string is replaced
  by a read that can never be confirmed (560-563), even when the primary alone would have confirmed.
- Research fixed this by counting glyphs before the vote (`R/anpr/pipeline.py:566-571`;
  `R/reports/LOOP_LOG.md:341`).

**Defect 2 — two-row reads are credited to a reader called "rows"**
- `pipeline.py:722` credits each crop read to `h.source.split("/")[-1]`.
- A per-row two-row read has the source `…/crnn/rows` (`ensemble.py:105`), so it is credited to
  `rows`.
- The primary-only vote tests `"crnn" in r[3]` (556), so these reads are left out of it.
- Research fixed this with `split("/")[1]` (`R/anpr/pipeline.py:733`). LOOP_LOG measured 22 of 45
  reads affected on cam06 t39 (`LOOP_LOG.md:340`).

**Missing from the edge copy:** research's `_vote_pick`, with `vote_any_reader`, `vote_unopposed`,
`vote_near_backing` and `string_vote_contested` (`R/anpr/pipeline.py:774-852, 911-913`; A21).

### A15. Indian format validation

**Templates** (`plate_grammar.py:71-99`)

| Format | Prior |
|---|---|
| Standard `AADD` + 2 letters + 4 digits | 1.0 |
| Standard, 1 or 3 letters + 4 digits | 0.5 |
| Standard, no letters + 4 digits | 0.15 |
| Standard, no letters + fewer digits | 0.03 |
| Standard, other shapes | 0.08 |
| Delhi single-digit district (DL only) | 0.6 / 0.35 |
| Bharat series | 0.15 |
| Diplomatic | 0.01 |
| Vintage | 0.005 |
| Temporary | 0.01 |

**Penalties** in `score_string` (145-192):
- unknown state × 0.05;
- state other than the preferred one (GJ) × 0.7;
- GJ district outside 01-39 × 0.1;
- district out of range for 8 listed states × 0.3;
- district 00 × 0.05;
- I or O in the series × 0.35;
- Bharat year outside 19-35 × 0.2.

`EW/config/india_codes.yaml` supplies 40 state codes, the GJ RTO list 01-39, and district ranges for
GJ, DL, MH, RJ, MP, KA, TN and UP.

**Confusable characters** (`plate_grammar.py:110-121`): O↔0 (plus D, Q, U), I↔1 (plus L, T, 7),
S↔5 (plus 8), B↔8 (plus 3, R), Z↔2 (plus 7), G↔6, A↔4 and others.
- They are used only as probability mass (0.15) inside the beam search (`beam_grammar.py:71-82,
  152-156`), together with pruning of letter and digit slots.
- There is no deterministic per-slot coercion. The `coerce`, `TO_LETTER` and `TO_DIGIT` helpers exist
  only in `R/anpr/plate_grammar.py`.

**Overlay filter** (224-240). It also rejects valid three-letter series: `GJ01REC1234` and
`GJ01CAM0123` are grammar-valid with prior 0.5, yet `looks_like_overlay` is True for both
(computed).

**Adapter floor.** `MIN_GRAMMAR_PRIOR` is 0.12 (`anpr_engine.py:66, 319-328`). Computed priors with
preferred state GJ:

| String | Prior | Passes the 0.12 floor? |
|---|---|---|
| `GJ01AB1234` | 1.0 | yes |
| `MH12AB1234` | 0.7 | yes |
| `DL3CCN5712` | 0.42 | yes |
| `GJ011234` | 0.15 | yes |
| `MH011234` | 0.105 | no |
| `GJ01AB123` | 0.08 | no — valid shape, short number |
| `GJ45AB1234` | 0.10 | no |
| `SS01AB1234` | 0.05 | no |
| `DL20AB1234` | 0.21 | **yes**, despite an out-of-range district |

Full-length strings with an impossible district therefore reach the output if ROVER produces them.
The string vote bars them through `vote_reject`.

**Plate class.** `plate_class` is always `"unknown"` (`pipeline.py:466`), so every sighting's
`plate_format` is "unknown" (`anpr_engine.py:342`). The colour classes (`plate_grammar.py:198-210`) are
unused, and `valid_format` (`pipeline.py:579-580`) is computed but the adapter ignores it.

**central-api check.** The server's only check is `is_plausible`: length 6-11 and a known
state/special prefix (`CA/services/plate_matching.py:177-186`). A string that fails it gets no
sighting, but the text stays on the detection row (`CA/routers/detections.py:274`).

### A16. Output, deduplication and what central-api stores

**The record.** `_finalise_track` builds it (`pipeline.py:461-469, 570-595`) with status CONFIRMED,
CANDIDATE or UNREADABLE and the reason.

**What the adapter emits.** `AnprEngine._settle` (`anpr_engine.py:292-352`) emits each record once. It
skips a record that:
- has no plate;
- is not CONFIRMED, unless `ANPR_EMIT_UNCONFIRMED` is set (default false: 53, 310; `compose:224`);
- scores below `REVIEW_SCORE` 0.35 (57, 314; `compose:227`);
- has a grammar prior below 0.12 (logged at INFO: 320-328).

**`PlateSighting` fields**
- `track_id`: the numeric id;
- `text`, `confidence`;
- `observations`: `frames_fused`, or `n_plate_hits` when that is 0;
- `confirmed`;
- `state`: always None;
- `plate_format`: always "unknown";
- the plate and vehicle boxes;
- `captured_at`: the track's last-seen PTS, not the best frame's (345);
- `frame_index`: the best frame;
- `method`: the reason;
- `quality`.

CANDIDATE and UNREADABLE outcomes are never reported anywhere (A18).

**The ingest row** (`worker.py:460-508`)
- `detection_id = sha1(camera | track | text | int(captured_at))`.
- Every plate row has `class_name "car"` and class id 2, so each plate also adds a car detection. The
  box falls back to `[0,0,1,1]`.
- `timestamp_utc` is the wall-clock moment the pass settled (414).
- Provenance carries `plate_observations`, `plate_confirmed`, `plate_state`, `plate_format`,
  `track_id`, `restoration` and `captured_at_pts`.

**Posting**
- Detections go out in batches of 50 during the pass (787-790). Plates go out only at the end
  (433-436).
- There is no retry (341-348).
- The engine has already cleared `_pending` and marked the plates emitted
  (`anpr_engine.py:289, 330`), so a failed POST loses them. An operator note records exactly this
  after central-api restarts.

**central-api** (a background survey, with the lines below spot-checked)
- Ingest: `POST /api/v1/detections/ingest` (`CA/routers/detections.py:155-372`).
- **Deduplication is by `detection_id` only.** A repeated id refreshes confidence, box, quality and
  latency, but never the plate fields (241-256). The sighting-level check is also by `detection_id`
  (`CA/services/watchlist_service.py:168-183`).
- A plausible plate becomes a `plate_sightings` row (`CA/models.py:685-732`;
  `watchlist_service.py:185-209`). Watchlist matching with confusable-aware distance happens on
  ingest.
- `plate_confirmed` is kept inside the provenance JSON. No code in central-api or the dashboard reads
  it (grep finds nothing).
- Only the track view merges repeated reads of one plate at one camera, within 90 s
  (`CA/services/track_service.py:42, 143-176`). `/sightings`, `/plates/search` and `/reports/anpr`
  do not merge.
- `/plates/{plate}/track` fetches the oldest rows first, capped at `limit × 20` (243).
- Plate retention is 30 days, enforced only when reading (`CA/config.py:731`).

**Dashboard**
- The live page shows sightings with confidence ≥ 0.1 (`DB/app/live/page.tsx:93`).
- Tile chips are coloured at ≥ 0.75 and ≥ 0.35 (`DB/components/LiveTile.tsx:611-615`).
- The plates page flags "single frame" when observations = 1 (`DB/app/plates/page.tsx:366-375`).
- Unconfirmed reads are never shown differently.

**Report script.** `scripts/anpr_report.py` reads `provenance.plate_confirmed` from `/sightings` rows
(155-162, 212-216). `SightingOut` has no provenance field (`CA/schemas.py:1392-1418`), so the
"confirmed" column is always blank or "no".

**One vehicle, several sightings.** The same vehicle can be stored more than once:
- each merged fragment produces its own sighting (A7);
- each loop of a looping grid recording produces another (A3);
- a revised read gets a new id (`worker.py:471-477`).

### A17. Camera configuration

| Source | Scope today | Read by |
|---|---|---|
| `EW/config/thresholds.yaml` | global; every engine loads the same file (`anpr_engine.py:171`) | pipeline |
| `EW/config/bytetrack.yaml` | global | Ultralytics |
| `EW/config/roi.yaml` | per camera id, but only one legacy screen-recording id is configured (`roi.yaml:22-30`); `lanes` is unused | `OverlayMasker` |
| `EW/config/india_codes.yaml` | global | grammar |
| `EW/config/cameras.yaml` | one development file source | nothing in EW |
| `EW/config/profiles.yaml` | LITE/FULL profiles naming models that do not exist (`yolo11n_vehicle.onnx`, `plate_det_n.onnx`, `plate_det_s.pt`, `reader_parseq.onnx`) | nothing in EW |
| `EW/config/plate_kinds.yaml` | synthetic-training generator config for `R/tools/synth_kinds.py`; says GJ district "01-40 excl 28,29", contradicting `india_codes.yaml` (01-39) | nothing in EW |
| `EW/config.yaml` | the previous engine's settings | nothing; not in the image |
| `data/reference/grid_cameras.json` | 30 cameras with site, facing and `camera_type` (fixed 10, PTZ 6, ANPR-capable 5, bullet 1, dome 1, unset 7) | not the edge worker, which builds URLs from ids (`worker.py:200-239`, `grid.py:217-237`) |
| Environment | `ANPR_*` (`anpr_engine.py:53-87`), `EDGE_*` (`worker.py:64-70`), `YOLO_*`, `SENTINEL_GRID_*` (`grid.py:50-55, 106, 212, 336`) | process-wide |

Per-camera state learned at run time lives only in memory, in the engine: the overlay mask, the
static-text map, and the incident flow. It is lost when an engine is evicted or the process restarts.
Nothing resets it after a PTZ move.

`reading.preferred_state: GJ` is global (`thresholds.yaml:61`). It applies even to the Delhi clip
served on the live wall. Hardcoded values are listed in B2.

### A18. Devices, threading, memory, logging

**Devices**
- **Docker:** CPU only. The CPU torch wheel is installed (`EW/Dockerfile:54`), and compose sets no
  `ANPR_DEVICE`, so it resolves to "auto" and then CPU.
- **Host:** `ANPR_DEVICE=mps` moves the YOLO models to the GPU. The ONNX readers and all OpenCV
  enhancement still run on the CPU.
- Half precision is never requested (`vehicle.py:39`, `plate.py:137` store the flag but never pass
  it).

**Threading**
- The worker is single-threaded, and cameras run in sequence.
- The live wall runs one decoder thread per ANPR camera. A single `engine_lock` wraps every
  `engine.process` call, because Metal is not thread-safe (`snapshot_wall.py:355-361, 425-426`). The
  tile detector runs on the CPU under its own lock (184, 317, 765).
- Host reader processes share the GPU with no coordination (operator note: five readers at once).
- Ultralytics' track-id counter is class-global (`BaseTrack._count`), shared by every engine in a
  process. Any engine's `reset()` sets it back to 0.

**Caching and blocking**
- `EngineCache` is an LRU of capacity 4 (`anpr_engine.py:75, 413-476`). With more cameras than that,
  every camera gets a freshly built engine on every cycle.
- Tracks are read synchronously: stale ones inside `process_frame` (`pipeline.py:324-325`), and all
  open ones at pass end. The frame loop stops while a track is read.

**Memory growth**
- Closed banks are never pruned (A10).
- `gate_log` is appended to forever (`pipeline.py:487`).
- `records` is trimmed to 500 only inside `_settle` (`anpr_engine.py:354-361`). The live wall never
  calls it, so there `records`, with their `_hyps`, grow for the whole process lifetime.

**Logging** goes to stdout (`worker.py:54-61`):
- one INFO line per emitted plate (427-431, 761-765);
- one INFO line per grammar-floor drop (`anpr_engine.py:324-327`);
- one line per skipped frame (682-685);
- a `done` summary and a sampler summary (818-829);
- engine messages about overlay-by-motion (`pipeline.py:264`) and missing plate weights
  (`plate.py:128`).

The "readability" and p50/p99 timing lines (`worker.py:830-855`) never print, because `stats()`
returns neither key (`anpr_engine.py:382-391`). No per-track outcome (CANDIDATE or UNREADABLE with
its reason) is logged in production.

### A19. Result storage

- The engine keeps its records in memory, trimmed to the last 500.
- The adapter disables evidence: `evidence_dir=None`, `keep_frames=False` (`anpr_engine.py:204-205`).
  No crop or frame is written anywhere in production, and central-api stores no images
  (`evidence_reference` is never set).
- Persistent results are the central-api `detections` and `plate_sightings` tables (A16).
- Research and offline runs can write more:
  - evidence packs (`EW/anpr/evidence.py:27-66`);
  - a JSON dump (`pipeline.py:852-857`);
  - bank pickles (`pipeline.py:152-156, 455-459`).

### A20. Tests and evaluation tools

**Edge-worker tests (`EW/tests`, 16 files)**
- Adapter and worker plumbing: `test_engine_cache`, `test_finish_pass`, `test_second_reader`,
  `test_grammar_floor` (with injected priors), `test_track_ids`, `test_sampling`.
- Grid capture: `test_grid_capture`, `test_grid_urls`, `test_grid_resolve`, `test_open_capture`,
  `test_snapshot_demand`.
- Imports: `test_vendor_imports`.
- Two engine units: `test_char_evidence`, `test_geometry_prior`.
- Other: `test_incidents_person`, `test_corpus`.

No edge test covers `_read_layout`, `_decide`, the string vote, `_merge_fragments`, ROVER, the beam
search, the overlay mask or the crop bank. Research has tests for these (`R/tests/test_decide_guards`,
`test_grammar_beam`, `test_one_track_per_plate`, `test_static_text`, `test_vote_any_reader`,
`test_text_vote_crops`, `test_overlay_regression`, `test_reading_v2`, `test_enhance`); they were not
vendored.

**Repo tests (`tests/`)**
- `test_anpr_engine.py`: the adapter against a fake pipeline, including emission floors, PTS and
  replay ids.
- `test_watchlist.py`, `test_detections.py`, `test_plate_geography.py`.
- `test_yolo_on_cctv.py`: real YOLO11n, skipped when the weights or clip are missing.

**Tools (`EW/tools`)**
- `annotate_video.py`, `assemble_demo_video.py`: demo rendering.
- Detector-dataset tools: `eval_by_size.py`, `error_analysis.py`, `mine_hard_cases.py`,
  `prepare_dataset.py`, `dataset_report.py`, `train_plate_detector.py`, and `_corpus.py`.
  `eval_by_size.py` assumes a deployed plate `imgsz` of 960 (`eval_by_size.py:73-75`); the engine
  runs at 640.

**Report.** `scripts/anpr_report.py` builds a report from the API.

**Vehicle-level evaluation.** No vehicle-level ANPR accuracy harness is committed. The research repo
has one (`R/eval/*`, untracked there) and the loop log. `EW/tools/anpr_benchmark.py`,
`anpr_gt_sheets.py`, `anpr_gt_reconcile.py` and `anpr_profile_cameras.py` appeared, untracked,
during this audit.

### A21. Drift from the research repo

`diff -rq EW/anpr R/anpr`, with the research working tree as it was at audit time:

| File | Difference |
|---|---|
| `pipeline.py` | **Research has:** `_one_track_per_plate` (R:119-140, used at 319); a post-prior plate floor (`min_conf`); `_vote_pick` with `vote_any_reader` / `vote_unopposed` / `vote_near_backing` / `string_vote_contested` (R:774-852, 911-913); glyphs counted before the vote (R:566-571); the reader credited by `split("/")[1]` (R:733); `text_vote_crops`, a text reader voting on the k best crops (R:736-754); `STAGE_B_MODULES`; a `binarise` import; `eval.metrics._iou`. **Only the edge has:** `plate_search_min_vehicle_px` (EW:165-167, 288); `_iou` from `static_text`. |
| `detect/plate.py` | research adds `min_conf`, applied after the prior (R:122-141, 200-208) |
| `detect/static_text.py` | research adds `min_gap_frames=30`: two sightings within a second are one plate boxed twice, never a sign (R:60-69, 99-100) |
| `detect/overlay_mask.py` | research adds a `warm_up()` helper |
| `detect/rtdetr_vehicle.py` | research adds a `UVH_NAMES` table |
| `enhance/binarize.py`, `enhance/glare.py` | research keeps `sauvola`, `binarise`, `stroke_normalise`, `bloom_mask`; the edge trimmed them |
| `fuse/rover.py`, `plate_grammar.py`, `read/crnn.py`, `read/rows.py` | the edge trimmed unused helpers (`coerce`, `TO_LETTER`, `TO_DIGIT`, `slot_types`, `detect_rows`, `ASPECT_SINGLE_ROW`); no behavioural difference on the paths used |
| `sources/__init__.py` | research exports `FileSource`, `RTSPSource`, `HLSSource`, `open_source` |
| Only in research | `api.py`, `fuse/calibrate.py`, `read/claude_reader.py`, `read/gemini_reader.py`, `sources/{file,rtsp,hls}_source.py`, `sources/grid.py` |
| Only in the edge | `__init__.py`, `sampling.py`, `incidents.py` |
| Identical | `read/awiros.py`, `read/ensemble.py`, `read/beam_grammar.py`, `detect/vehicle.py`, `track/crop_bank.py`, the rest of `enhance/`, `evidence.py` |

**`config/thresholds.yaml`.** Research differs in four places:
- `retro_proposer: false` (R:30);
- `plate_min_conf_after_prior: 0.2` (R:35), both adopted in H16;
- `readers: [crnn, awiros]`, `text_vote_crops: 64`, `awiros_crops: 3` (R:79-84, H17);
- `vote_any_reader: true`, `vote_unopposed: true`, `vote_near_backing: 2` (R:102-109, H17).

The edge alone has `plate_search_min_vehicle_px: 96`. `extra_crnn_weights` is the same v6 file in
both. The other config files are identical.

**Models.** `yolo11s.pt`, `plate_det_mix_n.pt`, `reader_crnn.onnx` and `reader_crnn_v6.onnx` are
byte-identical to `R/models` (sha256). Research also has `reader_crnn_v10.onnx`, the Awiros weights,
`plate_det_grid*`, `plate_det_hf_v1s.pt` and readers v4-v9.

**What the edge worker lacks from the newer research reading logic:**
- The **Awiros PP-OCR voter** (`text_vote_crops` in `_crop_reads`). The edge reader file exists, but
  the reader is not configured, and there are no weights, Paddle or PaddleOCR code under `EW`.
- **`_vote_pick`**, including `vote_any_reader`, `vote_unopposed`, `vote_near_backing` and the
  "rival reading" and "dropped glyph" opposition rules.
- **`_one_track_per_plate`**, plus the `static_text` time gap.
- **The two decision fixes**: glyphs counted before the vote, and the `rows` credit.
- **The detector-side H16 changes**: proposer off, and the 0.2 floor after the prior.
- **Reader v10.** The edge lacks it too, which matches research: research did not adopt v10
  (`LOOP_LOG.md:369`).

**Research-measured effect** (research banks, not re-run here). Replays with `vote_any_reader` and
`vote_unopposed`: Delhi 1080p 9 → 11 of 20 inventory plates, Delhi 4K 8 → 9, cam06 3 → 4 of 5, zero
wrong (`LOOP_LOG.md:343-356`). On banks built like the edge's (proposer on, `before_det`) the same
rules gave Delhi 1080p 8 → 10 and cam06 3 → 3 (`LOOP_LOG.md:350-351`). Adding the Awiros voter gave Delhi 1080p 12/20, Delhi 4K 12/20,
cam06 4/5, night 0, zero wrong (`LOOP_LOG.md:364`, `R/config/thresholds.yaml:79-80`). The research
"cam06" is a sandbox recording, not `data/videos/own/rec_cam06.mp4`.

### A22. Documentation that no longer matches the code

**`docs/anpr-reading.md`**
- Lines 96-100 say `extra_crnn_weights` is "inert here". It is loaded (`anpr_engine.py:186-192`, and
  `test_second_reader`).
- Lines 88-89 say `crnn` is the only reader whose weights ship. v6 ships too.
- Line 30 lists "binarise" as a stage. There is no binarised variant (`pipeline.py:654-656`).
- Line 49 says `candidate_threshold` hides readings. The code never reads that key.
- Line 109 says the adapter passes PTS. The clip path and the live wall pass wall-clock time.

**`docs/anpr.md`**
- §5 describes segmenting and per-slot coercion. The vendored engine only spreads confusion mass
  inside the beam search.
- §4 mentions a "Claude reader" that does not exist under `EW`.
- §10 says the worker logs pass timings, and §9 describes a per-camera readability verdict. Neither
  log line can fire (A18).
- §8 says the frame-quality router skips degraded frames. It only skips whole frames that are almost
  entirely clipped or edgeless.

**`docs/hld.md:239-241`** lists "binarise" as a stage.

**`docs/anpr-dataset.md:84, 153, 176-186`** and **`EW/tools/eval_by_size.py:73-75`** describe a
960 px region-of-interest pass (`roi_imgsz` from the old `config.yaml`). The current plate pass runs at
640.

**`EW/models/PROVENANCE.json`** describes four files from the previous engine (`plate_detector.pt`,
`plate_reader.pt`, `plate_sr.pt`, `plate_detector_frame.pt`). None of them exists, and the file says
nothing about the five models that do.

**`EW/app/grid.py:29-31`** says the capture falls back to HLS. It raises instead (581-590).

**`EW/app/anpr_engine.py:84-86`** says the record tail allows merges across pass boundaries. `_hyps`
are dropped at every flush, so it cannot (A7).

**`compose:218-223`** has a comment arguing to "emit every reading", above a default of `false`.

---

## Part B — findings

### B1. Stage diagram

```
SOURCE     grid RTSP, PTS ........................ EW/app/grid.py:389-540, worker.py:146-171
           clip / brokered file, wall clock ...... worker.py:88-118, 174-194, 383-392
           live wall, wall clock, no resets ...... tools/snapshot_wall.py:375-447
   │
SAMPLE     scan every 20th frame; 24-frame burst when a vehicle is ≥180 px wide
           ...................................... anpr/sampling.py:82-102, worker.py:626-670
           pass = 25 processed frames, then every open track is closed
           ...................................... compose:243-244, worker.py:665, 792-799
   │
QUALITY    skip clipped/edgeless frames; CLAHE the whole frame if mean luma ≤60
           ...................................... app/frame_quality.py:172-292, worker.py:673-686
   │
ADAPTER    AnprEngine.process -> Frame(pts_ms, frame_idx, discontinuity) ... app/anpr_engine.py:240-282
   │
MASK       roi.yaml rectangles + auto OSD mask (after 118 frames) + warm-up full-frame plate pass
           ...................................... anpr/detect/overlay_mask.py, pipeline.py:244-266
   │
VEHICLE    YOLO11s @1920, conf 0.15, COCO car/motorcycle/bus/truck, agnostic NMS
           ...................................... anpr/detect/vehicle.py:55-86, thresholds.yaml:6-13
TRACK      ByteTrack (0.30/0.05/0.20/0.8/45, fuse_score), ids s<session>_t<n>
           ...................................... config/bytetrack.yaml, vehicle.py:73-85
   │  for each confirmed track: skip if >50 % masked; bank.touch; skip if <96 px wide
   │                                              pipeline.py:284-289
PLATE      vehicle box +5 % -> upscale to ≥640 -> YOLO11n plate @640 conf 0.2
           + retro proposer (score ×0.6) -> score × geometry prior -> NMS 0.4 -> top 2
           -> keep 1 per vehicle if ≤15 % masked . anpr/detect/plate.py:160-207, pipeline.py:294-301
CROP       plate box +12 %/+25 % -> static-scene-text check -> corners (Otsu, conf ≥0.2, else box)
           ...................................... pipeline.py:302-320, detect/static_text.py, detect/corners.py
BANK       PlateCrop + CropQuality; rank = q × (0.4+0.6·det); ≤64 per track
           ...................................... track/crop_bank.py, enhance/quality.py
   │  track closes: unseen >45 processed frames | discontinuity | pass end (flush)
   │                                              pipeline.py:246-249, 324-329
GATE       width ≥22 · height ≥8 · lap ≥8 · contrast ≥25; static overlay; static scene text -> UNREADABLE
           ...................................... pipeline.py:435-494
CROP READS every banked crop rectified and read by both CRNNs (squat boxes both ways);
           keep valid registrations with mean p ≥0.5 ...... pipeline.py:505-518, 692-725
RESTORE    top-12 -> rectify 384×92 / 256×128 -> ECC -> weighted median (+ MFSR ×3)
           -> MSR+CLAHE -> NLM denoise -> Wiener deblur -> Hough deskew
           ...................................... pipeline.py:618-646, enhance/*.py
READ       variants fused / enhanced / mfsr / 6 singles -> CRNN primary (split rows) + CRNN v6
           (side-by-side) @64×256 -> grammar CTC beam (beam 16, confusion 0.15, top 3)
           ...................................... pipeline.py:652-680, read/ensemble.py, read/beam_grammar.py
FUSE       ROVER positional vote, T=0.3, overlay reject ... fuse/rover.py, pipeline.py:681-687
           string vote over crop reads: primary first, pooled fallback never confirms
           ...................................... pipeline.py:548-569, 744-774
DECIDE     glyphs ≥3 · vote rule (≥4 crops, share ≥0.4, w ≥40, alt <0.5×) or ROVER rule
           (conf ≥0.75, w ≥40, ≥6 hyps, char ≥0.5, ≥3 frames, ≥2 singles) -> CONFIRMED | CANDIDATE
           ...................................... pipeline.py:776-849, 598-607
FLUSH      retroactive static-text demotion; fragment merge (edit ≤1, IoU 0.3/0.2, 3 s)
           ...................................... pipeline.py:327-432
EMIT       CONFIRMED only (default), conf ≥0.35, grammar prior ≥0.12, once per track record
           ...................................... app/anpr_engine.py:292-352
INGEST     row = class "car" + plate fields, id = sha1(cam|track|text|int(t)); POST at pass end
           ...................................... worker.py:395-437, 460-508
CENTRAL    detections row; plate_sightings row if length 6-11 + known prefix; watchlist alerts
           ...................................... CA/routers/detections.py:155-372, CA/services/watchlist_service.py
DISPLAY    dashboard plates / alerts / live; scripts/anpr_report.py ... DB/app/*, scripts/anpr_report.py
```

### B2. Status lists

#### What currently works

- **Fallbacks.** The engine builds from the shipped weights and config. A missing extra, weight or
  config file turns off plates without stopping vehicle detection (`anpr_engine.py:159-211, 394-410`;
  `tests/test_anpr_engine.py:236-266`).
- **Grid capture follows the integrator guide:** TCP, PTS timing, backoff, join-time tolerance, loop
  discontinuities, a stall budget, and credential redaction (`EW/app/grid.py`; 14 tests in
  `EW/tests/test_grid_capture.py`, and `test_grid_urls.py`).
- **Readings are per track.** A plate is settled per vehicle track and emitted once. Plates are drained
  before the final flush (`EW/tests/test_finish_pass.py`, `tests/test_anpr_engine.py:142-213`).
- **The second reader loads** (`test_second_reader.py`).
- **The emission floors work:** confirmed-only, review score and grammar prior
  (`test_grammar_floor.py`, `tests/test_anpr_engine.py:163-175`).
- **The glyph floor and width knee work:** the windscreen hallucination was removed
  (`test_char_evidence.py`, `test_geometry_prior.py`; `LOOP_LOG.md` H16).
- **Closest research measurement.** The research arm nearest to the edge's current decision code is
  `before_det` plus the glyph floor, with the proposer on, no floor after the prior, and the same
  weights. It scored Delhi 1080p 8 correct / 0 false of 12 legible plates, and the sandbox cam06 3 / 0
  of 5 (`LOOP_LOG.md:299`). That was measured at stride 2 on files, not under the Docker duty
  cycle, and without the edge-only 96 px floor.
- **Mask-derived rejection works.** OSD and hoarding rejection works on footage the engine has warmed
  up on: the static-text guard learned 75 sign positions on Delhi (`LOOP_LOG.md:288`).
- **The engine cache works** for up to 4 cameras (`test_engine_cache.py`).
- **Privacy:** no crops or frames are stored (`anpr_engine.py:204-205`).
- **central-api:**
  - ingest is idempotent by detection id;
  - watchlist alerts are raised;
  - plate disclosures are audited;
  - retention is enforced when reading (tests in `tests/test_watchlist.py`, `tests/test_detections.py`).

#### What is broken

Each item below has evidence.

- **Fallback vote decision ignores glyphs** (A14, defect 1). `pipeline.py:559` omits `glyphs`, so the
  primary's vote always fails the glyph floor there. Whenever the pooled winner differs, it replaces
  the primary's string and can never be confirmed. Fixed in `R/anpr/pipeline.py:566-571`.
- **Per-row two-row reads are credited to `rows`** (A14, defect 2). They are excluded from the
  primary-only vote (`pipeline.py:556, 722`; `ensemble.py:105`). Fixed at `R/anpr/pipeline.py:733`.
- **`UnboundLocalError` when a pass has no usable frame.** `base_provenance` is first assigned inside
  the loop (`worker.py:718`) and used after it (798). A pass whose frames are all skipped, such as a
  blank "no video" picture skipped as blur, or an empty clip, raises. The error escapes `run()`
  (only `DetectorError` is caught, 808), and the pass's plates are not drained.
- **Plates are lost when the final POST fails.** The engine clears `_pending` and marks the plates
  emitted before the POST (`anpr_engine.py:289, 330`; `worker.py:433-436`), and there is no retry
  (341-348).
- **A capture stall skips `_finish_pass`.** `CaptureStalled` is not a `DetectorError`
  (`grid.py:385-386, 489`; `worker.py:808`), so detections not yet batched are dropped.
- **The readability and timing logs never print.** `worker.py:830-855` reads keys that `stats()`
  never returns (`anpr_engine.py:382-391`).
- **The live wall breaks the engine contract.** `snapshot_wall.py` never calls
  `finish()`/`flush()`/`reset()` (375-447), although the adapter's contract requires `finish()`
  (`anpr_engine.py:17-21`). Fragment merging and retroactive sign demotion never run there, records
  are never trimmed, and a looping VMS recording never resets the tracker.
- **Memory grows without bound.** Closed `TrackBank`s are never removed (`crop_bank.py:79-118`), and
  `gate_log` is never trimmed (`pipeline.py:487`).
- **Clip replays do not collapse.** The detection id seeds wall-clock seconds (`worker.py:392, 477`),
  contradicting the promise at 471-475.
- **Fragments from different flushes can never merge** (`pipeline.py:357, 428-432`), contradicting
  `anpr_engine.py:84-86`.
- **`new_track_thresh: 0.20` does nothing.** Ultralytics starts tracks only from detections at or above
  `track_high_thresh` 0.30 (A7), so the value does not do what its comment says
  (`bytetrack.yaml:2-7`). Checked in ultralytics 8.4.147.
- **The report's `confirmed` column is always empty or "no"** (`scripts/anpr_report.py:155-162,
  212-216`; `CA/schemas.py:1392-1418`).
- **Sighting fields `plate_format` and `state` carry no information:** always "unknown" and None
  (`pipeline.py:466`; `anpr_engine.py:338-342`).

#### What is slow

- **Vehicle detection:** YOLO11s at 1920 on 1280×720 frames enlarged 1.5×, in FP32, on the CPU in
  Docker (`thresholds.yaml:13`; `vehicle.py:65-75`; `EW/Dockerfile:54`).
- **Plate detection:** one YOLO call per vehicle per frame, with no batching, on crops enlarged to
  640 px (`pipeline.py:284-290`; `plate.py:173-181`).
- **Per-crop work on every banked candidate:**
  - the retro proposer's HSV and morphology on the enlarged vehicle crop (`plate.py:37-71`);
  - a static-text thumbnail with up to 800 NCC comparisons (`static_text.py:86-98`);
  - Otsu corner estimation (`corners.py`);
  - an FFT blur estimate (`quality.py:63-88`).
- **Reading at track close, for every banked crop** (up to 64):
  - it is rectified and read by 2 CRNNs, and twice when the box is squat (`pipeline.py:703-725`);
  - each read runs a pure-Python CTC prefix beam search over 64 steps × beam 16 × 12 symbols
    (`beam_grammar.py:85-121`).
- **Restoration per group and per layout:**
  - up to 11 ECC registrations, 2 attempts each when cc < 0.3;
  - a second ECC pass plus 8 Richardson-Lucy iterations at 3× for MFSR;
  - NLM denoise and Wiener deblur;
  - a discarded Lanczos 4× upscale;
  - MSR on every single crop;
  - then about 9 variants × 2 readers × beam search, and O(n·m) Python alignment per hypothesis in
    ROVER (`pipeline.py:618-689`; `enhance/fuse.py`; `fuse/rover.py:40-60`).
  - `two_row_both_ways` doubles this for squat plates.
- **Synchronous finalisation:** a track closed mid-pass stalls the frame loop (`pipeline.py:324-325`).
- **Engine rebuilds:** more than 4 cameras means model reloads on every pass, plus the warm-up
  full-frame plate passes (A5, A18).
- **`close_stale` scans every bank ever created**, on every frame (`crop_bank.py:106-112`).
- **The live wall is serialised:** one lock for all ANPR cameras (`snapshot_wall.py:357, 425`).
- **Documented timings:**
  - Delhi 1080p clip (26.8 s): 586 s with the 96 px floor, 878 s without (`docs/hld.md:264-268`);
  - Awiros roughly doubles to quadruples processing time: cam06 451 s vs 232 s, 4K 959 s vs 257 s
    (`thresholds.yaml:68-70`);
  - operator note: one Delhi pass took 13-20 minutes with five host readers sharing the GPU.

#### What causes false positives

These cover false plate strings and false "vehicles".

- **Junk in the banks.** The retro proposer is on and nothing filters after the prior
  (`thresholds.yaml:26`; `plate.py:184-207`). Research measured bank junk falling from 61 % to 21 %
  on Delhi and from 40 % to 14 % on cam06 with both changes (`R/config/thresholds.yaml:26-35`;
  `LOOP_LOG.md:298, 316`).
- **Every read is forced into plate shape.** The grammar-constrained beam and slot-wise ROVER
  assembly can produce strings no reader read (`beam_grammar.py:106-140`; `rover.py:66-68, 97-123`;
  `supported_strings_only` unset). The glyph floor does not stop glyph-rich junk such as hoardings
  (`char_evidence.py:25-33`; `LOOP_LOG.md:263-265`: a hoarding read as TS57SQ8300).
- **A consistent small-plate misread by the primary is confirmed unopposed.** `vote_unopposed` is
  absent; in research, DL11AB3684 was confirmed on a 48-56 px yellow plate (`LOOP_LOG.md:333`).
- **Plates bank into the wrong track.** With no one-plate-one-track rule, overlapping vehicle boxes
  mix plates between tracks (`LOOP_LOG.md:331`).
- **Confusion spreading can pick the wrong glyph.** The GJ preference (× 0.7 for other states, × 0.1
  for a bad GJ district) pushes wrong reads toward valid GJ codes
  (`beam_grammar.py:71-82`; `plate_grammar.py:164-171`). Format checks cannot catch a valid-looking
  misread (`docs/anpr.md` §8).
- **Out-of-range districts pass the adapter floor** in full-length strings (DL20AB1234 scores 0.21
  against a floor of 0.12; A15).
- **OSD and timestamp text can reach the reader** when the auto mask never finalises (A5). This is
  mitigated only by `looks_like_overlay` (`plate_grammar.py:224-240`) and the static-overlay check,
  which needs at least 5 crops (`pipeline.py:477`).
- **Presentation shows weak reads:**
  - the live wall and the annotator show CANDIDATE reads down to 0.10
    (`snapshot_wall.py:433`; `annotate_video.py:82`);
  - the dashboard live page shows sightings ≥ 0.1 (`DB/app/live/page.tsx:93`);
  - `plate_confirmed` is never read, so with `ANPR_EMIT_UNCONFIRMED=true` unconfirmed sightings look
    exactly like confirmed ones (A16);
  - central-api checks only length and prefix (`plate_matching.py:177-186`).
- **Extra "vehicles":**
  - one sighting per merged fragment and per recording loop (A16);
  - a new id for every revised read (`worker.py:471-477`);
  - every plate row also counted as a car detection (`worker.py:483-484`);
  - Traffic cameras processed twice (`compose:194-197`).

#### What causes missed plates

- **Duty cycle** (A4). The Docker worker processes 25 frames per pass at stride 20, runs through at
  least 23 cameras in sequence and sleeps 120 s, so each camera is watched for at most about 3 % of
  wall time. A burst uses up the budget, and tracks are cut at the end of each pass
  (`compose:243-245`; `worker.py:665, 1047-1084`; `sampling.py:66`).
- **Tracks never confirm under sparse sampling** (A7). A new track needs a second association with
  IoU × score ≥ 0.3 against a zero-velocity prediction, and scan frames are 0.8 s apart. A vehicle
  scoring below 0.30 never starts a track. An untracked vehicle is never searched for a plate
  (`pipeline.py:282-290`).
- **Bursts rarely fire on 720p wide views:** they need a vehicle ≥ 180 px wide (`sampling.py:37-42,
  97`).
- **Fixed-pixel floors**, all tuned on 1080p/4K and sandbox clips:
  - no plate search on vehicles under 96 px (`thresholds.yaml:28`);
  - no confirm below 40 px margined width (about 32 px of plate) (`thresholds.yaml:54`);
  - legibility gate at 22 px (`thresholds.yaml:2`);
  - only CONFIRMED readings are emitted by default (`anpr_engine.py:53`; `compose:224`).
- **Evidence floors per track:** at least 4 exact crops, 3 glyphs and a 0.4 vote share for a vote
  confirm; at least 3 frames and 2 agreeing singles for a ROVER confirm (`thresholds.yaml:43, 64-66,
  86-88`). Fragmented tracks rarely reach them, and fragments merge only when their final strings
  are within one edit (`pipeline.py:376-384`).
- **The two decision defects** in A14.
- **Bank contamination** (proposer on, no floor after the prior), which outvotes real plates.
- **The static-text guard demotes real plates** boxed by two overlapping vehicles. There is no time
  gap in the edge copy; in research, DL1LT1087 was demoted despite 45 exact reads
  (`LOOP_LOG.md:331`).
- **The grammar floor drops valid short-number plates** (`GJ01AB123` scores 0.08) and series-less
  plates from other states (`MH011234` scores 0.105) (A15). The overlay filter drops series such as
  REC, PTZ, IPC and CAM0-3 (A15).
- **No auto-rickshaw class** in the COCO vehicle model (`thresholds.yaml:8-12`).
- **Low light:** only whole-frame CLAHE. The crop-level gamma and CLAHE variants are disabled
  (`pipeline.py:676`). The research night clip confirmed nothing in either configuration
  (`LOOP_LOG.md:315`).
- **Plates dropped at the frame edges.** A plate more than 15 % masked is dropped (`pipeline.py:299`),
  and OSD strips mask full-width bands in the top and bottom 18 % (`overlay_mask.py:197-220`).
- **Pass-end fragility:** failed POSTs, stalls, and passes with no usable frame (see "What is broken").

#### What causes bad OCR

- **Tiny, heavily compressed plates.** The `rec_cam*` clips are 1280×720 at 249-1209 kbps, against
  25.7 Mbps for `delhi_1080p` (measured). The CRNN input is 64×256 (ONNX shape), so a plate 20-40 px
  wide is enlarged 6-12× (`crnn.py:63-67`).
- **Known reader confusions.** Research documents the primary's systematic misreads: 9/5, S/B at
  shares of 0.24-0.48, N/M (`LOOP_LOG.md:306, 328, 335`). The stronger readers are not deployed: Awiros is
  off, and v6 may only fill in a read that can never be confirmed.
- **Heuristic corners.** Otsu plus `minAreaRect` with a 0.2 confidence floor
  (`corners.py:41-69`; `pipeline.py:316-320`) can lock onto a bright body panel. The warp then
  includes body or cuts glyphs. This is a risk, not measured.
- **Blurry fusion outvotes sharp crops.** Fused variants keep weight 1.0 even when registration kept
  only 2 of 12 crops (`fused_weight_by_registration` unset, `pipeline.py:647-651`). The code comment
  itself says a blurry fusion outvoted sharp crops on handheld footage.
- **Enhancement not tuned for tiny plates:**
  - NLM denoise at h = 7 on small fused plates (`denoise.py:25`);
  - Wiener deblur driven by an uncalibrated blur-extent estimate (`quality.py:86-87`;
    `deblur.py:59-69`);
  - MSR and CLAHE on every single crop, glare or not (`pipeline.py:672`).
- **Two-row handling depends on box aspect** (below 2.6 counts as two-row, `plate.py:82`). The primary
  splits at an ink valley (`rows.py:18-23`), and its per-row reads are credited to the wrong reader
  (defect 2).
- **Low-light crops** get no photometric variants (`pipeline.py:676`). Whole-frame CLAHE is applied
  before detection on dark cameras (`frame_quality.py:244-259`); its effect on the readers is
  unmeasured.
- **Temperature 0.3 pushes confidence to the extremes.** The 0.75 confirm threshold corresponds to a
  raw score of about 0.59 (`rover.py:192-196`; `thresholds.yaml:45-47`).

#### What is camera-specific

- **Configured per camera:** only the `roi.yaml` rectangles, and no live camera id has any (A5, A17).
- **Learned per camera, in memory only:** the auto OSD mask and strips, static OSD boxes, static-text
  positions, and the incident flow. All are lost on eviction or restart, and none is invalidated
  after a PTZ move. `grid_cameras.json` lists 6 PTZ cameras.
- **Seen in the evaluation clips** (measured):
  - `rec_cam09`: median luma 10, 249 kbps;
  - `rec_cam07`: median luma 41, 499 kbps;
  - `rec_cam15`: 399 kbps, Laplacian median 98;
  - `rec_cam12`: Laplacian median 2212.

  The frame router enhances `rec_cam07` and `rec_cam09` on every frame, and never skips any clip.
- **Available but unused:** `camera_type` (fixed, PTZ, ANPR-capable), facing, and site in
  `grid_cameras.json`. ANPR-capable cameras get exactly the same thresholds as wide overview views.
- **Global when it should be per camera:** `preferred_state` (GJ), even for the Delhi feed on the live
  wall (`thresholds.yaml:61`); every threshold in `thresholds.yaml`, `bytetrack.yaml`, the sampler and
  the router.

#### What is hardcoded

These are numeric constants in Python that decide behaviour and are not configurable.

| File | Constants |
|---|---|
| `EW/anpr/pipeline.py` | frame cache 30 (163); RT-DETR conf ≥ 0.3, imgsz 640 (172); warm-up detector ≤ 12 frames, every 3rd, score ≥ 0.15 (255-258); vehicle mask share 0.5 (285); `plates_per_vehicle` default 2 (294); crop margin 0.12 w / 0.25 h (304); corner confidence 0.2 (317); merge edit ≤ 1, gap 3000 ms, IoU 0.3 / 0.2 (376-384); static overlay ≥ 5 crops, < 3 px, > 30 px (477-481); read-filter minimum 3 (517); cluster minimum 3 (530); ablation resize 384×92 / 256×128 (624); weight floor 0.05 (628); fused weight 0.3 + 0.7·ratio (651); mfsr 0.9, sr 0.8 (658, 662); photometric CLAHE 2.0 (2,8), gamma 0.6 / 1.6 at mean < 90 / > 170, sharpen 1.6 / −0.6 σ 1.2 (88-96); `_top_valid_texts` k = 3, length ≥ 8 (100-113); vote length ≥ 8 (697); vote needs ≥ 2 crops (761); vote strength formula (768); `_decide` fallback defaults 40 px, 6 hypotheses, 0.5, 4 crops, 0.4 (814-843) |
| `EW/anpr/detect/vehicle.py` | COCO class map (16); class-agnostic NMS True (30); tracker config path (29); session id format (84) |
| `EW/anpr/detect/plate.py` | vehicle margin 5 %, minimum crop 8 px (165-169); retro rules: rel > 25, V > 110, S < 110, yellow V > 120, S > 80, H 15-40, kernels 9×3 / 3×3, w ≥ 10, h ≥ 4, w ≤ 0.9 W, fill ≥ 0.45, score weights 0.4 / 0.3 / 0.3, texture / 200, aspect / 4 (45-69); retro × 0.6 (186); minimum box 8×3 (192); prior floors 0.1 / 0.2 / 0.05, motorcycle band 0.05-0.40, top-25 % × 0.5, exponent 6 (84-112); NMS 0.4 (204); top 2 (207); tile merge 0.5 (150); log cap 5000 / 2500 (198-199) |
| `EW/anpr/plate_grammar.py` | two-row aspect (1.2, 2.6), any aspect (1.0, 6.0), height fraction (0.04, 0.25), width fraction 0.60 (213-221); format priors (74-99); penalties 0.05 / 0.7 / 0.1 / 0.3 / 0.05 / 0.35 / 0.2 and Bharat years 19-35 (160-186); forbidden substrings and overlay regex (224-240) |
| `EW/anpr/detect/corners.py` | minimum 6×12, upscale to 120, area ≥ 0.15, aspect 1.2-6.5, Otsu, kernel 7×3 (32-56) |
| `EW/anpr/enhance/quality.py` | 128 px normalisation width; weights 0.30 / 0.25 / 0.15 / 0.10 / 0.05 / 0.10 / 0.05; scales 12-90 px, lap / 300, contrast / 45, bloom × 4, dark × 2, extent / 12, skew / 30; h < 8 → × 0.3; bloom 250, dark 20; extent formula (55-135) |
| `EW/anpr/track/crop_bank.py` | rank 0.4 + 0.6·conf (61); centres cap 256 (95) |
| `EW/anpr/enhance/rectify.py` | canvases 384×92, 256×128; deskew ≤ 12°, Canny 60/160, Hough parameters (8-51) |
| `EW/anpr/enhance/fuse.py`, `register.py` | min cc 0.25, bloom 0.35, trim 0.2, ≥ 5 crops for trimmed mean / ≥ 3 for median, RL σ = 0.6·scale × 8 iterations, ECC 80 iterations / 1e-5 / 2×, translation retry below cc 0.3 (`fuse.py:54-153`; `register.py:30-92`) |
| `EW/anpr/enhance/glare.py`, `denoise.py`, `deblur.py`, `sr.py` | MSR σ (5, 15, 40), CLAHE 2.0 (4,2), band 0.12-0.88; NLM h = 7, 7/21; deblur only when extent ≥ 1.5, Wiener K 0.01; SR scale 4 and model path |
| `EW/anpr/read/ensemble.py`, `beam_grammar.py`, `rows.py`, `crnn.py` | beam 16, top 3, row weight × 1.2, rank weight 0.4 / rank; confusion mass 0.15, 12 symbols per step, prior floor 1e-4; row overlap 0.06, cut band 0.38-0.62, gap 0.25; default input 32×128 (overridden by the ONNX shape) |
| `EW/anpr/fuse/rover.py` | SR penalty 0.6, top 3, `n_lengths` 1, agreement within 1 edit, alternates within 2 edits, prior^0.3, 0.5 + 0.5·prior |
| `EW/anpr/detect/char_evidence.py` | margins derived from 0.12 / 0.25; normalised height 48; CLAHE 2.0 (8,8); block H//3, C = 8; glyph bounds 0.22-0.95 H, 0.03-0.25 W, aspect 0.8-6.0, fill ≥ 0.12 (40-82) |
| `EW/anpr/detect/static_text.py` | thumbnail 96×32; IoU 0.6, vehicle IoU 0.7, NCC 0.85, history 800, minimum 20×8 (25, 58-59). A `static_text` config section would override these, but none exists |
| `EW/anpr/detect/overlay_mask.py` | every 3rd frame; Canny 80/160; kernels (25,9) and (31,15); blob ≥ 40×8 with area ≥ 200; static share 0.9; OSD glyph height ≤ 8 %, w ≥ 6, area ≥ 20, fill ≤ 0.65, edge ≥ 0.08; dilation 2.5 glyph heights; band 18 %; ≥ 6 glyphs, ≥ 12 % width, ≤ 8 % height, margin 4 px; static boxes 0.6 / 0.8 / pad 6 / 18 % (110-273) |
| `EW/anpr/sampling.py` | plate fraction 0.25, burst threshold 45 px, burst 24 frames, default stride 20 (37-66) |
| `EW/app/frame_quality.py` | 60 / 200 / 0.18 / 40 / 12, clip level 250, skip at 0.85, CLAHE 2.0 (8,8) (88-92, 129, 199, 257) |
| `EW/app/grid.py` | backoff 2 → 30 s, 25 grace failures, loop 5000 / 1500 ms (398-414); probe 5 s (565). Socket 10 s and handshake 3 s are environment-overridable |
| `EW/app/worker.py` | CLI defaults: max-frames 40, cycle 60 s (1117-1121); sign-in backoff ≤ 60 s (929); plate row class "car" / 2 (483-484); a literal default password in the source (66; value not reproduced here) |
| `EW/tools/snapshot_wall.py` | display floor 0.10 (433); label hold 4 s (105); publish width 1280 (438); late-frame drop 0.2 s (420); a default VMS API key in the source (770, 772) |

**Configuration with no effect**
- `thresholds.yaml`: `tracker.track_high_thresh`, `track_low_thresh`, `new_track_thresh`,
  `match_thresh` (30-35); `confidence.candidate_threshold` (46); `detector.aspect_soft_band`,
  `plate_h_frac_of_vehicle` (19-24).
- `bytetrack.yaml`: `new_track_thresh`, which sits below `track_high_thresh`.
- `roi.yaml`: `lanes`.
- Whole files: `cameras.yaml`, `profiles.yaml`, `plate_kinds.yaml`, `EW/config.yaml`,
  `models/PROVENANCE.json`.

### B3. Models and thresholds

#### Models

| File in `EW/models` | Size | Network | Training (checkpoint) | Role | Loaded at | Device |
|---|---|---|---|---|---|---|
| `yolo11s.pt` | 19.3 MB; 9.46 M params, FP16 checkpoint | YOLO11s, COCO 80 classes (4 used) | Ultralytics release, imgsz 640 | ANPR vehicle detector and tracker | `vehicle.py:33` through `pipeline.py:174`; file name from `ANPR_VEHICLE_WEIGHTS` (`anpr_engine.py:80`) | `ANPR_DEVICE`; "auto" = CUDA else CPU (`vehicle.py:89-96`); CPU in Docker, MPS on the host when set |
| `plate_det_mix_n.pt` | 21.2 MB; 2.59 M params (the file includes optimizer state) | YOLO11n, 1 class `plate` | imgsz 640, 6 epochs, `data\det\mix`, 2026-09-10 | plate detector in vehicle ROIs; full-frame during mask warm-up | `plate.py:124-126` through `pipeline.py:176`; `ANPR_PLATE_WEIGHTS` (`anpr_engine.py:81`) | same as above |
| `reader_crnn.onnx` | 7.7 MB | CRNN-CTC; input `[B,1,64,256]`, output `[B,64,37]`; no `two_row_mode` (so split) | not recorded under `EW` | primary reader `crnn` | `crnn.py:85-94` through `pipeline.py:194-202`; `ANPR_READER_WEIGHTS` (`anpr_engine.py:82`) | ONNX Runtime, CUDA else CPU (`denoise.py:28-38`); CPU on the Mac |
| `reader_crnn_v6.onnx` | 7.7 MB | same I/O; `two_row_mode=side_by_side` | not recorded under `EW` | fallback reader `crnn_reader_crnn_v6`; its reads are shown, never confirmed | `anpr_engine.py:190-192` → `pipeline.py:199-201` | as above |
| `yolo11n.pt` | 5.6 MB; 2.62 M params | YOLO11n, COCO | Ultralytics release | not used by the Docker ANPR engine. It is the plain detector's model (`detectors.py:243`, `YOLO_MODEL_NAME`) and, per operator notes, the host live wall's `ANPR_VEHICLE_WEIGHTS` | `detectors.py:317` | CUDA, MPS or CPU (`detectors.py:271-283`) |

**Referenced in code but absent:**
- `models/plate_sr_x4.onnx` (`sr.py:29`);
- `models/reader_parseq.onnx` (`parseq.py:22`);
- `models/awiros/*` and `third_party/PaddleOCR` (`awiros.py:51-53`);
- the UVH-26 RT-DETR weights (`rtdetr_vehicle.py:5-7`);
- denoise and deblur ONNX models, which are constructed without paths (`pipeline.py:217-218`);
- the models named in `profiles.yaml`.

`PROVENANCE.json` describes none of the present files (A22). The four ANPR weights are byte-identical
to the research copies (A21).

#### Thresholds that gate a vehicle, a plate candidate, a read or a confirm

| Value | Location | What it gates |
|---|---|---|
| **Frames** | | |
| mean luma ≤ 60 | `frame_quality.py:88, 209` | LOW_LIGHT: whole-frame CLAHE before detection |
| luma ≥ 200 or ≥ 18 % clipped | `frame_quality.py:89-90, 191` | OVEREXPOSED flag |
| clipped ≥ 0.85 | `frame_quality.py:199` | frame skipped |
| Laplacian < 40 / < 12 | `frame_quality.py:91-92, 225, 231` | BLURRED flag / frame skipped |
| stride 20 (compose), 5 (environment default) | `compose:244`; `worker.py:67` | scan frames |
| 25 (compose) / 40 (CLI) processed frames | `compose:243`; `worker.py:1121` | pass length; track closure |
| 500 delivered frames (max × stride) | `worker.py:648` | grid pass window |
| vehicle ≥ 180 px (0.25 × w ≥ 45) | `sampling.py:37, 42, 97` | burst trigger |
| 24 frames | `sampling.py:66` | burst length |
| 90 s | `worker.py:69` | capture stall |
| **Masks** | | |
| 40 stored frames, every 3rd | `roi.yaml:7`; `overlay_mask.py:113` | auto mask exists after 118 frames |
| std 0.35, edge 0.08, dilate 6, max 12 %, bright 150 / 115, range 90 | `roi.yaml:8-15` | auto mask |
| vehicle > 50 % masked | `pipeline.py:285` | vehicle ignored |
| plate > 15 % masked | `thresholds.yaml:25`; `pipeline.py:299` | candidate dropped |
| **Vehicles** | | |
| conf 0.15 | `thresholds.yaml:7` | vehicle detection |
| imgsz 1920 | `thresholds.yaml:13` | detector input |
| classes 2, 3, 5, 7 | `thresholds.yaml:8-12` | vehicle types |
| NMS IoU 0.7 (library default), class-agnostic | `vehicle.py:30, 73-75` | duplicate boxes |
| high 0.30 / low 0.05 / new 0.20 (inert) / match 0.8 / fuse_score | `bytetrack.yaml:5-10` | track start and association |
| second association IoU ≥ 0.5; confirmation IoU × score ≥ 0.3 | Ultralytics `byte_tracker.py:431, 460` | low-score matches; track output |
| buffer 45 | `bytetrack.yaml:8`; `thresholds.yaml:34` → `pipeline.py:324` | lost track removed; bank closed |
| **Plate candidates** | | |
| vehicle width ≥ 96 px | `thresholds.yaml:28`; `pipeline.py:288` | plate search runs |
| ROI long side ≥ 640 | `thresholds.yaml:16`; `plate.py:174` | ROI upscale |
| imgsz 640, conf 0.2, NMS 0.7 (default) | `thresholds.yaml:14-15`; `plate.py:154` | CNN proposals |
| retro rules and × 0.6 | `plate.py:45-69, 186` | retro proposals |
| w ≥ 8, h ≥ 3 px | `plate.py:192` | candidate kept |
| geometry prior (aspect 1-6, height 0.04-0.25, top 25 %, width > 0.60) | `plate.py:74-113`; `plate_grammar.py:213-221` | score multiplier (no floor) |
| box aspect < 2.6 | `plate.py:82` | two-row layout |
| NMS 0.4; top 2; bank 1 per vehicle | `plate.py:204, 207`; `thresholds.yaml:27` | candidates banked |
| IoU 0.6, NCC 0.85, vehicle IoU < 0.7, box ≥ 20×8 | `static_text.py:58-99` | static scene text |
| corner confidence 0.2 | `pipeline.py:317` | Otsu quad vs box corners |
| bank 64 | `thresholds.yaml:38` | crops kept per track |
| **Reads** | | |
| width ≥ 22, height ≥ 8, Laplacian ≥ 8, contrast ≥ 25 | `thresholds.yaml:1-5` | legibility gate → UNREADABLE |
| ≥ 5 crops, plate < 3 px, vehicle > 30 px | `pipeline.py:477-481` | static overlay → UNREADABLE |
| top k 12; singles 6 at 0.5 × q | `thresholds.yaml:37, 63`; `pipeline.py:665` | fusion input; single-crop votes |
| ≥ min(3, n) readable crops | `pipeline.py:517` | read-filter pool |
| cc ≥ 0.25, bloom ≤ 0.35; retry below cc 0.3 | `fuse.py:55-56`; `register.py:88` | crop used in fusion |
| ≥ 3 kept crops | `fuse.py:82, 90` | weighted median; MFSR |
| blur extent ≥ 1.5 | `deblur.py:64` | deblur runs |
| mean char p ≥ 0.5; length ≥ 8; known state; `vote_reject` | `pipeline.py:692-719`; `thresholds.yaml:100` | crop read may vote |
| ≥ 2 distinct crops | `pipeline.py:761` | string vote yields a read |
| beam 16, 12 symbols, top 3, confusion 0.15 | `ensemble.py:43`; `beam_grammar.py:65-88` | decoded strings |
| template prefix pruning; valid format required | `beam_grammar.py:40-52, 128-132` | decoded strings |
| agreement within 1 edit; alternates within 2 | `rover.py:155, 183` | ROVER agreement / alternates |
| T = 0.3; SR penalty 0.6 | `thresholds.yaml:47`; `rover.py:127` | calibrated confidence |
| overlay regex / forbidden substrings | `plate_grammar.py:224-240`; `pipeline.py:685` | read blanked |
| **Confirms** | | |
| glyphs ≥ 3 in the 8 best crops | `thresholds.yaml:86-87`; `pipeline.py:799-801` | any confirm |
| ≥ 4 exact crops, share ≥ 0.4 | `thresholds.yaml:88`; `pipeline.py:814-815` | vote confirm |
| best margined width ≥ 40 | `thresholds.yaml:54`; `pipeline.py:816, 834` | any confirm |
| runner-up < 0.5 × winner | `thresholds.yaml:65`; `pipeline.py:817, 840` | any confirm |
| confidence ≥ 0.75; ≥ 6 hypotheses; weakest char ≥ 0.5; ≥ 3 frames; ≥ 2 agreeing singles | `thresholds.yaml:43-55, 64-66`; `pipeline.py:832-845` | ROVER confirm |
| `string_vote_secondary` never confirms (`secondary_confirm_min_glyphs` unset) | `pipeline.py:802-808` | fallback reader |
| merge: within 1 edit and IoU ≥ 0.3 (overlapping) or ≥ 0.2 (≤ 3 s apart) | `pipeline.py:376-384` | fragments re-decided together |
| `candidate_threshold` 0.35 | `thresholds.yaml:46` | nothing (never read) |
| **Emission and display** | | |
| CONFIRMED only | `anpr_engine.py:53, 310`; `compose:224` | emitted |
| confidence ≥ 0.35 | `anpr_engine.py:57, 314`; `compose:227` | emitted |
| grammar prior ≥ 0.12 | `anpr_engine.py:66, 320` | emitted |
| last 500 records | `anpr_engine.py:87` | retained |
| length 6-11 and known prefix | `CA/services/plate_matching.py:177-186` | sighting stored |
| 0.10 | `snapshot_wall.py:433`; `annotate_video.py:82`; `DB/app/live/page.tsx:93` | shown |

### B4. Current vs target design

| Target stage | State | Evidence |
|---|---|---|
| Frame sampling | PARTIAL | `AdaptiveSampler`: stride plus a burst on vehicle width (`sampling.py`). Driven by a global per-pass budget, not per-track demand. Deployed at stride 20 / 25 frames, ending passes mid-track (A4). Clip path runs on wall-clock time (A3). |
| Vehicle detection | EXISTS | YOLO11s, COCO 4 classes, @1920, conf 0.15, agnostic NMS, masked input (A6). No Indian classes; the RT-DETR/UVH-26 option is not wired. |
| Tracking | PARTIAL | ByteTrack with resets on discontinuity and every pass (A7). No re-identification. Irregular timesteps. A track needs two associated processed frames to exist. Fragments are joined only after reading, by plate string. |
| Vehicle ID | PARTIAL | `s<session>_t<n>` per engine (`vehicle.py:84`). The numeric id sent upstream repeats (`anpr_engine.py:120-134`). No stable id across passes or cameras; central-api tracks by plate (`CA/services/track_service.py`). |
| Plate detection inside the vehicle ROI | EXISTS | Vehicle crop + 5 %, upscale ≥ 640, YOLO11n plate @640, retro proposer, geometry prior, NMS, cap (A8). No batching, no floor after the prior, no one-plate-one-track rule. |
| Candidate buffer with metadata | PARTIAL | `PlateCrop` + `CropQuality` (A10). **Present:** frame index, PTS (wall clock on clips), box (so w, h, area and aspect can be derived; not stored), post-prior detection confidence, Laplacian and Tenengrad sharpness, blur extent and angle, local contrast, bloom and dark fractions, skew (0 when box corners are used), two-row flag. **Missing:** mean brightness, raw detector confidence, proposer source, stored per-crop OCR result and confidence (computed in `_crop_reads` and dropped), preprocessing method. |
| Quality scoring | EXISTS | Fixed weighted formula (`quality.py:121-134`). Not fitted to OCR success; the blur term is uncalibrated (86-87). |
| Best-frame selection (5-20 per track) | PARTIAL | Top 12 by rank for fusion, 6 singles, and every crop read for the vote (A10). No temporal diversity and no minimum count. Eviction by quality, not rank. |
| Tiny-plate deferral | PARTIAL | 96 px vehicle floor (`pipeline.py:288`), burst on large vehicles, legibility gate at close. No size bins. No "wait for a larger plate" per track: tracks are force-closed at pass end. |
| Perspective correction | PARTIAL | Otsu / `minAreaRect` corners or the box, perspective warp to a fixed canvas, Hough deskew (A9). No learned corner model. |
| Enhancement | EXISTS | ECC registration, weighted-median fusion, MFSR, MSR+CLAHE glare, NLM denoise, Wiener deblur (A12). Photometric variants are coded but off; no binarised variant. |
| Low-light branch | PARTIAL | Whole-frame CLAHE when luma ≤ 60 (`frame_quality.py:209, 274-288`). No crop-level branch (gamma coded but off), no night thresholds or models. |
| Selective SR | PARTIAL | MFSR on every track with ≥ 3 registered crops, regardless of size (`fuse.py:89-91`). No learned single-image SR (`sr.py`). |
| OCR ensemble | PARTIAL | Two CRNNs, primary plus a never-confirming fallback (A13). Awiros coded but off and not installed; PARSeq wrapper without weights; research `_vote_pick` and the Awiros voter missing (A21). |
| Temporal fusion | EXISTS | Per-crop string vote, image fusion, ROVER, fragment merge (A14, A7). |
| Character-level weighted voting | PARTIAL | ROVER positional weighted vote with per-character shares and a weakest-character floor (`rover.py:63-123`; `pipeline.py:824, 836`). The adopted string-vote path decides at string level, and its per-character confidence is just the vote share (`pipeline.py:773`). |
| Indian validation with confusion handling | PARTIAL | Templates, state codes, GJ RTO list, district ranges, priors, overlay rules (A15). Confusions handled only by probability mass inside the beam; no deterministic slot coercion; the floor passes out-of-range districts in full strings; the server check is weaker. |
| Per-camera configuration | MISSING | Only `roi.yaml` rectangles (none for live ids). Thresholds, tracker, sampler, router and preferred state are global; `cameras.yaml` and `profiles.yaml` are unused (A17). |
| Per-vehicle logging | PARTIAL | In-memory records, `gate_log` and `rejection_log` (dumped only in research, `pipeline.py:852-857`). Production logs one line per emitted plate; CANDIDATE and UNREADABLE outcomes and reasons are not logged; the readability summary is dead (A18). |
| Debug video | PARTIAL | `tools/annotate_video.py` (tracks, plate boxes, final reads) and the live-wall overlay. No per-candidate quality or OCR overlay. `evidence.py` exists but is disabled in the adapter (`anpr_engine.py:204`). |

### B5. Ranked causes

Ranking is by likely impact on 1280×720 grid footage (`rec_cam*`, and the live grid under the Docker
worker). **Confidence:**
- **High:** the consequence follows directly from code or config.
- **Medium:** the mechanism is clear but its size is unmeasured.
- **Low:** plausible, not verified.

#### Lost "unique vehicles with a correctly read plate"

1. **The deployed duty cycle never looks long enough.**
   - 25 processed frames per pass at stride 20; at least 23 cameras in sequence; 120 s sleep. That is
     at most about 3 % of each camera's time.
   - A 24-frame burst consumes the pass.
   - Every track is closed at the end of the pass.
   - Evidence: `compose:241-245`; `worker.py:648, 665, 1047-1084`; `sampling.py:66`;
     `anpr_engine.py:286-290`. **High.**
2. **Vehicles never become tracks under sparse sampling, so their plates are never searched.**
   - An output track needs a second association with IoU × score ≥ 0.3 against a zero-velocity
     prediction; scan frames are 0.8 s apart.
   - Detections scoring below 0.30 never start a track.
   - Bursts need a vehicle ≥ 180 px wide.
   - Evidence: A7; `pipeline.py:282-290`; `sampling.py:97`. Mechanism **High**, size **Medium**.
3. **Fixed pixel floors set on 1080p/4K footage.**
   - Plate search needs a vehicle ≥ 96 px.
   - A confirm needs ≥ 40 px margined width (about 32 px of plate), ≥ 4 exact crops and ≥ 3 glyphs.
   - Only CONFIRMED is emitted by default.
   - On wide 720p views most plates are likely below these floors, so they end as CANDIDATE or
     UNREADABLE and are dropped silently.
   - Evidence: `thresholds.yaml:28, 54, 86-88`; `anpr_engine.py:53, 310`; `compose:224`;
     `docs/hld.md:264-268`. Mechanism **High**. Size **Medium**: the plate-width distribution of
     `rec_cam*` has not been measured yet.
4. **Two decision defects.**
   - Glyphs are passed as 0 into the fallback decision.
   - Per-row two-row reads are credited to `rows`.
   - Both are fixed in research, where the fix bundle, together with `vote_any_reader` and
     `vote_unopposed`, moved Delhi 1080p 9 → 11 of 20 and cam06 3 → 4 of 5 with no wrong reads. On
     banks built like the edge's (proposer on) the same rules gave Delhi 1080p 8 → 10 of 20.
     The individual share of each defect is not isolated.
   - Evidence: A14; `LOOP_LOG.md:340-356`. **High.**
5. **Contaminated and mixed banks.**
   - The retro proposer is on and there is no floor after the prior (bank junk 61 → 21 % on Delhi in
     research).
   - There is no one-plate-one-track rule, and the static-text guard has no time gap. A real plate was
     demoted despite 45 exact reads.
   - Evidence: A8, A21; `LOOP_LOG.md:298, 316, 331`. **High** in research footage, **Medium** here.
6. **An OCR ceiling on tiny, low-bitrate plates.**
   - 249-1209 kbps 720p, against 25.7 Mbps for Delhi.
   - A 64×256 CRNN with documented systematic confusions.
   - The stronger text-reader voter and the research voting rules are not deployed, and v6 can never
     confirm.
   - Evidence: A13, A21; measured bitrates. **Medium.**
7. **Low light** (`rec_cam07` luma 41, `rec_cam09` luma 10).
   - Only whole-frame CLAHE; crop-level photometric variants are off.
   - The research night clip confirmed nothing.
   - Evidence: A4, A12; `LOOP_LOG.md:315`. **Medium.**
8. **Engine cache thrash.**
   - At least 23 cameras rotate through 4 engines, so models reload every pass.
   - The auto OSD mask (118 frames) never finalises in 25-frame passes, and per-camera learning is lost
     every cycle.
   - Evidence: A5, A18; `anpr_engine.py:75, 421-423`. **Medium.**
9. **Vehicle detector mismatch.**
   - COCO classes only (no auto-rickshaw).
   - YOLO11s at 1920 on enlarged 720p frames, three times its training size.
   - Evidence: A6. **Low-Medium**, unmeasured.
10. **Pass-end fragility.** Plates are lost on a failed POST, stalls drop the pass, and a pass with no
    usable frame crashes. Evidence: B2 "What is broken"; the operator note on API restarts.
    **Medium.**

#### False reads

1. **Junk candidates turned into plausible registrations.** Retro proposals and boxes the prior
   demoted still reach the bank, and grammar-forced decoding turns glyph-rich junk (hoardings) into
   valid-looking strings, which the glyph floor cannot stop. Evidence: A8, A15; `LOOP_LOG.md:263-265`.
   **High.**
2. **Strings no reader produced.** Slot-wise ROVER assembly and grammar pruning build them
   (`rover.py:66-68`), because `supported_strings_only` is off. **Medium.**
3. **A consistent small-plate misread confirmed without opposition.** `vote_unopposed` and
   `_vote_pick` are absent; research's DL11AB3684 on a 48-56 px yellow plate is the example.
   Evidence: A14, A21; `LOOP_LOG.md:333`. **High** as a mechanism.
4. **Mixed banks.** With no one-plate-one-track rule, a track votes on several plates. Evidence: A8;
   `LOOP_LOG.md:331`. **Medium.**
5. **Confusable characters under mass-spreading decoding.** The GJ preference and GJ RTO penalties
   steer errors toward valid GJ strings, and format checks cannot catch a valid-looking misread.
   Evidence: A15. **Medium.**
6. **OSD and timestamp text** when the auto mask has not finalised (cache thrash). It is caught only by
   `looks_like_overlay` and a static-overlay check that needs at least 5 crops. Evidence: A5, A11.
   **Low-Medium.**
7. **Out-of-range districts pass the 0.12 floor** in full-length ROVER reads (DL20AB1234 scores 0.21).
   Evidence: A15. **Low-Medium.**
8. **Presentation and storage amplify weak reads.**
   - CANDIDATE reads down to 0.10 are drawn on the live wall and in demo videos.
   - The dashboard live page shows sightings ≥ 0.1.
   - `plate_confirmed` is never shown, so with `ANPR_EMIT_UNCONFIRMED=true` unconfirmed reads look
     confirmed.
   - The server check is only length and prefix, and a rejected text stays on the detection row.

   Evidence: A16. **High** as a mechanism; the effect depends on `.env`.
9. **Inflated vehicle counts rather than wrong strings.**
   - One sighting per merged fragment and per recording loop.
   - A new sighting for every revised read.
   - Every plate also counted as a car.
   - Traffic cameras processed twice.

   Evidence: A16, A1. **High.**
