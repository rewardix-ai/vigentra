# ANPR modernisation — moving off EasyOCR

Read `docs/anpr.md` first. This document does not change any of the policy in
it: ANPR stays off by default, plates stay strict, the frame still never leaves
the device. What changes is the **machinery underneath**, because the current
machinery cannot run live.

---

## 1. Why the current pipeline is slow — it is architectural, not a tuning problem

`docs/anpr.md` §7 measures ~2.5 s per vehicle crop on CPU. That figure is not
EasyOCR being badly configured. It is the consequence of two design choices:

**We ask a scene-text engine to do a plate-shaped job.** EasyOCR is a general
optical-character-recognition stack: CRAFT runs a full *text detection* pass to
find arbitrary text regions anywhere in the image, then a CRNN recogniser reads
each region. Finding text is the expensive half, and we pay it on every crop —
even though we already know exactly what we are looking for and roughly where
it is.

**We never actually locate the plate.** `plate_region()` crops the lower 45% of
the *vehicle* box at full width and hands that to OCR. That is a guess about
where a plate sits, not a detection. It makes the crop large (slow), noisy
(bumper text, stickers, the vehicle behind), and — in dense traffic — it
regularly contains a *different vehicle's* plate, which is how a plate ends up
attached to the wrong detection.

Both problems have the same fix.

## 2. The current standard architecture

Every serious real-time ALPR system built since ~2023 has the same three
stages, and the middle one is what we are missing:

```
frame
  ↓  vehicle detector          (we have this: YOLO11n)
  ↓  PLATE detector            ← missing; tiny YOLO trained only on plates
  ↓  plate OCR                 (fixed-size, detection-free, ~0.5 ms)
```

The third stage is the big win. Once you hand the recogniser a **tight, already
cropped plate**, you no longer need a text-detection pass at all. The model
becomes a single fixed-input forward pass that emits a fixed-length character
sequence. That is why the numbers below are in *milliseconds*, not seconds.

### Measured latency of the OCR stage

