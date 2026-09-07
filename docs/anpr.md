# ANPR — number-plate reading

Read this before enabling it. A registration number identifies a vehicle and,
through the registry, a person. It is the most sensitive thing this platform
produces.

ANPR was **not** in the original scope. It was added on an explicit decision,
and the controls below exist because that decision was made knowingly rather
than by drift. The same is true of what a plate now feeds: watchlist matching
and cross-camera movement history, both set out in
[`docs/access-model.md`](access-model.md) §9.

---

## 1. Two readers, and why

There are two implementations behind one switch. They are not interchangeable,
and the difference is the whole point of this section.

### The consensus engine (`services/edge-worker/anpr/`) — preferred

Stateful per camera. It tracks each vehicle, detects the plate on every frame
that vehicle appears in, restores each crop through whichever branches the
defects call for (perspective rectification, CLAHE, a low-light branch, a glare
branch, ×4 super-resolution for small plates), reads each variant
independently, repairs each reading against the Indian plate grammar, and then
**votes across every frame of the pass**.

The two layers doing the real work are the grammar engine and the vote, not the
OCR model:

- **Grammar.** Indian plates have rigid formats, so the *position* of a
  character tells you whether it must be a letter or a digit. `GJ05JV34S6` is
  unambiguously `GJ05JV3456`, because slot 8 cannot hold a letter. Repair is a
  bounded search over one-to-many confusion sets constrained by the format and
  the real state-code list, picking the cheapest fix.
- **Consensus.** A plate is emitted once per vehicle, with the number of frames
  that agreed. That count travels all the way to the operator's screen, because
  one frame is a guess and twelve frames agreeing is a reading, and an operator
  is entitled to know which they are looking at.

There is a soft **Gujarat prior**: readings landing on `GJ` win ties against an
equally cheap repair onto another state. Plates from every other state still
validate normally. Change it in the engine's `config.yaml` under
`region.preferred_states`.

### The single-frame reader (`app/plates.py`) — fallback

Reads OCR off one crop from one frame and keeps the answer if it parses. It is
used only when the consensus engine cannot be built — a missing wheel, an
unsupported interpreter, absent weights. It works, and it is measurably worse:
see §7. A worker that reads no plates at all is worse still, which is why it
stays.

`build_engine()` returns `None` rather than raising when the extras are
missing, the worker logs one actionable line, and detection continues without
plates. ANPR failing is never a reason to stop counting vehicles.

## 2. What it does

For each **vehicle** the engine tracks, it reads the plate and, once the track
has voted, attaches the result to a detection. A person is never cropped or
read. Everything that does not parse as an Indian registration is thrown away
at the edge and never transmitted.

A plate that reaches the central API becomes a **sighting**, which may raise a
**watchlist alert** and may appear on a **movement history**. That chain is in
scope, deliberately, and is governed by its own permissions — see
[`docs/access-model.md`](access-model.md) §9.

## 3. What it does not do

- It does not join a plate to the vehicle reference registry.
- It does not identify a vehicle by appearance — colour, make, model, shape.
  A track is built from plate reads alone.
- It does not trigger any enforcement action.
- It does not read faces, and it never sends a frame anywhere.

## 4. Turning it on

Requires the analytics extras first ([`docs/yolo-setup.md`](yolo-setup.md)),
then:

```bash
pip install -r services/edge-worker/requirements-anpr.txt
```

Python **3.12** is required for the consensus path. PaddlePaddle publishes no
3.13/3.14 wheels and the CUDA PyTorch builds lag new interpreter releases; on a
newer interpreter the install falls back to the single-frame reader.

Place the weights under `services/edge-worker/models/` (or point
`ANPR_MODELS_DIR` elsewhere):

| File | What it is |
|---|---|
| `plate_detector.pt` | plate-finetuned YOLO11 |
| `yolov8n.pt` | vehicle detector — ultralytics downloads this itself if absent |
| `ESPCN_x4.pb` | ×4 super-resolution for plates under ~140 px wide |

Then run the worker with ANPR on:

```powershell
$env:ANPR_ENABLE='true'
.\scripts\edge-worker.ps1 --camera VIGENTRA-TRAFFIC-AHM-0001 --max-frames 40
```

