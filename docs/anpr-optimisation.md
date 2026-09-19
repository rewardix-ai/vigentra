# ANPR optimisation: what changed, and what it is worth

Baseline, method and camera inventory: `docs/anpr-baseline.md`. What the system looked like before any change: `docs/anpr-audit.md`.

Every number here comes from `services/edge-worker/tools/anpr_benchmark.py` over the recorded clips, scored against `data/eval/anpr_gt/` (by-eye ground truth, used only for scoring). Reading-stage changes are compared by replaying one run's saved plate crops through the new code (`tools/anpr_replay.py`), so the detector, tracker and banks are identical between the two sides of the comparison.

**The measure:** unique vehicles whose plate the system reads correctly, and how many plates it gets wrong.

## Result

Over all 38 recorded clips (15 grid cameras, the Delhi clip, the 1080p cam06 recording, the noon and
night cam06 recordings, 19 TfL clips), run whole on 19 Sep with the final configuration (`final2`):

| | Before (`baseline`, 36 clips) | After (`final2`, same 36 clips) | After, all 38 clips |
|---|---|---|---|
| Correct plates, of those readable by eye | 9 of 26 | **16 of 26** | **36 of 48** |
| False plates confirmed or sent | 0 | **0** | **0** |
| Vehicle tracks | 1 524 | **1 868** | 2 100 |
| Plate candidates banked | 22 279 | **3 448** | 7 817 |
| OCR images | 36 727 | **14 915** | 20 087 |
| Processing time (Apple M1, MPS) | 5 784 s | 3 341 s* | 4 030 s |

\* The `final` run of the same configuration minus change 8 took 2 779 s on the same 36 clips; the
19 Sep machine was slower, not the pipeline: every count above — tracks, candidates, OCR images,
plates — is identical between `final` and `final2` on those 36 clips.

Per clip: Delhi 7 → **13** of 20; cam06 1080p 2 → **3** of 5 (a fourth reading matches a plate only
partly legible by eye, so it is counted neither right nor wrong); **cam06 at noon 15 of 16**; cam06 at
night 5 of 6; cam07 0 of 1 — its one readable plate is still never detected, though the clip now yields
10 vehicle tracks instead of 2. Every other camera has no readable plate and confirms none, before and
after. The per-camera table, the missed plates and the width bands are in
`services/edge-worker/reports/anpr_benchmark/final2/REPORT.md`, the failure attribution in `FAILURES.md`
beside it.

