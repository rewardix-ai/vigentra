# Reading the plate: enhancement, OCR, and what the footage allows

Companion to [anpr-dataset.md](anpr-dataset.md), which covers finding the
plate. This document covers everything after the box: the uniform dataset
the reader and the enhancers were trained on, the models, what was measured
on 2026-09-08/09, and the per-camera verdicts that follow from it.

## The chain, as deployed

```
frame -> vehicle detector -> plate detector (two models, one per pass)
      -> attach plate to vehicle -> enhancement (single-frame SR on every
      crop, multi-frame SR once a track has 2+ crops, CLAHE / low-light /
      glare / sharpen variants) -> OCR (PaddleOCR; the trained reader is
      optional) -> grammar (Indian formats, confusion-aware repair)
      -> track consensus -> confirmed plate
```

Every value below was chosen by a measurement recorded under
`services/edge-worker/reports/`. `models/PROVENANCE.json` says which file
came from which run.

## The uniform dataset (`dataset/v4_uniform`)

Not one plate in the grid footage is readable by eye, so there is no real
text to train a reader on. `tools/build_uniform_dataset.py` composites a
rendered plate with known text (`tools/synthesize_plates.py`: grammar-legal
formats, Gujarat prior, one or two rows, HSRP styling, five colour schemes,
ten faces) onto the real plate position of a real vehicle crop, then
degrades the whole crop the way the cameras do: blur, motion, noise, JPEG,
an H.264 pass on a third, distance down to 6 px, 30% night, headlight glare
(bloom, veil, clipping) on 55% of night crops. Every image carries a box for
the detector and the text for the reader.

| Item | Count |
|---|---|
| Synthetic vehicle images (train) | 19,999 (+604 real) |
| Tiers extreme / severe / moderate / mild | 5,028 / 6,966 / 4,956 / 3,049 |
| Median plate width per tier (px) | 9 / 16 / 33 / 63 |
| Night / glare images | 10,134 / 7,785 |
| Reader crops (train), all labelled with text | 77,988 |
| Single-frame SR pairs / multi-frame (5 frames) sets | 25,996 / 10,000 |

`--reader-only` replays the same seeds against an existing build and
rewrites only the reader crops and SR pairs; it was verified to reproduce
identical labels. Every crop carries its text at every size by instruction;
the per-width evaluation, not a floor, says where reading holds.

## Enhancement

`anpr/sr.py` holds two networks and the alignment they need.

**Single-frame** (`PlateSR`, 64 features, 8 residual blocks, pixel-shuffle
x4, correction over bicubic; `models/plate_sr.pt` = `runs/sr/S3_wide_cont`).
Iterated against the sharp truth until the gain fell under 0.1 dB:

| Validation pairs | Bicubic | S1 | S2 | S3 |
|---|---|---|---|---|
| All | 13.10 | 13.72 | 14.31 | 14.32 |
| Under 24 px | 12.35 | 13.01 | 13.34 | 13.29 |
| Night | 12.73 | 14.19 | 15.28 | 15.34 |
| Headlight glare | 8.62 | 11.84 | 13.21 | 13.23 |

**Multi-frame** (`PlateMFSR`: five aligned frames stacked on the channel
axis, six residual blocks, correction over the bicubic of the frames'
median; `models/plate_mfsr.pt` = `runs/mfsr/M2_cont`). Frames are
registered to the sharpest by ECC translation (`align_frames`). Scored on
the same 500 plates as the single-frame model:

| Plates | Bicubic | S3 single | M2 multi |
|---|---|---|---|
| All | 12.21 | 13.25 | 13.55 |
| Under 24 px | 11.71 | 12.74 | 13.33 |
| 16 to 24 px | 12.08 | 13.10 | 13.54 |
| 48 px and up | 13.50 | 14.80 | 14.29 |

Both run in the pipeline: `enhance.SuperResolver` prefers `plate_sr.pt`
under `sr_backend: auto`; `pipeline._multiframe_readings` fuses a track's
kept crops once it has `fuse_min_frames` of them and reads the result as one
more vote. Below about 14 px the output of either is a cleaner blur: the
characters were never captured. That is the physical floor, not a model
limit.

Sheets in the agreed layout (vehicle | snapshot | bicubic | single SR |
multi SR | truth): `tools/result_sheet.py`, outputs under `reports/result_sheet_*.jpg`.