| Variable | Default | Meaning |
|---|---|---|
| `ANPR_ENABLE` | `false` | Off unless set. |
| `ANPR_MIN_SCORE` | `0.55` | Consensus score floor. See §7. |
| `ANPR_EMIT_UNCONFIRMED` | `false` | Ship readings the vote has not settled. Turning this on reintroduces single-frame behaviour through the side door. |
| `ANPR_MODELS_DIR` | `services/edge-worker/models` | Where the weights live. |
| `ANPR_CONFIG` | `services/edge-worker/config.yaml` | Engine tuning overrides. |
| `ANPR_MIN_CONFIDENCE` | `0.55` | Fallback reader only. |
| `ANPR_PLATE_RETENTION_DAYS` | `30` | How long a plate is disclosed for. |

## 5. Why the reads are strict

OCR on a plate 60 pixels wide is unreliable. The pipeline is built so that a
plate in this system is **either right or absent**, never a plausible guess:

- The string is segmented as *state / district / series / number* and each
  segment is coerced to the type its position demands. A blanket
  character-repair turns the district `01` into the letters `OI`.
- The two-letter state code is checked against the real list of Indian state
  and UT codes. This is what stops OCR's `OJ` or `6J` being stored as though it
  were a registration.
- Anything that still fails the format is dropped at the edge and never
  transmitted.
- The central API drops it a second time: a sighting is only created for a
  plate that is still plausible after normalisation there.

An operator acting on `GJ01AB1Z34` because OCR misread one digit is the failure
mode worth engineering against. A missing plate costs a lookup; a wrong one
costs someone a knock on the door.

## 6. Who may read one

`plate:read`. Held by:

| Role | Rationale |
|---|---|
| `traffic_operator` | Enforcement and incident follow-up — the reason ANPR exists. |
| `department_admin` | Departmental oversight. |
| `grid_operator` | Watches the sandbox grid, several of whose cameras are red-light-violation units positioned for ANPR. |
| `system_admin` | Operates the platform. |
| `auditor` | Cannot audit disclosure without seeing what was disclosed. |

Deliberately **not** held by `ai_operator` — it ingests plates and has no
business reading them back — nor by `municipal_operator`,
`state_registry_viewer` or `health_monitor`. Civic monitoring counts vehicles;
it does not identify their owners.

An account without the permission still receives the detection, and still sees
that an alert fired, with `plate_withheld: true`. The row is not refused; the
identifying field is.

## 7. Retention and audit

Plates are retained for `ANPR_PLATE_RETENTION_DAYS` (default 30), **enforced on
read** as well as by any purge job — a late or failed purge cannot quietly
extend how long identifying data stays available.

Every disclosure writes a `plate_data_viewed` entry with the count disclosed.
It fires only when a plate was actually returned, so the line means something.
Tracing a vehicle writes a separate `vehicle_movement_viewed` entry carrying
the operator's stated reason.

## 8. Accuracy, measured

### The fallback reader

Run against the bundled 4K street clip (`126434-735976920.mp4`), 12 sampled
frames, 67 vehicle crops examined:

| | |
|---|---|
| Plates accepted | **1** — `DL1LCE5987`, OCR confidence 0.77 |
| Vehicles examined | 67 |
| Time | ~2.5 s per vehicle crop, CPU |

One plate from 67 vehicles is the honest yield **for a single-frame reader**
on wide street footage where most vehicles are distant or side-on. Much of it
is what the optics allow — but not all of it, which is exactly why the
consensus engine exists: a vehicle that is unreadable in thirty frames and
legible in three is lost entirely by a reader that only ever looks once.

A camera positioned for ANPR — near-side approach, plate filling a meaningful
fraction of the frame — performs completely differently from a general-purpose
overview camera. Several of the sandbox grid's cameras are red-light-violation
units and are in the first category; most are in the second.

### The consensus engine

Not yet measured on the government feed. The engine's own per-track voting and
grammar repair are covered by `tests/test_anpr_engine.py` and by the upstream
project's `tests/test_consensus.py` and `tests/test_plate_rules.py`, but the
number that matters — yield per vehicle on the grid's own footage — needs a run
against the live cameras with the weights installed, and that has not been done
yet. **Do not quote a figure for it until it has.** The fallback's 1-in-67 is
the only measured number here, and it measures the reader we are trying not to
use.

### Two findings worth keeping

