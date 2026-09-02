# YOLO setup

Object detection in Sentinel runs at the **edge**, in
`services/edge-worker` — next to the video it is authorized to process. The
central API has no computer-vision dependencies at all: it receives detection
*metadata* and stores it. Raw frames never cross the wire.

```
authorized local video
        ↓
  edge analytics worker      ← ultralytics + torch live here only
        ↓  (detections, not pixels)
  POST /api/v1/detections/ingest
        ↓
  central registry + dashboard
```

---

## 1. Pinned versions

| Component | Pinned | Where |
|---|---|---|
| Python | 3.11 | `services/edge-worker/Dockerfile` |
| Ultralytics | **8.4.123** | `services/edge-worker/requirements-yolo.txt` |
| OpenCV | `opencv-python-headless >=4.10,<5.0` | same |
| NumPy | `>=1.26,<3.0` | same |
| torch | installed by Ultralytics, or pinned by you first | see below |

8.4.123 was the latest stable release on PyPI when this was written and was
verified with `pip index versions ultralytics`. It is pinned exactly, not
floated: every detection row stores the model name and version that produced
it, so the version must not drift underneath a stored result.

**Default model:** `yolo11n.pt` (`YOLO_MODEL_NAME`). The nano model is the
right default for an edge box — a few MB, runs on CPU. Move up to `yolo11s.pt`
or `yolo11m.pt` when you have a GPU and want the accuracy.

To change version, edit `requirements-yolo.txt` and rebuild. Do not `pip
install -U ultralytics` in a running container: stored detections would then
claim a version that is no longer what is installed.

---

## 1a. Verifying it actually works

`tests/test_yolo_on_cctv.py` runs the real model over the real clip this
deployment serves (`data/videos/traffic/traffic_live.mp4`) and checks what
matters: that objects are found in a street scene, that every class is in the
canonical vocabulary, that boxes stay inside the frame, that detection IDs are
stable so a replayed frame collapses instead of inflating counts, and that
stock COCO weights never claim an `auto-rickshaw`.

```bash
pytest tests/test_yolo_on_cctv.py -v
```

It **skips** when the analytics extras or the weights are absent, so the base
suite stays runnable on a machine without a 2 GB CV stack. Every other
detection test uses the mock detector, which cannot tell you whether the model
works - this is the one that can.

Measured on the bundled clip (CPU, yolo11n, confidence 0.45, every 15th frame):

| Class | Detections |
|---|---|
| person | 181 |
| car | 147 |
| motorcycle | 93 |
| truck | 26 |
| bicycle | 18 |
| bus | 10 |

~187 ms/frame on CPU. No `auto-rickshaw`, as expected and as asserted.

## 2. Model weights

Weights are **not** baked into the image and are **not** downloaded
automatically. Fetching a model on first request is a surprise network call
from inside a government network; it is an explicit step here.

Three options:

**(a) Mount weights you already hold** — preferred for an air-gapped or
restricted network:

```bash
mkdir -p weights
# obtain yolo11n.pt through your own approved channel, place it in ./weights
docker compose run --rm \
  -v "$PWD/weights:/app/weights" \
  edge-worker python -m app.worker --camera SENTINEL-TRAFFIC-AHM-0001
```

**(b) Let Ultralytics download it** — fine on a developer machine:

```powershell
$env:YOLO_ALLOW_DOWNLOAD='true'
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 40
```

**(c) Run the mock detector** — no weights, no torch, no GPU:

```powershell
$env:YOLO_ENABLE='false'
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --synthetic
```

If weights are missing and download is disabled, the worker fails with an
actionable message rather than a stack trace:

```
Model weights 'yolo11n.pt' were not found in '/app/weights' and automatic
download is disabled.
Either:
  1. place yolo11n.pt in /app/weights, or
  2. set YOLO_ALLOW_DOWNLOAD=true to let Ultralytics fetch it, or
  3. set YOLO_ENABLE=false to run the mock detector.
```

---

## 3. CPU / GPU

`YOLO_DEVICE` accepts `auto` (default), `cpu`, `cuda:0`, `mps`.

`auto` probes in this order: CUDA → Apple MPS → CPU. If torch is absent or
misconfigured the probe fails safely to CPU rather than raising.

**CPU-only install** (much smaller — no CUDA userspace):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r services/edge-worker/requirements-yolo.txt
```

**CUDA 12 install:**

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -r services/edge-worker/requirements-yolo.txt
```