## OCR: what was measured

`tools/train_plate_reader.py` trains `anpr/reader.py` (a 4.4M-parameter
CRNN over the 36-symbol plate alphabet, 32x192 input, CTC, track fusion by
summing per-column log-probabilities). Runs R1 to R7 are recorded in
`reports/reader/` and `runs/reader/`.

The real test is `dataset/real_plates`: 49 crops of 9 plates from a Delhi
video, labelled by reading the images, cut to the detector's box
(`--tight`) because that is what the pipeline hands the OCR:

| Engine | Exact | Characters |
|---|---|---|
| PaddleOCR, detector-tight crops | 37% | 68% |
| Trained reader R5 (readable-first curriculum), same crops | 0% | 31% |
| PaddleOCR, the old loose crops | 4% | 48% |

Findings that shaped the config:

- Hard-first reader training (R1 to R4) collapsed to a constant output; a
  readable-first order (`--stage-widths 40,24,0`, every crop still trained
  on) learns, but plateaus near 20% exact on 60 to 100 px synthetic crops
  and reads no real plate. The reader stays in the code and in training;
  `ocr.engines` lists it only when it beats PaddleOCR on the tight test
  (`tools/run_after_r7.sh` performs that check).
- Loose crops halve PaddleOCR's accuracy. Evaluate on detector-cut crops.
- Reader training must use `--cache` on this box (epochs 45 s instead of
  10 min).

## Detector: one model per pass

On 382 real frames of cameras 6 and 7 (`reports/footage_comparison_hybrid.json`):

| Configuration | Full-frame boxes | ROI boxes | Attached | Confirmed reads |
|---|---|---|---|---|
| Original model, PaddleOCR, 40 px floor | 35 | 56 | 68 | 1 |
| B in both passes, reader + Paddle, no floor | 10 | 37 | 37 | 2 |
| Original on full frame, B in vehicle boxes | 38 | 37 | 52 | 6 |

`DetectConfig.plate_model_frame` names the full-frame model
(`models/plate_detector_frame.pt`, the original); `plate_model` the ROI
model (B). The detector G trained on the synthetic composites was rejected
on measurement: worse on the tiniest band at every confidence, with two to
three times the false boxes.

## The estate, camera by camera

`tools/compare_on_footage.py` over every feed on disk followed by
`tools/estate_table.py` gives, per feed: vehicles, plates boxed, plates
attached, plate width p50/p90, reads, confirmed reads, and a verdict:

- **READABLE**: median boxed plate 30 px or wider; most vehicles pass
  through the band the reader and enhancer work in.
- **MARGINAL**: only the nearest vehicles reach 30 px; a zoom preset on the
  stop line would make the camera readable.
- **RE-AIM**: no vehicle comes close enough; software cannot yield reads
  here. This is a camera-placement finding and the system reports it as
  one instead of inventing plates.

The report for the run of 2026-09-09 is `reports/footage_estate.json` and
`reports/estate_table.md`.

## Runners

| Script | What it does |
|---|---|
| `tools/run_sr_iterations.sh` | S2, S3, M1 against the truth pairs, with sheets |
| `tools/run_overnight.sh` | M2, readers R2/R3, install, footage comparison |
| `tools/run_after_readers.sh`, `run_after_r6.sh`, `run_after_r7.sh` | reader evaluation and the reader-vs-PaddleOCR verdict |
| `tools/run_final_compare.sh` | install B + frame model + S3 + M2 (+ reader) and compare on footage; `DETECTOR=none` keeps B |
| `tools/uniform_sheet.py`, `tools/result_sheet.py` | dataset and result sheets |

Shell scripts must stay LF: a CRLF checkout breaks them under Git Bash
(`.gitattributes` enforces it; the Write tool and Python text writes produce
CRLF on Windows).

## Hardware notes

8 GB of RAM and a 13 GB page file: two trainings plus a data builder, or a
training plus a demo that loads YOLO and PaddleOCR, exhaust the commit
limit (segfaults, "paging file too small", 6 MB allocation failures). Run
one GPU job and one CPU job at a time. Stop Docker Desktop's WSL VM before
training. `multiprocessing` spawn is refused in the Claude sandbox; the
builders use thread pools.