**OCR returns a plate in pieces.** The first real read came back as two boxes,
`DL` and `1LCE5987`. Judged separately neither is a registration and the plate
was lost entirely. Fragments on a shared text line are now joined left-to-right
before parsing — see `assemble_lines`.

**Format validation cannot catch a confident misread.** The same car one second
later read as `DL11CES9871` at confidence 0.49: eleven characters, valid state
code, correct shape — and wrong. Nothing structural rejects it. That is why the
default floor is **0.55** rather than 0.40, set above where misreads were
actually observed rather than at a number that looked reasonable.

### Still true

Night, glare, motion blur, occlusion and oblique angles all degrade this
sharply, and the frame-quality router already skips frames too degraded to mean
anything.

Do not present a plate read as identification. It is a probabilistic reading of
a photograph; the confidence score is a model score, not a guarantee. If a plate
matters to a case, a human should look at the frame.

---

## 9. Why a camera reads nothing

Silence used to be ambiguous. A camera reporting no plates might have seen no
traffic, or might have seen two hundred vehicles whose plates were forty pixels
wide — opposite situations with opposite fixes, and the platform could not tell
them apart.

Every vehicle that leaves the frame now settles into one of three outcomes
(`services/edge-worker/anpr/readability.py`):

| Verdict | Meaning | Whose problem |
| --- | --- | --- |
| `CONFIRMED` | the vote settled and the restorations agreed | — |
| `UNCERTAIN` | the plate was big enough to read; the readings disagreed | this vehicle: blur, angle, glare |
| `UNREADABLE` | the plate never reached a size any recogniser resolves | this camera's placement |

Only `UNREADABLE` is a statement about the camera. It is the honest answer on a
wide junction view, and it is a siting finding rather than a software defect.

The size bands are shared with `tools/diagnose_cameras.py` so the suitability
survey and the running pipeline cannot drift apart:

| Band | Plate width | Behaviour |
| --- | --- | --- |
| `COMFORTABLE` | ≥160 px | reads off the unmodified crop |
| `READABLE` | ≥120 px | restoration recovers the read |
| `MARGINAL` | ≥80 px | reads only when several restorations agree |
| `SUB_MARGINAL` | ≥40 px | attempted; agreement decides |
| `UNREADABLE` | <40 px | not attempted |

**The gate sits at 40 px and must not be raised.** Plates on this estate have
been read correctly by eye at 53–90 px, and no width separates the legible ones
from the illegible: a 71 px motion-blurred plate is unreadable while a 53 px
sharp one is not. Raising the floor discards real evidence; a 100 px floor
confirms nothing anywhere. Agreement across restorations — not size — is what
separates a real read from an invented one, and that is already what
`consensus.py` requires.

Lowering the gate below 40 px is worse still. Undersized crops pushed through
the recogniser do not come back as near misses, they come back as fabrications:
`GJ06D02415` read as `LD607415`, `GJ01MR4873` as `GI667673`. A fabricated
registration is a wrong vehicle attached to a real place and time.

## 10. Timings

`anpr/metrics.py` reports P50/P95/P99 per stage — detect, assess, enhance, ocr,
and the whole frame — and the worker logs them at the end of each run.

Percentiles rather than an average, because the average is the summary that
hides the failure. Ninety-nine frames at 40 ms and one at 900 ms average to
48 ms and look healthy, while the 900 ms frame is the one a viewer sees as a
freeze. Read `frame.p99`: a 25 fps feed needs it under 40 ms to never fall
behind.

`capacity_fps` is the rate this machine could sustain if it never waited for
anything; comparing it against `fps` separates "the box is too slow" from "the
network delivered frames slowly".

## 11. Engines are cached per camera

A worker cycling its cameras used to build an ANPR engine per camera per pass,
reloading hundreds of megabytes of weights and discarding everything the camera
had learned about itself — where its burned-in clock sits, its prevailing
direction of travel, the plate sizes it delivers. A 25-frame pass barely
reaches those thresholds once.

`EngineCache` keeps one engine per camera between cycles. A reused engine is
told the stream restarted (`new_stream`), so track ids never cross a cycle
boundary and an old plate can never attach to a new vehicle. The cache is
bounded by `ANPR_ENGINE_CACHE` (default 4) because engines are heavy; with more
cameras than that in rotation every lookup misses and behaviour degrades to
what it was before — correct, just no faster.