Install torch **first** from the right index, otherwise Ultralytics pulls the
default wheel and you may get ~2 GB of CUDA userspace you cannot use.

The Docker image has two targets:

```bash
docker build --target base -f services/edge-worker/Dockerfile .   # ~120 MB, mock only
docker build --target yolo -f services/edge-worker/Dockerfile .   # ~2.5 GB, real inference
```

The demo Compose stack uses `base` deliberately.

---

## 4. Detection classes

Canonical vocabulary accepted by the central API:

```
person · car · motorcycle · bus · truck · auto-rickshaw · bicycle
```

**Honest limitation:** a stock COCO-pretrained YOLO knows `person`, `bicycle`,
`car`, `motorcycle`, `bus` and `truck`. It does **not** know `auto-rickshaw` —
COCO has no such class, and a stock model will never emit one. It stays in the
vocabulary because Indian traffic needs it; producing it requires a fine-tuned
model trained on local data.

`GET /api/v1/detector/health` and `UltralyticsYoloDetector.describe()` both
report `producible_classes` and `unproducible_classes` so this is visible at
runtime rather than being a surprise in a demo.

COCO classes outside the vocabulary (traffic light, stop sign, …) are
**discarded**, never coerced into the nearest-looking canonical class.

---

## 5. Confidence thresholds

| Variable | Default | Notes |
|---|---|---|
| `YOLO_CONFIDENCE_THRESHOLD` | `0.45` | Below this, detections are dropped at the edge and never sent |
| `YOLO_FRAME_SAMPLE_INTERVAL` | `5` | Process every Nth frame |

Sampling is not an optimisation detail — a 25 fps feed is 90,000 frames an
hour, and running all of them buys very little for object counting at 25× the
compute. Every detection records the interval it was produced under.

0.45 is a starting point, not a calibrated value. Tune per site: a camera
looking down a bright arterial road and one under a flyover do not share a
sensible threshold.

---

## 6. Low-light routing

`FrameQualityRouter` classifies every sampled frame before inference:

| Class | Action |
|---|---|
| `normal` | original frame → detector |
| `low_light` | CLAHE on the L channel → detector, flagged, provenance recorded |
| `overexposed` | **original** frame → detector, flagged |
| `blurred` | flagged; inference skipped when variance is very low |

**Classification order matters, and not in the obvious way.** Both darkness and
clipping depress edge variance for reasons that have nothing to do with focus.
Checking blur first therefore mislabels every dark frame and every glared frame
as "blurred" — and routes them away from the enhancement that might have helped.
Exposure is classified first; the focus measure is only trusted on a frame whose
exposure is in a usable range. This was found by testing, not assumed:

```
dark frame    luma= 27.7  var=38.1  →  low_light    (not "blurred")
bright frame  luma=252.0  var= 0.0  →  overexposed  (not "blurred")
flat frame    luma=128.0  var= 0.0  →  blurred      (correctly)
```

**What enhancement cannot do.** Lifting a dark frame recovers contrast that is
present but compressed — that genuinely helps. It does **not** create
information that was never captured. Where highlights are clipped to pure white,
or a plate is a saturated smear under direct headlight glare, the pixels carry
no recoverable signal, and an "enhanced" version is the algorithm inventing
plausible texture. That is worse than useless as evidence. Overexposed frames
are therefore detected on the original and flagged.

Every detection carries `frame_quality`, and enhanced frames additionally
record `enhancement_applied`, so a reviewer can always tell whether a result
came from original or processed pixels.

Tunable thresholds (`FrameQualityRouter.__init__`): `low_light_luma=60`,
`overexposed_luma=200`, `clipped_highlight_ratio=0.18`, `blur_variance=40`,
`skip_blur_variance=12`.

---

## 7. Running the worker

All four go through the launcher, which settles the working directory, the
interpreter and the environment (see the warning above).

```powershell
# offline, no central API, no CV stack
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --synthetic --dry-run

# synthetic frames, real ingestion into a running central API
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --synthetic --max-frames 20

# a real local clip with real inference
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --clip dataideos	raffic	raffic_live.mp4 --source-mode demo_local

# authorized live session (opens, decodes and revokes a real video session)
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --source-mode authorized_edge
```

The same arguments work with `./scripts/edge-worker.sh` on macOS and Linux.

The worker signs in as `traffic.ai` and is subject to **exactly** the same
authorisation as a human: an `ai_operator` outside the camera's department or
city is refused, and the refusal is audited. It is not a privileged back door.