Without the optional whole-crop text reader (the Docker image's configuration) the same run reads 13.

Sightings: 18 for 16 plates, against 15 sightings for 9 before — one vehicle is no longer reported
several times.

**Regression proof.** Change 8 was replayed first (reading stage only), then the whole set was run
again from the video. On the 36 clips that `final` covered, `final2` confirms the same 16 plates and
the same 0 false, banks the same 3 448 candidates and reads the same 14 915 images. Change 8 gains one
plate on the noon clip and moves nothing else. The 124 edge-worker tests pass.

One thing the rerun caught: the whole-clip benchmark had begun pacing the sampler like the live worker
(change at camera speed, below), so a slow day on the benchmark machine thinned the frames and lost a
plate that the same pipeline reads when it sees every frame. Pacing now applies only under
`--realtime`: a whole-clip run measures what the pipeline can read, a paced run what one machine keeps
up with, and the two are reported separately.

## Changes, in the order they were measured

### 1. Decision rules ported from the research repo (+4 plates)

`anpr/pipeline.py`. The primary reader's string vote decides; another reader's own vote may confirm only when a second reader produced that string and no reader opposes it; a primary vote another reader contradicts is never confirmed. Two defects were fixed with it: the second-reader path never received the glyph count (so it could not pass the glyph floor), and two-row reads were credited to a reader named "rows" instead of to the reader that made them. One physical plate is banked into one track per frame, a sign must be seen by different vehicles at least 30 frames apart, the retro-reflective proposer is off, and a plate proposal must still score 0.2 after the geometry prior.

Delhi, replayed on the same banks: 7 → 9 correct, 0 wrong. Junk candidates fall by half and the clip runs 3.7× faster.

**Near backing** (a second reader's string one substitution away counts as support) is kept off for two CRNNs: they share their confusions, and the primary's `DL4SAS6522` "backed" v6's `DL6SAS6522` against the true `DL6SAS6524`. It now counts only when the backing reader is a whole-crop text reader, which is independent of the CRNNs.

### 2. Per-camera configuration

`config/camera_profiles.yaml`, `anpr/camera/profile.py`. A camera is matched by key or alias (canonical registry id, grid id, or clip name) and may override any threshold, its sampling and its low-light mode. The Delhi camera sets `preferred_state: DL`; with the global GJ every Delhi string carried a 0.7 grammar penalty.

`cameras.yaml`, `profiles.yaml` and `plate_kinds.yaml` were removed: nothing read them.

### 3. Native-resolution detection, and no whole-frame brightening (+330 tracks, 2.4× faster)

720p frames were being enlarged to 1920 for the vehicle detector, and dark frames were CLAHE-boosted before detection. On rec_cam07 the enlarged input halved the tracked detections (27 against 41) and the brightening cost another third (18 against 27).

Measured over 9 clips: no plate lost, cam01 88 → 129 tracks, cam08 63 → 87, cam07 banked plates on both its tracks instead of one, TfL clips 3.8 → 34 fps.

### 4. Reading floor and duplicate vehicles

- `reading.crop_read_min_width_px: 24` — a plate too narrow to carry glyphs is not read at all; the vehicle stays tracked and its wider crops are read when it comes closer. Same plates, less OCR.
- `reading.merge_same_plate_seconds: 15` — two fragments that read the same registration within 15 s are one vehicle. Delhi reported HR26CC2083 four times and DL13CA2927 three times: 17 sightings for 11 vehicles.
- One sighting per merged vehicle is emitted (`app/anpr_engine.py`); before, every fragment of a merged vehicle was sent.

### 5. Format-corrected candidates

`reading.coerce_votes` adds, for a read that is a registration except for one class of character, the same string with each character moved into the class its slot wants (O/0, I/1, S/5, B/8, Z/2, G/6), at a fraction of the read's confidence. The original read keeps its vote: the evidence decides rather than a rewrite.

### 6. Pass budget per camera

A plate needs many frames of the same vehicle. On the Delhi clip:

| Pass | Frames processed | Plates confirmed |
|---|---:|---:|
| Deployed (every 20th frame, 25 frames) | 25 | 2 |
| Every 10th, 60 frames | 60 | 2 |
| Every 5th, 100 frames | 100 | 2 |
| Every 2nd, 150 frames | 150 | 4 |
| Whole clip, every 5th | 797 | 11 |

So the budget is now per camera: cam06, cam07, cam12 and the Delhi camera take a dense pass (stride 2, 150 frames); the twelve cameras whose widest plate per track is 14–31 px keep stride 20, because no budget makes an unreadable plate readable and their frames are better spent counting vehicles.

### 7. Robustness (no plate counts, but plates were being lost)

From the audit: a pass whose every frame was skipped crashed and dropped the plates the engine held; a failed final upload dropped its payloads silently; clips carried wall-clock timestamps, so the 3 s fragment-merge window depended on replay speed; closed crop banks and the gate log grew without limit; the live wall never settled its tracks; and the pass-end diagnostics read keys the engine never returned, so the line that explains "why no plates" never printed.

### 8. A plate two kinds of reader agree on (+1 plate on the busiest clip, 0 false)

`reading.cross_reader_confirm`. The vote rules above want one reader's string to hold a share of that
reader's own crops. On a long daylight track the readings scatter across blur variants, so no single
share is reached even when the readers agree: on the cam06 noon clip GJ11VV7988 was read exactly by the
primary CRNN on 5 crops and by the whole-crop text reader on 11, every share sat between 0.11 and 0.29,
and the plate was thrown away. Now a string that readers of *both kinds* read on their own crops, on at
least 2 crops each and 6 together, with no rival reading of the same glyphs behind more crops, confirms
on that evidence. Two CRNNs cannot do it between themselves: they share their confusions, which is the
same reason near backing between two CRNNs was switched off in change 1.

Measured by replay on the noon clip, whose ground truth was read by eye over all 102 vehicles that had a
plate proposal: 14 → **15 of the 16 plates legible by eye, 0 wrong**. The one that remains, GJ1KR6061, is
misread by every reader (the K), so refusing it is the right answer. Delhi 13 of 20 and cam06 1080p 3 of 5
are unchanged, and the cameras that confirm nothing still confirm nothing.

## The architecture, stage by stage

The pipeline is vehicle-centric: nothing is read per frame, and a plate is decided once per vehicle.

```
video / RTSP -> frame sampling (per camera) -> vehicle detection -> ByteTrack -> vehicle id
  -> plate detection inside the vehicle box -> candidate bank (quality, size, angle, detector score)
  -> best crops -> perspective correction -> enhancement -> OCR (CRNN x2, optional text reader)
  -> per-crop reads -> weighted string vote -> Indian format validation -> one plate per vehicle
```

| Stage | Where it lives |
|---|---|
| Frame sampling, low-light routing | `app/worker.py`, `anpr/sampling.py`, `app/frame_quality.py`, per camera in `config/camera_profiles.yaml` |
| Vehicle detection and tracking | `anpr/detect/vehicle.py`, `config/bytetrack.yaml` |
| Overlay and sign masking | `anpr/detect/overlay_mask.py`, `anpr/detect/static_text.py`, `config/roi.yaml` |
| Plate detection in the vehicle box | `anpr/detect/plate.py` (geometry prior, score floor, one plate per track per frame) |
| Candidate bank, quality scoring, best-frame selection | `anpr/track/crop_bank.py`, `anpr/enhance/quality.py` |
| Perspective correction | `anpr/detect/corners.py`, `anpr/enhance/rectify.py` |
| Enhancement and super-resolution | `anpr/enhance/` (glare, denoise, deblur, fuse, sr) |
| OCR | `anpr/read/` (crnn, awiros, parseq, rows, beam_grammar, ensemble) |
| Temporal fusion and the confirm decision | `anpr/pipeline.py` (`_crop_reads`, `_string_vote`, `_vote_pick`, `_decide`), `anpr/fuse/rover.py` |
| Indian format validation | `anpr/plate_grammar.py`, `config/india_codes.yaml` |
| Per-camera configuration | `anpr/camera/profile.py`, `config/camera_profiles.yaml` |
| Evaluation | `tools/anpr_benchmark.py`, `anpr_replay.py`, `anpr_profile_cameras.py`, `anpr_gt_sheets.py`, `anpr_gt_plates.py` |

Every stage the target architecture asks for exists and is separately configurable. The package keeps
its present names rather than being renamed into `detection/`, `tracking/`, `plate_detection/` and so
on: `services/edge-worker/anpr/` is a vendored copy of the research repository's package, changes are
carried across by hand, and a rename would break that for no measured gain. The table above is the map
between the two namings.

## What was tested and rejected

| Change | Result |
|---|---|
| Merge fragments by shared reading hypotheses | Delhi 11 → 9 correct |
| Keep 20 best crops per vehicle instead of 12 | no change, more reading |
| Confirm a vote on 3 crops instead of 4 | a false confirm on Delhi (DL01AP4173 for DL8CAP4175) |
| 32 px reading floor instead of 24 | same plates, no further saving |

## At camera speed

`tools/anpr_benchmark.py run --realtime` paces a clip at its own frame rate and drops any frame that
arrives while the engine is busy, exactly as the live reader's newest-frame grabber does. Measured on
three recordings of grid cam06 — the 150 s 854x480 clip from the night1 session, the 1080p sandbox
recording and the 720p grid recording — on an Apple M1 with the whole-crop text reader loaded:

| Clip | Speed against live | Stride it settled on | Frames processed / dropped | Correct plates | False |
|---|---|---:|---|---:|---:|
| cam06 night, 854x480, 150 s | 1.31x | 9 | 1 016 / 20 | 5 of 6 | 0 |
| cam06, 1080p, 66 s | 0.92x | 12 | 196 / 607 | 3 of 5 | 0 |
| cam06, 720p, 45 s | 1.00x | 3 | 275 / 301 | no readable plate; reads the green EV plate that matches what is legible | 0 |

Eight of the eleven readable plates across the three clips, none wrong, at camera speed on one machine.

Two things came out of this:

- **The sampler has to respect the hardware.** At 1080p the profile asked for every second frame, about
  12 frames a second, while this machine processes 3. Of 1 189 frames wanted, 941 were dropped wherever
  they happened to fall, and 2 plates were read. At stride 5 and stride 8 the same engine read 3.
  `AdaptiveSampler.pace()` now raises the effective stride from the measured per-frame time and the
  source frame rate, never below the configured stride, so a faster machine keeps the dense sampling.
  Self-paced, the 1080p clip settled on stride 12 and read 3.
- **One 1080p camera is about one machine's worth of work here.** 173 ms a frame at 1080p against 58 ms
  at 854x480. The estate's cameras are 720p, where the same engine runs at 96 ms a frame and keeps up
  with the camera.

## What still misses, and why

`tools/anpr_failures.py --tag final2` pairs every missed readable plate with the nearest reading the run
produced (`reports/anpr_benchmark/final2/FAILURES.md`). On the Delhi clip:

| Missed | Nearest reading | Why it was not confirmed |
|---|---|---|
| DL5SAR5109, UP13AY3893 | the same string, exactly | only the text reader read them; no reader of the other kind read them at all |
| DL6SAS6524 | DL6SAS6522 | last glyph read wrong by both CRNNs |
| DL6SBE6415 | DL6SRE6415 | one glyph wrong; the true string never appears |
| DL11SD3385 | DL11SD385 | the text reader drops a glyph on this two-row scooter plate |
| DL1LAB9684 | DL11AR9684 | 5 crops of a 45 px yellow plate; readers disagree |
| UP14EC6398 | MP14EC6396 | readers disagree on the state and the last glyph |

On cam06 1080p, GJ18X6705 (a two-row auto plate) and GJ03KS7334 are read only in part; the same auto
passes the night clip and misses there too. On the noon clip the one miss, GJ1KR6061, is misread by every
reader (the K), so no reading of it is confirmed. On cam07 the one readable plate is never proposed by
the plate detector on the two frames where it is wide enough.

The pattern: what remains is not decision logic but glyph-level recognition on 45–100 px plates, and
plate detection on dark 720p footage. Both are model work — a reader trained on more Indian two-row
plates, and a plate detector trained on dark low-bitrate frames — not tuning.

## The dataset the evaluation produced

`tools/anpr_export_dataset.py --tag final` writes every tracked vehicle's plate crops with their
measurements, readings and the by-eye plate: 175 vehicles and 932 observations from the final run, as

    <camera>/vehicle_<track>/<frame>.png
    <camera>/vehicle_<track>/meta.json

Each observation carries frame, timestamp, box, size band, sharpness, brightness, contrast, blur, skew,
detector score, layout and its own reading; each vehicle carries the engine's verdict, the reason, and
the ground-truth plate where the clip has one. That is the training and regression set for the model
work above.

## What the grid footage can give

Eleven of the fifteen grid clips have a median widest plate of 14–31 px per track, against a readability floor of about 60 px for ten glyphs. One plate in all fifteen clips is legible in full by eye (cam07, GJ32AG0416). That ceiling is the footage: 720p at 250–1 200 kbps, mostly at night. The Delhi clip, at 1080p and 25.7 Mbps, carries 20.

The system's job on those cameras is therefore to count vehicles honestly and to read nothing it cannot see: it confirms no plate on any of them.
