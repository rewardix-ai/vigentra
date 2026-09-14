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

### The track-level engine (`services/edge-worker/anpr/`) — preferred

Stateful per camera. It tracks each vehicle, detects the plate on every frame
that vehicle appears in, banks the crops with their quality, and produces a
verdict **when the track closes** — restoring the banked crops (rectify,
register, fuse, glare, denoise, deblur, deskew, binarise), reading every
variant independently, repairing each reading against the Indian plate grammar,
and voting across the whole pass.

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
validate normally. Change it in `config/thresholds.yaml` under
`reading.preferred_state`.

The chain and its thresholds are documented in
[`anpr-reading.md`](anpr-reading.md).

### When the engine cannot be built

`build_engine()` returns `None` rather than raising when the extras are
missing, the worker logs one actionable line, and detection continues without
plates. ANPR failing is never a reason to stop counting vehicles.

There is no second reader to fall back to. The earlier single-frame EasyOCR
reader was removed: `easyocr` was never installed in any image, so it could
not run.

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

ANPR ships as its own image stage, on top of the analytics stage
([`docs/yolo-setup.md`](yolo-setup.md)):

```bash
docker compose --profile anpr build edge-worker
```

The stage installs `services/edge-worker/requirements-anpr.txt` and adds
`services/edge-worker/config/`. The default reader runs on ONNX Runtime; the
optional PaddleOCR and Claude readers are imported lazily and are deliberately
not dependencies of the image (see [`anpr-reading.md`](anpr-reading.md)).

Place the weights under `services/edge-worker/models/` (or point
`ANPR_MODELS_DIR` elsewhere). They are not committed — `.gitignore` excludes
`*.pt` and `*.onnx` — and `models/PROVENANCE.json` records where each came
from:

| File | What it is |
|---|---|
| `yolo11s.pt` | vehicle detector |
| `plate_det_mix_n.pt` | plate detector |
| `reader_crnn.onnx` | CRNN-CTC plate reader |
| `reader_crnn_v6.onnx` | second reader (`reading.extra_crnn_weights`): fills in a read, never a confirm, on tracks the first cannot decide. Used when present. |

Then run a worker with ANPR on (`ANPR_ENABLE=true` in its environment).

| Variable | Default | Meaning |
|---|---|---|
| `ANPR_ENABLE` | `false` | Off unless set. |
| `ANPR_EMIT_UNCONFIRMED` | `false` | Ship readings the vote has not settled. Turning this on reintroduces single-frame behaviour through the side door. |
| `ANPR_REVIEW_SCORE` | `0.35` | Below the floor but worth a human look. |
| `ANPR_MODELS_DIR` | `services/edge-worker/models` | Where the weights live. |
| `ANPR_CONFIG_DIR` | `services/edge-worker/config` | `thresholds.yaml` and `roi.yaml`. |
| `ANPR_VEHICLE_WEIGHTS` | `yolo11s.pt` | Vehicle detector filename. |
| `ANPR_PLATE_WEIGHTS` | `plate_det_mix_n.pt` | Plate detector filename. |
| `ANPR_READER_WEIGHTS` | `reader_crnn.onnx` | Reader filename. |
| `ANPR_ENGINE_CACHE` | `4` | Engines kept between camera cycles. See §11. |
| `ANPR_RECORD_TAIL` | `500` | Settled track records retained per engine. |
| `ANPR_PLATE_RETENTION_DAYS` | `30` | How long a plate is disclosed for. |

Per-camera tuning lives in `config/thresholds.yaml`, not in environment
variables; read the provenance comments in that file before changing a number.

## 5. Why the reads are strict

OCR on a plate 60 pixels wide is unreliable. The pipeline is built so that a
plate in this system is **either right or absent**, never a plausible guess:

- The string is segmented as *state / district / series / number* and each
  segment is coerced to the type its position demands. A blanket
  character-repair turns the district `01` into the letters `OI`.
- The two-letter state code is checked against the real list of Indian state
  and UT codes. This is what stops OCR's `OJ` or `6J` being stored as though it
  were a registration.
- A crop whose district does not exist for its state is barred from voting.
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

### The previous single-frame reader