From the [FastPlateOCR model zoo](https://ankandrew.github.io/fast-plate-ocr/latest/inference/model_zoo/),
benchmarked on an RTX 3090 with the TensorRT/CUDA providers:

| Model | Arch | Latency b=1 | Plates/sec |
|---|---|---|---|
| `cct-xs-v2-global-model` | CCT, XS | **0.47 ms** | 2144 |
| `cct-s-v2-global-model` | CCT, S | **0.68 ms** | 1480 |
| `global-plates-mobile-vit-v2-model` (legacy) | MobileViT-v2 | 2.9 ms† | 344 |

† legacy row measured on an M1 CPU, not the 3090 — the two blocks are not
directly comparable. Take the CCT rows as the current numbers and treat the
legacy row as indicative of CPU-class performance.

Against our measured 2500 ms, the OCR stage stops being the bottleneck by
roughly three orders of magnitude. Read that skeptically — it is a *different
workload*, a tight plate crop rather than a wide vehicle crop, and the
comparison is only fair because the plate detector does the localising work
that EasyOCR was previously doing badly.

`cct-s-v2-global-model` is the recommended default for new integrations. The
"global" models are trained on plates from 65+ countries at ~93% plate-level
accuracy — India is in that distribution but is **not** specifically tuned for,
which matters (see §5).

## 3. Concretely: FastALPR

[FastALPR](https://github.com/ankandrew/fast-alpr) (MIT, ~550 stars, released
0.4.0 in March 2026) packages exactly the architecture above: plate detection
from [open-image-models](https://github.com/ankandrew/open-image-models),
OCR from [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr), both as
ONNX, both swappable.

```bash
pip install fast-alpr[onnx-gpu]      # CUDA — your laptop
```

```python
from fast_alpr import ALPR

alpr = ALPR(
    detector_model="yolo-v9-t-384-license-plate-end2end",
    ocr_model="cct-s-v2-global-model",
)
results = alpr.predict(frame)
```

The detector is a YOLOv9-tiny at 384px trained only on plates, end-to-end (NMS
folded into the graph). On a CUDA laptop that is a few milliseconds.

**Why this fits our design rather than fighting it.** `plates.py` already puts
the reader behind an abstract `PlateReader` with `name` / `version` /
`describe()`, precisely so the OCR engine is a deployment choice. A FastALPR
reader is a new subclass. `ANPR_ENABLE`, `ANPR_MIN_CONFIDENCE`, the state-code
check, `normalise_plate`, the retention rules and the audit path are all
untouched — they sit *downstream* of the engine swap.

### Alternatives considered

- [`ort-alpr`](https://github.com/tharakarehan/ort-alpr) — same ONNX-Runtime
  approach, CPU-focused, less maintained. Worth knowing about if the CUDA path
  ever becomes a problem on a deployment box.
- **PaddleOCR PP-OCRv4/v5** — much faster than EasyOCR and genuinely good, but
  still a general scene-text engine. It fixes the constant factor, not the
  architecture. Reach for it only if a plate-specific model cannot be licensed
  or trained.
- **Keeping EasyOCR with a plate detector in front.** This is the cheap
  half-step, and it is worth measuring: a tight plate crop makes CRAFT's job
  trivial and should cut the 2.5 s substantially. The spike measures this so
  the decision has a number behind it.

## 4. Real-time on a live feed — the part the model choice does not solve

Making OCR 1000× faster removes the *inference* bottleneck. It does not by
itself make the worker real-time, because our loop is still synchronous: decode
→ detect → for each vehicle, OCR → ingest. Three things still need doing.

**Decouple ANPR from the detection loop.** ANPR should be a bounded queue
consumed by a separate worker, so that when plate reading falls behind it sheds
load instead of stalling detection or growing memory. `docs/anpr.md` already
promises that "ANPR failing is not a reason to stop counting vehicles" — right
now that promise holds for a *missing model* but not for *saturation*.

**Batch the crops.** The OCR models take a batch dimension. Ten vehicles in a
frame should be one batched call, not ten sequential ones. On GPU this is
nearly free and it is where most of the remaining wall-clock goes.

**Track, then read once.** Add a tracker (ByteTrack ships with Ultralytics) and
read each *track*, not each detection. This kills the duplicate-read problem —
a car stopped at a signal currently produces one row per sampled frame for the
whole red cycle — and it lets you do something better than any single read can:
take the modal plate across N frames of the same vehicle and attach a
per-track agreement score. Three independent frames agreeing on `GJ01AB1234` is
qualitatively stronger evidence than one frame at confidence 0.77, and it is
the only real defence against the confident-misread failure in `docs/anpr.md`
§7. **This is the single highest-value change in this document**, and it is
orthogonal to which OCR engine you pick.

## 5. Indian plates — where the global models will disappoint

The `-global-` models are trained across 65+ countries. India is represented,
but Indian plates have local properties the global distribution under-weights:
hand-painted plates, non-standard fonts and spacing, two-line motorcycle
plates, state emblems, and the BH / diplomatic / military formats our regex
does not even accept yet.

Expect the stock model to underperform its headline ~93% on our footage. Plan
to fine-tune. Two current sources:

- [Indian License Plate Recognition Dataset (YOLO & OCR)](https://ieee-dataport.org/documents/indian-license-plate-recognition-dataset-yolo-ocr),
  IEEE DataPort, published August 2026 — YOLO-format plate boxes plus tightly
  cropped per-character images. It is shaped exactly for both stages of this
  pipeline.
- [`morsetechlab/yolov11-license-plate-detection`](https://huggingface.co/morsetechlab/yolov11-license-plate-detection)
  on Hugging Face — a ready plate detector if you would rather start from
  YOLO11 weights consistent with the detector we already run.

Published work fine-tuning YOLOv5s on Indian plates reports ~99.5% *detection*
accuracy. Note carefully that this is plate **localisation**, not character
recognition — the two get conflated constantly in ANPR papers, and it is
recognition that our §7 numbers are about.

`fast-plate-ocr` supports fine-tuning and export to ONNX directly, so an
India-tuned recogniser drops into the same interface with only a
`reader_version` change — which our schema already records per detection.

## 6. What this does and does not fix

| Problem | Fixed by this change? |
|---|---|
| 2.5 s/crop, cannot run live | **Yes** — the core win |
| Plate attached to the wrong vehicle | **Yes** — a real plate box can be associated to the vehicle box by containment, instead of assumed |
| Fragmented reads (`DL` + `1LCE5987`) | **Yes** — fixed-length sequence output, no fragments, `assemble_lines` becomes unnecessary |
| Duplicate reads of a stopped vehicle | No — needs tracking (§4) |
| Confident misreads | Partly — better model, but only multi-frame agreement really helps (§4) |
| `TG` missing, BH-series rejected | No — those are bugs in `normalise_plate` / `PLATE_PATTERN`, independent of the engine |
| Retention, audit, permissions | Unaffected by design |

## 7. Suggested order

1. Fix `STATE_CODES` (add `TG`) and `PLATE_PATTERN` (BH-series). Small, and
   independent of everything else here.
2. Run `scripts/anpr_benchmark.py` on `data/videos/traffic/traffic_live.mp4`
   to get real numbers on your own hardware and footage. Do not adopt on the
   strength of the table in §2.
3. If the numbers hold, add `FastAlprPlateReader(PlateReader)` alongside the
   EasyOCR one, selected by `ANPR_READER`. Keep both — the comparison stays
   reproducible and the rollback is an env var.
4. Add tracking and per-track modal plates.
5. Fine-tune on Indian data if stock accuracy is not good enough.

---

## Sources

- [FastALPR](https://github.com/ankandrew/fast-alpr)
- [FastPlateOCR model zoo and benchmarks](https://ankandrew.github.io/fast-plate-ocr/latest/inference/model_zoo/)
- [fast-plate-ocr](https://github.com/ankandrew/fast-plate-ocr)
- [ort-alpr](https://github.com/tharakarehan/ort-alpr)
- [Indian License Plate Recognition Dataset (YOLO & OCR), IEEE DataPort](https://ieee-dataport.org/documents/indian-license-plate-recognition-dataset-yolo-ocr)
- [yolov11-license-plate-detection, Hugging Face](https://huggingface.co/morsetechlab/yolov11-license-plate-detection)
- [Laroca et al., A Robust Real-Time ALPR Based on the YOLO Detector](https://arxiv.org/pdf/1802.09567) — the paper that established the detect-plate-then-recognise architecture
- [Laroca et al., An Efficient and Layout-Independent ALPR System](https://arxiv.org/pdf/1909.01754) — layout independence, directly relevant to mixed Indian plate formats
