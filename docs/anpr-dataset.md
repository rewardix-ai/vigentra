# Plate dataset and the small-plate problem

How to measure what the cameras deliver, mine the cases the detector fails on,
and build a training set from them without poisoning it.

Everything here lives in `services/edge-worker/tools/` and shares
`tools/_corpus.py`, so the analysis, the mining and the dataset build cannot
disagree about what "tiny" or "unreadable" means.

---

## The two rules

**A model's own output is a proposal, not a label.** Training a detector on
boxes it produced teaches it that its own mistakes are correct. Nothing reaches
`images/train` without an adjudication attached, and every adjudication records
who made it. Proposals from the deployed detector land in `unverified/` and
stay there until reviewed.

**A plate too small to read is still a plate.** Detection and readability are
different questions about different things. Readability is recorded beside
every box and never decides whether the box exists. `--min-plate-width` defaults
to `0` — excluding plates for being small is the bug this exists to fix, so it
has to be asked for.

---

## Running it

Set `ANPR_MODELS_DIR` if the weights are not under `services/edge-worker/models`.
All commands run from `services/edge-worker/`.

### 1. Measure the feeds

```bash
python tools/analyze_feeds.py --per-camera 20
```

Add `--ocr` to measure readability too (much slower, and memory-hungry when the
detector and PaddleOCR are both resident). Restrict with
`--cameras cam06 cam07`, point elsewhere with `--roots <dir>`.

Writes `reports/feed_analysis.json` (per-camera statistics and the stage funnel)
and `reports/plate_samples.json` (every measured plate box).

### 2. Mine the hard cases

```bash
python tools/mine_hard_cases.py --per-category 200 --neighbours 2
```

Ranks each difficulty category by the quantity that defines it — smallest first
for `tiny`, blurriest first for `blur` — and keeps the worst. `--neighbours`
retains the frames either side of each pick so a vehicle is present across
several looks.

### 3. Build the dataset

```bash
python tools/prepare_dataset.py --version v2 --overwrite
```

Useful flags: `--cameras cam06 cam07`, `--unit frame` (default `vehicle_crop`),
`--min-plate-width 0` (leave it at 0 unless you mean it).

### 4. Report and review

```bash
python tools/dataset_report.py --version v2 --sheets --limit 200
python tools/dataset_report.py --version v2 --sheets --tag tiny
python tools/dataset_report.py --version v2 --unverified      # the review queue
```

`--unverified` renders the proposals awaiting adjudication, cut from the
original frames with enough context to judge them. **Look at these.** The
review question is only *is that box on a plate* — readability is not part of
it.

### 5. Evaluate, stratified by size

```bash
# compare two sets of weights, per size band: precision, recall and AP.
# imgsz defaults to 960 and NMS to 0.7 - what the deployed ROI pass runs.
python tools/eval_by_size.py --weights D:/ANPR/models/plate_detector.pt --tag baseline
python tools/eval_by_size.py --weights runs/detect/runs/plate/C_smallobj_aug/weights/best.pt

# the pipeline's own view: recall through the deployed Detector wrapper
python tools/eval_plate_detector.py --version v2 --split test

# what the errors actually look like
python tools/error_analysis.py --weights <weights> --imgsz 960 --conf 0.25

# and the far end: the full pipeline on untouched full frames
python tools/compare_on_footage.py --weights <baseline> <candidate>     --labels baseline candidate --cameras cam06 cam07
```

`eval_by_size.py` is the tool for comparing weights; `eval_plate_detector.py`
answers the different question of how the assembled pipeline behaves. The
headline number in both is recall on tiny plates - a model strong on large
plates and weak on small ones has not solved this problem.

---

## Dataset layout

```
dataset/<version>/
  images/{train,val,test}/     adjudicated positives + hard negatives
  labels/{train,val,test}/     YOLO boxes; an EMPTY file means "no plate here"
  hard_cases/<tag>.json        index into images/, by difficulty
  quarantine/                  boxes excluded on geometry, with reasons
  unverified/                  proposals awaiting review
  review/sheets/               contact sheets of the adjudicated set
  review/unverified_sheets/    contact sheets of the review queue
  metadata.csv                 one row per plate box, fully described
  manifest.json                provenance, counts, split rule, seed
  data.yaml                    ultralytics config
```

