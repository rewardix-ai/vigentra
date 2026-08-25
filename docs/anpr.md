# ANPR — number-plate reading

Read this before enabling it. A registration number identifies a vehicle and,
through the registry, a person. It is the most sensitive thing this platform
produces.

ANPR was **not** in the original scope. It was added on an explicit decision,
and the controls below exist because that decision was made knowingly rather
than by drift.

---

## 1. What it does

For each **vehicle** detection (car, motorcycle, bus, truck, auto-rickshaw),
the edge worker crops the lower part of the vehicle box — where a plate sits —
upscales it, and runs OCR. A result that parses as an Indian registration
number is attached to the detection. Everything else is thrown away.

A person is never cropped or read.

## 2. What it does not do

- It does not join a plate to the vehicle reference registry.
- It does not follow a vehicle between cameras.
- It does not trigger any enforcement action.
- It does not read faces, and it never sends a frame anywhere.

A plate here is one observation at one camera at one instant. That is the whole
of it.

## 3. Turning it on

Requires the analytics extras first (`docs/yolo-setup.md`), then:

```bash
pip install -r services/edge-worker/requirements-anpr.txt
```

EasyOCR downloads two models on first use. **On a network that intercepts TLS
that download fails**, and you must place them yourself in `~/.EasyOCR/model/`:

| File | Source |
|---|---|
| `craft_mlt_25k.pth` | `https://github.com/JaidedAI/EasyOCR/releases/download/pre-v1.1.6/craft_mlt_25k.zip` |
| `english_g2.pth` | `https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/english_g2.zip` |

Then run the worker with ANPR on:

```powershell
$env:ANPR_ENABLE='true'
.\scripts\edge-worker.ps1 --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 40
```

If the models are missing the worker logs one actionable error and **carries on
producing detections without plates** — ANPR failing is not a reason to stop
counting vehicles.

| Variable | Default | Meaning |
|---|---|---|
| `ANPR_ENABLE` | `false` | Off unless set. |
| `ANPR_MIN_CONFIDENCE` | `0.55` | OCR score below this is discarded. See §7. |
| `ANPR_LANGS` | `en` | EasyOCR language packs. |
| `ANPR_PLATE_RETENTION_DAYS` | `30` | How long a plate is disclosed for. |

## 4. Why the reads are strict

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

An operator acting on `GJ01AB1Z34` because OCR misread one digit is the failure
mode worth engineering against. A missing plate costs a lookup; a wrong one
costs someone a knock on the door.

## 5. Who may read one

`plate:read`. Held by:

| Role | Rationale |
|---|---|
| `traffic_operator` | Enforcement and incident follow-up — the reason ANPR exists. |
| `department_admin` | Departmental oversight. |
| `system_admin` | Operates the platform. |
| `auditor` | Cannot audit disclosure without seeing what was disclosed. |

Deliberately **not** held by `ai_operator` — it ingests plates and has no
business reading them back — nor by `municipal_operator`, `state_registry_viewer`
or `health_monitor`. Civic monitoring counts vehicles; it does not identify
their owners.

An account without the permission still receives the detection, with
`plate_withheld: true`. The row is not refused; the identifying field is.

## 6. Retention and audit

Plates are retained for `ANPR_PLATE_RETENTION_DAYS` (default 30), **enforced on
read** as well as by any purge job — a late or failed purge cannot quietly
extend how long identifying data stays available.

Every disclosure writes a `plate_data_viewed` entry with the count disclosed.
It fires only when a plate was actually returned, so the line means something.

## 7. Accuracy, measured

Run against the bundled 4K street clip (`126434-735976920.mp4`), 12 sampled
frames, 67 vehicle crops examined:

| | |
|---|---|
| Plates accepted | **1** — `DL1LCE5987`, OCR confidence 0.77 |
| Vehicles examined | 67 |
| Time | ~2.5 s per vehicle crop, CPU |

One plate from 67 vehicles is the honest yield on wide street footage where
most vehicles are distant or side-on. **This is not a fault to tune away** —
it is what the optics allow. A camera positioned for ANPR (near-side approach,
plate filling a meaningful fraction of frame) performs completely differently
from a general-purpose overview camera.

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
