# ANPR optimisation: what changed, and what it is worth

Baseline, method and camera inventory: `docs/anpr-baseline.md`. What the system looked like before any change: `docs/anpr-audit.md`.

Every number here comes from `services/edge-worker/tools/anpr_benchmark.py` over the recorded clips, scored against `data/eval/anpr_gt/` (by-eye ground truth, used only for scoring). Reading-stage changes are compared by replaying one run's saved plate crops through the new code (`tools/anpr_replay.py`), so the detector, tracker and banks are identical between the two sides of the comparison.

**The measure:** unique vehicles whose plate the system reads correctly, and how many plates it gets wrong.

## Result

Over all 36 clips (15 grid cameras, the Delhi clip, the 1080p cam06 recording, 19 TfL clips):

| | Before | After |
|---|---|---|
| Correct plates, of the 26 readable by eye | 9 | **13** |
| False plates confirmed or sent | 0 | **0** |
| Wrong unconfirmed readings held in records | ~104 | **70** |
| Vehicle tracks | 1 524 | **1 854** |
| Plate candidates banked | 22 279 | **3 309** |
| OCR images | 36 727 | **14 604** |
| Total processing time (Apple M1, MPS) | 5 784 s | **2 389 s** |

Per camera, only the two clips that carry readable plates change: Delhi 7 → 11 of 20, cam06 1080p 2 → 2 of 5. No camera confirms a false plate, before or after.

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

## What was tested and rejected

| Change | Result |
|---|---|
| Merge fragments by shared reading hypotheses | Delhi 11 → 9 correct |
| Keep 20 best crops per vehicle instead of 12 | no change, more reading |
| Confirm a vote on 3 crops instead of 4 | a false confirm on Delhi (DL01AP4173 for DL8CAP4175) |
| 32 px reading floor instead of 24 | same plates, no further saving |

## What the grid footage can give

Eleven of the fifteen grid clips have a median widest plate of 14–31 px per track, against a readability floor of about 60 px for ten glyphs. One plate in all fifteen clips is legible in full by eye (cam07, GJ32AG0416). That ceiling is the footage: 720p at 250–1 200 kbps, mostly at night. The Delhi clip, at 1080p and 25.7 Mbps, carries 20.

The system's job on those cameras is therefore to count vehicles honestly and to read nothing it cannot see: it confirms no plate on any of them.
