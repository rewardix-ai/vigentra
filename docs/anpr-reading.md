# Reading the plate: the deployed chain

Companion to [anpr-dataset.md](anpr-dataset.md), which covers finding the
plate. This document covers everything after the box: the engine that turns a
track's crops into a registration, the thresholds that decide whether it is
allowed to leave the edge, and how the worker consumes it.

The engine under `services/edge-worker/anpr/` was replaced wholesale on
2026-09-12 with the vendored "Detect → Enhance → Read" core. The measurements
that justified the *previous* engine's design — the single-frame and
multi-frame super-resolution tables, the PaddleOCR-versus-trained-reader
comparison, the R1–R7 reader runs — described modules that no longer exist and
have been removed with them rather than left here to mislead. Nothing in this
document is a performance claim; where a number appears it is a configured
threshold, not a measured result.

## The chain, as deployed

```
frame (decoded once by the worker, handed to the engine)
  -> overlay mask      OSD strips, static text, hoardings: never plate candidates
  -> vehicle tracker   YOLO11 + ByteTrack, COCO car/motorcycle/bus/truck
  -> plate detector    per vehicle box: crop -> upscale >=640 px -> CNN
                       (+ retro-reflective proposer) -> geometry prior
  -> crop bank         every crop stamped with track, frame, PTS and quality
  (on track close)
  -> legibility gate   width / sharpness / contrast -> UNREADABLE
  -> enhance           rectify -> ECC register -> fuse (weighted median or
                       shift-and-add SR) -> glare -> denoise -> deblur ->
                       deskew -> binarise
  -> read              CRNN-CTC over variants -> grammar beam search ->
                       ROVER vote -> calibrated confidence
  -> record            CONFIRMED | CANDIDATE | UNREADABLE
```

A verdict exists only when a track closes. That is the single most important
property for anyone changing this code: the engine does not emit a plate per
frame, so the worker must ask for settled readings at the end of a pass. See
"How the worker consumes it" below.

## Where the knobs live

`services/edge-worker/config/thresholds.yaml`, loaded per camera. The values
that decide whether a reading is allowed out:

| Key | Meaning |
|---|---|
| `confidence.confirm_threshold` | fused confidence needed for CONFIRMED |
| `confidence.candidate_threshold` | below this the reading is not shown at all |
| `fusion.min_frames_for_confirm` | independent frames that must agree |
| `confidence.confirm_min_width_px` | narrowest best crop that may confirm |
| `confidence.confirm_min_hypotheses` | independent variants that must agree |
| `confidence.confirm_min_char_vote` | weakest character's share of its vote |
| `reading.vote_confirm` | crops and vote share a string vote needs |
| `reading.vote_reject` | grammar rules that bar a crop from voting |
| `reading.confirm_min_glyphs` | glyph-shaped marks a track's best crops must show to confirm (3) |
| `reading.glyph_crops` | how many of a track's best crops are searched for glyphs (8) |
| `reading.secondary_confirm_min_glyphs` | lets a second-reader string confirm on glyph evidence; unset, and inert here (see Readers) |
| `legibility_gate` | width, height, sharpness and contrast floors |

The file carries its own provenance: each block records the replay that chose
it. Read those comments before changing a number — several exist to block a
specific false confirm that was observed, not to tune a score upward.

## Never invent a plate

The design rule of the vendored engine, preserved here:

- every string must pass the plate grammar, and overlay tokens are rejected;
- CONFIRMED needs fused confidence **and** several agreeing frames, not one
  good look;
- a super-resolved hypothesis can never confirm on its own;
- CONFIRMED needs glyph-shaped marks in the pixels, not only agreement between
  reads: crops that all show the same pane of glass can "agree" on an invented
  string (`anpr/detect/char_evidence.py`, `reading.confirm_min_glyphs`). The
  check does not tell a plate from advertising text, which has glyphs too;
- a proposed plate box wider than 0.60 of its vehicle is demoted in the geometry
  prior (`PLATE_W_FRAC_OF_VEHICLE_MAX` in `anpr/plate_grammar.py`), since a
  registration never spans the vehicle carrying it;
- confidence is temperature-calibrated, so the number is comparable between
  cameras rather than being a raw softmax.

A plate too small or too blurred to read is reported as UNREADABLE. That is a
camera-placement finding, and the system reports it as one instead of guessing.

## Readers

`reading.readers` selects them. `crnn` runs on ONNX Runtime and its weights ship
in `models/`.

`reading.extra_crnn_weights` names a second CRNN (`reader_crnn_v6.onnx`, which
does ship). The adapter resolves it in `ANPR_MODELS_DIR` and passes both
readers to the engine (`app/anpr_engine.py`), so it is loaded and votes; under
`second_reader_mode: fallback` the primary reader decides every confirmation
and the second one may carry a confirmation only when it agrees with a third
opinion (`anpr/pipeline.py` `_vote_pick`).

`reading.readers` also asks for `awiros` — `anpr/read/awiros.py`, a PaddleOCR
model fine-tuned on Indian plates that reads a whole crop, two-row plates
included. It is imported lazily and its dependencies are **not** in the image
(`requirements-anpr.txt` says what enabling it costs). Where it is present it
is worth 2 more plates over the evaluation clips; where it is not, the engine
logs that it is unavailable and reads with the CRNNs alone.

## How the worker consumes it

`services/edge-worker/app/anpr_engine.py` is the only adapter between the
engine and Vigentra. It:

- feeds pre-decoded frames, so ANPR costs no extra stream — the worker decodes
  once for object detection and the same frame goes to the engine;
- passes PTS, not arrival time, so a sighting's timestamp survives buffering;
- resets the tracker on a discontinuity, because a scene cut must not splice
  two vehicles into one plate history;
- returns vehicle detections per frame and plates only from `finish()`, which
  drains the readings that settled during the pass;
- maps the engine's class names back to COCO ids, dropping anything outside
  the canonical vehicle vocabulary.

If the engine cannot be built — extras absent, weights missing, config absent
— `build_engine()` returns `None` and the worker keeps counting vehicles.
ANPR failing is never a reason to stop object detection.

## Weights

`models/` holds `yolo11s.pt` (vehicles), `plate_det_mix_n.pt` (plates) and
`reader_crnn.onnx` (reader), with `models/PROVENANCE.json` recording where each
came from. Weights are **not** committed: `.gitignore` excludes `*.pt` and
`*.onnx`, and the paths are overridable with `ANPR_MODELS_DIR`,
`ANPR_VEHICLE_WEIGHTS`, `ANPR_PLATE_WEIGHTS` and `ANPR_READER_WEIGHTS`.

## Running it

ANPR ships as a separate image stage so the default edge worker stays small:

```bash
docker compose --profile anpr build edge-worker
```

The stage adds `requirements-anpr.txt` and the `config/` directory on top of
the YOLO stage. `ANPR_ENABLE=true` switches the engine on in a worker.

Three of that file's entries are not obvious from reading our own source, and
all three were found the hard way - by running the engine on real footage
rather than by any import check:

| Package | Why it is needed |
|---|---|
| `onnxruntime` | the CRNN reader and the denoise/deblur stages load ONNX models; every import of it is inside a function, so its absence is silent - the reader simply reads nothing |
| `lap`, `scipy` | ultralytics imports its ByteTrack tracker, and that tracker's own dependencies, on the first tracked frame. The engine tracks vehicles (`anpr/detect/vehicle.py` calls `model.track()`); the plain YOLO detector only calls `predict()`, so nothing else here pulls them in |

`tests/test_vendor_imports.py` guards all three. Do not trim that file by
checking which imports appear at the top of a module - in this tree almost none
of them do.