An empty label file is not a missing label — it is a positive instruction that
the image contains no plate. That is how the `not_plate` crops teach the model
to stop firing on headlights, badges and caption bars.

### Splitting

By `(camera, time block)`, never by frame. A block is 20 consecutive frames and
is assigned whole by a stable SHA-256 hash, so:

* consecutive frames of one vehicle cannot straddle train and val/test;
* rebuilding from the same footage reproduces the same split exactly, including
  when frames are added in the middle.

`dataset_report.py` asserts this and prints `CLEAN` or `LEAKING`.

For a genuine generalisation test, hold out whole cameras with `--cameras`.

---

## Training

Not run automatically. Review the dataset first - see the provenance warning in
`manifest.json`.

`tools/train_plate_detector.py` runs four controlled configurations over the
same dataset with the same seed, so the difference between them is the
measurement rather than run-to-run noise:

```bash
python tools/train_plate_detector.py --list
python tools/train_plate_detector.py --all --epochs 60 --device 0
python tools/train_plate_detector.py --experiment C_smallobj_aug --epochs 60
```

| experiment | what changes |
|---|---|
| `A_baseline_640` | low-resolution control, stock augmentation |
| `B_highres_960` | the deployed ROI resolution (`roi_imgsz: 960`); only imgsz differs from A |
| `C_smallobj_aug` | B plus scale jitter / mosaic / small rotation aimed at small objects |
| `D_tiny_oversample` | C plus 3x exposure to tiny-plate images at an **equal step budget** |

Each writes weights, `args.yaml`, `results.csv`, `experiment.json` and both
checkpoints under `services/edge-worker/runs/plate/<name>/` — a rerun gets
`<name>2`, never an overwrite. Nothing touches the deployed weights.

### Before retraining: the association step

Retraining cannot help a plate the pipeline discards after finding it. The
funnel on cam06/cam07 showed 18 of 20 detected plates orphaned — not attached
to any vehicle — for two reasons that were fixed in `anpr/detect.py` before
any training result was trusted: the ROI pass searched a vehicle box expanded
by 4% but attachment tested containment against the *unexpanded* box, so a
bumper plate flush with the searched edge failed the 0.55 floor exactly when it
was small; and vehicles the tracker had not yet assigned an id were skipped
outright, which on sparse frames is most of them. An ROI box now attaches to
the vehicle whose crop found it, and id-less vehicles get a stable fallback id.
Same footage: attached 2 → 15.

### Why 960 and not 1280

Measured, not assumed. The dataset unit is a vehicle crop whose median longest
side is 168 px, so letterboxing to 640 already upscales the plate about 3.8x.
Median effective plate width is **53 px at imgsz 640** and 79 px at 960, and
**zero** boxes fall below the stride-8 floor of the detection head at any of
these sizes. 1280 would therefore spend 4x the compute on interpolated pixels
that carry no additional information, and a P2 (stride-4) head is not the fix
here. The information ceiling is the original crop, not the input size.

### Hardware, measured on the target card

An RTX 3050 with 4 GB:

| config | peak VRAM | epoch |
|---|---|---|
| 640 / batch 8 | 1.94 GB | ~21 s |
| 960 / batch 4 | 2.31 GB | ~35 s |
| 960 / batch 6 | 3.31 GB | - |

960/batch 6 fits but leaves little headroom for validation, so the 960
experiments use batch 4. AMP is on throughout, which roughly halves activation
memory.

**Host RAM matters more than VRAM here.** Training first failed with
`CUBLAS_STATUS_NOT_SUPPORTED` at every batch size - the giveaway that it was
not VRAM, since a real OOM would have succeeded at the smallest. The full
traceback showed `fatal: Memory allocation failure` while CUDA compiled
kernels, on a box with 0.44 GB of system RAM free. Stop the app containers
(`docker compose stop`) and, if needed, `wsl --shutdown` before training.
`workers=0` for the same reason: forked dataloader workers make that failure
more likely and buy almost nothing on 604 small crops.