Measured before it was removed, on the bundled 4K street clip
(`126434-735976920.mp4`), 12 sampled frames, 67 vehicle crops examined:

| | |
|---|---|
| Plates accepted | **1** — `DL1LCE5987`, OCR confidence 0.77 |
| Vehicles examined | 67 |
| Time | ~2.5 s per vehicle crop, CPU |

One plate from 67 vehicles is the honest yield **for a single-frame reader**
on wide street footage where most vehicles are distant or side-on. Much of it
is what the optics allow — but not all of it, which is exactly why the
track-level engine exists: a vehicle that is unreadable in thirty frames and
legible in three is lost entirely by a reader that only ever looks once.

A camera positioned for ANPR — near-side approach, plate filling a meaningful
fraction of the frame — performs completely differently from a general-purpose
overview camera. Several of the sandbox grid's cameras are red-light-violation
units and are in the first category; most are in the second.

### The track-level engine

It has run on the government feed since 2026-09-12. What it read is in the
ANPR report, which lists every reading with its timestamp and confidence. Set
`REPORT_PASSWORD` for a reporting account, then:

    python scripts/anpr_report.py --since-hours 72

Most readings on these wide junction views are low-confidence because the plates
are narrow in pixels: the vendor's measurement behind
`confidence.confirm_min_width_px` in `config/thresholds.yaml` reads nothing
exactly below 40 px. Quote the report, not a yield figure.

### A finding worth keeping

**Format validation cannot catch a confident misread.** With the previous
reader, the same car one second later read as `DL11CES9871` at confidence 0.49:
eleven characters, valid state code, correct shape — and wrong. Nothing
structural rejects it, which is why the track-level engine confirms a plate only
when several frames agree, and why an unconfirmed reading is shown as
unconfirmed.

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

Every vehicle that leaves the frame now settles into one of three outcomes:

| Verdict | Meaning | Whose problem |
| --- | --- | --- |
| `CONFIRMED` | the vote settled and the restorations agreed | — |
| `CANDIDATE` | the plate was big enough to read; the readings did not settle | this vehicle: blur, angle, glare |
| `UNREADABLE` | the plate never reached a size any recogniser resolves | this camera's placement |

Only `UNREADABLE` is a statement about the camera. It is the honest answer on a
wide junction view, and it is a siting finding rather than a software defect.
A `CANDIDATE` is never presented as a reading; `ANPR_EMIT_UNCONFIRMED` exists
to make that behaviour explicit rather than accidental.

The legibility gate and the confirm floors live in `config/thresholds.yaml`
(`legibility_gate`, `confidence.confirm_min_width_px`).

**The floor is a measurement, not a preference, and must not be raised
casually.** Plates on this estate have been read correctly by eye at 53–90 px,
and no single width separates the legible from the illegible: a 71 px
motion-blurred plate is unreadable while a 53 px sharp one is not. Raising the
floor discards real evidence. Agreement across restorations — not size — is
what separates a real read from an invented one, and that is what the vote
requires.

Lowering the gate is worse still. Undersized crops pushed through the
recogniser do not come back as near misses, they come back as fabrications:
`GJ06D02415` read as `LD607415`, `GJ01MR4873` as `GI667673`. A fabricated
registration is a wrong vehicle attached to a real place and time.

## 10. Timings

The worker logs per-camera pass timings, and the engine records per-track
frame counts with every settled reading.

Percentiles rather than an average, because the average is the summary that
hides the failure. Ninety-nine frames at 40 ms and one at 900 ms average to
48 ms and look healthy, while the 900 ms frame is the one a viewer sees as a
freeze. A 25 fps feed needs its p99 under 40 ms to never fall behind.

## 11. Engines are cached per camera

A worker cycling its cameras used to build an ANPR engine per camera per pass,
reloading hundreds of megabytes of weights and discarding everything the camera
had learned about itself. A short pass barely reaches those thresholds once.

`EngineCache` keeps one engine per camera between cycles. A reused engine is
told the stream restarted (`new_stream`), so track ids never cross a cycle
boundary and an old plate can never attach to a new vehicle. The cache is
bounded by `ANPR_ENGINE_CACHE` (default 4) because engines are heavy; with more
cameras than that in rotation every lookup misses and behaviour degrades to
what it was before — correct, just no faster.