`--source-mode` is recorded on every detection (`authorized_edge`,
`demo_local`, `mock`) so provenance is unambiguous. Anything other than
`authorized_edge` is stored with `is_demo_data=true`.

---

## 8. Performance measurement

Every detection carries `inference_latency_ms`, and the worker reports
throughput on completion:

```
done: 6 frames processed, 0 skipped, 24 detections in 0.3s (19.1 fps)
```

Indicative figures — measure on your own hardware, these are not benchmarks:

| Setup | Model | Approx. per frame |
|---|---|---|
| Mock detector | — | < 0.1 ms |
| CPU (modern x86) | yolo11n | 40–120 ms |
| CUDA (mid-range) | yolo11n | 5–15 ms |
| CUDA (mid-range) | yolo11m | 20–40 ms |

To measure properly:

```powershell
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --clip <clip> --max-frames 200 --dry-run
```

`--dry-run` isolates inference cost from ingestion and network time.

---

## 9. Scope

**In:** generic object detection, frame-quality routing, detection metadata
ingestion, provenance, per-camera and per-class query.

**Out, deliberately, and not a small extension of the above:** ANPR / OCR /
plate reading, face recognition or any biometric identification, vehicle
make / model / colour, vehicle re-identification, registration (VAHAN /
Dharmik) lookup, cross-camera identity association, watchlist matching, any
automatic enforcement action. Each needs its own legal basis, its own review
and its own data protections.

**Detections are probabilistic.** `confidence` is a model score, not a
guarantee. It degrades in low light, glare, occlusion, rain and motion blur.
`GET /api/v1/detector/health` returns this disclaimer in its payload so no
consumer has to infer it.

---

## Running continuously, across many cameras

> **Run it through the launcher.** The worker imports its own `app` package, so
> a bare `python -m app.worker` from the repository root fails with
> `No module named 'app'` — it only resolves from inside
> `services/edge-worker`, and only with the project venv, which is where
> torch, ultralytics and OpenCV live. The launcher settles the directory, the
> interpreter, `CENTRAL_API_URL` and `YOLO_WEIGHTS_DIR`, and passes every other
> argument through unchanged.

Windows PowerShell:

```powershell
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 40
```

macOS / Linux:

```bash
./scripts/edge-worker.sh --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 40
```

Several cameras on one worker, cycling until stopped:

```powershell
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --camera SENTINEL-TRAFFIC-AHM-0002 --forever --cycle-seconds 60
```

Every camera this worker's account may watch, re-checked each cycle so a
camera commissioned this morning joins without a redeploy:

```powershell
.\scripts\edge-worker.ps1 --all-cameras --forever
```

If the weights are absent the launcher says so and starts the mock detector,
rather than failing at model load.

Inside the container the bare form is correct, because `WORKDIR` is already
the worker directory:

```bash
docker compose run --rm edge-worker python -m app.worker --all-cameras --forever
```

### What scales, and how

The model is loaded **once per process** and reused across every camera and
every cycle. Loading weights costs seconds and hundreds of megabytes;
inference costs ~190 ms a frame on CPU. So the cost of adding a camera to an
existing worker is small, and one process comfortably covers the handful of
cameras at a site.

Past that, scale **horizontally: one worker per site or per device.** That is
not a limitation being worked around, it is the shape of the problem — the
compute belongs next to the camera, and shipping frames to a central GPU farm
would undo the reason inference is at the edge at all.

Three properties make horizontal scaling safe:

| Property | Why it matters |
|---|---|
| Ingest is stateless | Workers need no coordination and no shared lock. |
| Detection IDs are deterministic | Built from camera + instant + class + box. Two workers overlapping on a camera produce *duplicates*, which collapse on ingest — wasteful, never corrupting. |
| One camera's failure is contained | A suspended camera, a revoked grant or a dead NVR is logged and the cycle continues to the next camera. |

`--all-cameras` filters on the per-camera video decision, so discovery is
convenience and never escalation: a worker signed in as `traffic.ai` (Traffic
Police) discovers one camera where `system.admin` discovers two.

### Live feeds

The worker opens an authorised video session through the broker — the same
permission check, the same audit entry and the same opaque URL a human
operator gets — and decodes it. An edge worker can never reach footage a human
in its position could not.

For a real RTSP or HLS camera, `iter_session_frames` becomes a direct capture
against the device and nothing else in the loop changes: sampling, frame
quality routing, the confidence floor, batching and ingest are all source
agnostic.