### Augmentation, and what is deliberately absent

The training images are already real CCTV - vehicle crops from H.264 streams at
400 kbps-2 Mbps, arriving with genuine compression blocking, motion blur and
sensor noise. Synthesising more of it would produce double-degraded images no
camera ever emits. So the augmentation budget goes on *geometry and exposure* -
`scale=0.9`, `mosaic=1.0`, `hsv_v=0.5` - which genuinely vary between cameras
and times of day.

`fliplr=0.0` throughout: a registration is directional text, and a mirrored
plate teaches a glyph shape that does not exist. Two knobs that look relevant
are deliberately absent because they do nothing here: `copy_paste` is a no-op
on box-only labels (ultralytics returns early without segments), and
`erasing` is classification-only.

Early stopping is off. ultralytics closes mosaic only at `epochs − close_mosaic`
and breaks out first if `patience` fires, so with it on, whether a run ever
trained mosaic-free depended on when it happened to peak — an undocumented
variable in a comparison meant to be controlled.

### Tiny-plate oversampling

`D_tiny_oversample` repeats tiny-plate image *paths* in the train list rather
than copying files. Each appearance is independently augmented - different
mosaic partners, scale, crop and exposure - so the model sees genuinely
different pictures of the same plate rather than the identical tensor three
times. The factor stays at 3: past that the tiny images dominate the batch
statistics and the larger bands regress, which is this project's own failure
in reverse. Val and test lists are untouched, so metrics stay comparable.

Repeating images makes the epoch ~46% longer, so at equal epochs the
oversampled run would also simply have trained longer. The harness scales its
epochs down by the list's growth to hold optimizer steps equal, so exposure is
the only thing that differs from C.

## Evaluation

`eval_plate_detector.py` matches predictions to labels greedily at
**IoU ≥ 0.30**, not the usual 0.50. At 12 px wide a one-pixel offset costs about
0.25 IoU, so 0.50 would score a correct detection on a small plate as a miss —
baking the bias we are measuring into the measurement.

Metrics are reported per stratum:

* size band — `EXTREMELY_TINY` … `LARGE`, derived from the data
* difficulty — `tiny`, `blur`, `low_light`, `glare`, `angled`, `partial`,
  `low_contrast`, `multi_vehicle`, `low_confidence`, `unreadable`
* readability — `READABLE`, `UNREADABLE_TOO_SMALL`, `UNREADABLE_QUALITY`

### The caveat that must travel with every number

Recall here is an **upper bound**. The labels contain only plates that some
proposer proposed; a plate no model ever saw is invisible to the evaluation and
does not count as a miss. That residual biases against the smallest plates —
the class being measured. Removing it needs an exhaustively hand-labelled frame
set, which does not exist yet.

---

## Size bands

Derived from the observed width distribution by quantile
(`_corpus.derive_bands`), not from constants, because a fixed pixel threshold
encodes an assumption about vehicle distance that is wrong the moment the camera
changes. With fewer than 20 observations it falls back to thresholds anchored on
the physical floor and says so in `provenance`.

Reported alongside them, and never conflated with them, is the **physical
readability floor: 60 px**. A single-row Indian registration carries about 10
glyphs across its width, and below roughly 6 px per glyph no recogniser resolves
a character. That is arithmetic about the information present, not a tuning
knob — and it is why `UNREADABLE_TOO_SMALL` is a distinct outcome from
`UNREADABLE_QUALITY`. The first is physics and the detector should still find
the plate; the second means the pixels were there and something else failed,
which is the case worth mining hardest.

---

## What cannot be determined automatically

`occluded`, `dirty_or_damaged`, `non_standard` and `false_positive` are never
assigned by the classifier. None is decidable from a crop's statistics, and a
guessed label trains the model on fiction. Samples that plausibly need one are
flagged `needs_human_review` with a reason, and `visibility` and `occlusion`
are emitted as `UNKNOWN` in `metadata.csv` for a reviewer to fill in.

`quarantine/` holds boxes excluded on **geometry** — a box taller than it is
wide cannot be a plate whatever the adjudication says. It is not a size filter,
and the reasons are recorded so the exclusion is auditable and can be
overruled.
