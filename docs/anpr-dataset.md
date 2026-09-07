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
python tools/eval_plate_detector.py --version v2 --split test
python tools/eval_plate_detector.py --version v2 --split test --imgsz 1536
```

Reports recall per size band, per difficulty tag and per readability class. The
headline number is recall on tiny plates; a model strong on large plates and
weak on small ones has not solved this problem.

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

Not run automatically. The dataset must be reviewed first — see the provenance
warning in `manifest.json`.

```bash
yolo detect train \
  data=dataset/v2/data.yaml \
  model=yolo11s.pt \
  imgsz=960 \
  epochs=120 \
  batch=8 \
  patience=30 \
  scale=0.9 mosaic=1.0 copy_paste=0.3 \
  degrees=7 perspective=0.0005 \
  hsv_v=0.5 hsv_s=0.6 \
  fliplr=0.0 \
  project=runs/plate name=v2
```

**Image size 960, not 640.** The dataset unit is a vehicle crop, and the median
plate in it is a small fraction of that crop's width. At 640 a crop is
downscaled before the model sees it and the plate loses the pixels that carry
the characters. 960 matches `roi_imgsz` in `config.yaml`, so training and
deployment see the plate at the same scale.

**`fliplr=0.0`.** Horizontal flipping is on by default in ultralytics and is
wrong here: a registration is directional text, and a mirrored plate teaches
the model a glyph shape that does not exist.

**`scale=0.9`, `mosaic=1.0`, `copy_paste=0.3`.** Aggressive scale jitter and
mosaic put the same plate into the batch at many apparent distances, which is
the augmentation that matters for small-object recall. Copy-paste multiplies
the small-plate instances without duplicating whole frames.

**`hsv_v=0.5`, `hsv_s=0.6`.** The estate is largely night and dim footage;
exposure jitter is doing real work, colour jitter less so.

**Do not add `fliplr`, `shear` or heavy `degrees`.** Plates on these cameras are
close to frontal; teaching severe rotation costs capacity that small plates
need.

---

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
