# Vigentra: the technical choices, and why

For preparing the keynote's questions. Each layer of the system covers what it runs on, the
settings that matter, why we chose it and what it does better than the obvious alternative. Numbers
come from the project's own reports, cited by file. Estimates and plans are marked as such.
`EW/` is `services/edge-worker/`.

## The stack at a glance

| Layer | What we use | Why, in one line |
|---|---|---|
| Integration | An adapter per department system; one camera model | Departments keep their own VMS. A new vendor is one adapter, and nothing downstream changes |
| Capture | FFmpeg through OpenCV, RTSP over TCP | Reads the streams cameras already publish; TCP loses no packets, so frames are not smeared |
| Vehicles | YOLO11s + ByteTrack | One identity per vehicle, so its plate is decided once, from all its frames |
| Plates | YOLO11n plate detector, run inside each vehicle box | Small search area: small plates get found, and hoardings outside vehicles never do |
| Reading | Two CRNN-CTC readers on ONNX Runtime, plus a PaddleOCR PP-OCRv5 text reader | Different kinds of reader make different mistakes, so when they agree it counts as evidence |
| Deciding | Votes across frames, Indian plate rules, a check for character shapes in the pixels | A plate is either right or absent, never a guess: 0 wrong among 48 legible plates |
| Central | FastAPI, SQLAlchemy 2 (async), PostgreSQL | No computer vision and no camera passwords at the centre, so it is small, safe and runs anywhere |
| Console | Next.js 14, React 18, Tailwind, Leaflet + OpenStreetMap | The session token never reaches page scripts; no map licence or API key |
| Delivery | Docker Compose; the edge image in three sizes | One command starts the whole stack; a site installs only what it runs |
| Proof | 338 test functions; a replayable ANPR benchmark | Every change is measured on the same clips before it is kept |

## 1. Architecture: process the video where it lives

**What.** We use reference Model 1 + Model 3, with Model 2's console on top ([HLD §2](../../docs/hld.md)):
- a camera registry and map;
- an adapter per department system;
- video analysis at the edge, next to the video;
- one console.

We rejected Model 4, a central VMS.

**Why.**
- **Network.** Streaming 80,000 cameras to a centre is about 2 Mbps each: about 160 Gbps sustained and 1.7 PB a day.
  - Vigentra moves detection rows instead: about 48 kbps for a busy camera at peak.
  - For the whole state at a realistic mix that is about 600 Mbps–1 Gbps, 40–250× less ([scalability §3](../../docs/scalability.md)).
- **Departments keep their systems.** Nothing is installed on a departmental VMS. The first department to join gets value that day, and the 26th changes nothing for the first.
- **Frames never leave the edge.** central-api has no computer-vision dependency at all. A compromise of it yields no imagery, and it is small enough to deploy anywhere.

**Better than** a central VMS. That design costs the network above, concentrates every camera
credential in one place, and makes statewide viewing the default.

**Adapters.** There is one adapter per vendor, behind one abstract class ([adapter contract](../../docs/adapter-contract.md)). We proved it on three genuinely different dialects:

| Source | Field names | Timestamps | Sign-in | Notes |
|---|---|---|---|---|
| Traffic Police VMS | flat snake_case | IST strings | `X-API-Key` | |
| Municipal VMS | nested camelCase | epoch milliseconds | Bearer token | |
| Sentinel grid | flat, minimal | none | none (public sandbox) | RTSP / WHEP / HLS, live only |

Above the adapter boundary, nothing can tell them apart. Four further properties:
- **Read-only sources are read-only in code.** The grid adapter refuses every write before a request is made.
- **Capability is checked twice.** The grid has no recordings, and both the camera record and the video adapter refuse playback.
- **An outage stays local.** One department going down takes only its own cameras offline, with the reason shown.
- **Liveness follows the video, not the web gateway.** On 14 Sep the grid's gateway refused requests while its RTSP server still played. Without this rule, 72 of 180 minutes would have been lost.

## 2. Capture: getting frames in

Every rule is enforced in code and pinned by a test against a fake capture ([HLD §5](../../docs/hld.md)).

| Rule | Why |
|---|---|
| RTSP forced over TCP | UDP drops packets under load, and one lost packet smears the picture until the next keyframe. TCP also passes firewalls and NAT on the single RTSP port |
| Frame time taken from the stream's PTS, never arrival time | On connect the gateway replays its buffer, so the first second arrives faster than real time. Arrival times would give impossible speeds after every reconnect |
| Frame rate measured per camera, never taken as declared (`CAP_PROP_FPS` is never read) | Timing must not depend on a number the camera may report wrongly |
| Reconnect with 2 s → 30 s backoff; 25 frames of join-time decoder noise tolerated | A dead feed is never hammered, and a fresh join is not mistaken for a failure |
| Always the newest frame; frames that arrive while busy are dropped | Live means live: the reader never falls behind the camera |
| Adaptive frame stride (`AdaptiveSampler.pace()`) | Sampling follows the machine's measured speed. On an M1 at 1080p, a fixed stride of 2 dropped 941 of 1,189 frames at random and read 2 plates; self-paced, it settled on stride 12 and read 3 |
| Tracking reset when the scene jumps | A scene cut must not splice two vehicles into one plate history |
| Nothing written to disk; one capture per camera | No copies of footage at the edge, and no extra load on the camera |
| Camera list read once, by central-api | The gateway allows one session per account, so a worker reading the list would sign central-api out |
| Optional HLS fallback through the broker | A site that blocks port 8554 can still be read, over the worker's own audited session. It is slower than RTSP, so it is off by default |

## 3. Vehicles: YOLO11s + ByteTrack

**Settings** (`EW/config/thresholds.yaml`, `EW/config/bytetrack.yaml`):
- Classes: COCO car, motorcycle, bus, truck.
- Confidence: ≥ 0.15.
- Frame size: detected at the frame's own size, up to 1920 px, never enlarged.
- Duplicate boxes: removed across classes (class-agnostic NMS).
- Precision: half precision on the GPU.
- ByteTrack:
  - high / low thresholds 0.30 / 0.05;
  - new tracks start only from 0.20;
  - lost tracks kept for 45 frames.

**Why YOLO11 (Ultralytics).**
- Open weights.
- One library does detection, tracking and export to ONNX, TensorRT, OpenVINO and CoreML.
- The same code runs on a CPU, an NVIDIA GPU or Apple's GPU.
- Ultralytics reports that YOLO11m beats YOLOv8m on COCO accuracy (mAP) with 22% fewer parameters.

We use the small model (9.4 M parameters) for vehicles, which must be found far away in wide junction views. The nano model (2.6 M) is used where speed matters more: the plate detector and the plain object-detection worker.

**Why the frame's own size.** Measured, a 720p frame detected at its own 1280 px tracks about twice as many vehicles as the same frame stretched to 1920 px, and runs 1.5–9× faster. Together with dropping whole-frame brightening, this gave +330 vehicle tracks and 2.4× the speed ([optimisation, change 3](../../docs/anpr-optimisation.md)). Enlarging adds no information, only interpolated pixels.

**Why remove duplicate boxes across classes.**
- The COCO model often puts both a "car" box and a "truck" box on one Indian hatchback; on the evidence car it said truck in 49 of 80 frames.
- Keeping one box per vehicle keeps one identity in the tracker.
- A vehicle's type is then the class of its most confident detection over the whole track. The evidence car is labelled car, at 0.91.

**Why ByteTrack** (Zhang et al., ECCV 2022).
- **It uses low-confidence boxes too.** A second matching pass uses them, so a vehicle whose score dips in blur, or behind another vehicle, keeps its identity instead of being split in two. That is why the vehicle floor is 0.15: boxes between 0.15 and 0.30 can only extend an existing track, and only boxes of 0.20 or more can start one.
- **It needs no appearance network.** It matches on motion and box overlap only, so it costs almost nothing beyond the detector. That matters when one machine keeps pace with a live camera.
- **Better than DeepSORT,** which runs a re-identification network on every box.
- **Better than BoT-SORT,** Ultralytics' default tracker. BoT-SORT corrects for a moving camera with optical flow on every frame, and CCTV cameras are fixed.
- **The track is the unit of reading.** A plate is decided once per vehicle, from all its frames.

## 4. Finding the plate

- **Inside each vehicle box, not the whole frame.**
  - The vehicle crop is enlarged to at least 640 px before the plate detector runs (YOLO11n, one class, `plate_det_mix_n.pt`, confidence ≥ 0.2), so a plate a few dozen pixels wide becomes findable.
  - Text outside any vehicle (hoardings, the camera's own clock) cannot be proposed.
  - The plate inherits the vehicle's track.
- **Only where a plate can be read.** Plate search waits until a vehicle is at least 96 px wide; below that its plate is under about 40 px, and readings of plates that small are never exact.
  - Measured: the same 36 readings on the labelled Delhi clip, in 586 s instead of 878 s.
  - The time saved goes into more frames of the vehicles that can be read.
- **Shape check.** A proposed box wider than 0.60 of its vehicle is demoted. Of 323 boxes checked by eye, the 279 real plates reached 0.57 at most, while 37 of the 44 that were not plates sat above 0.60. The old limit, 0.9, let a hoarding be read as a plate.
- **On-screen text masked.** The camera's clock and name strips, static text and hoardings are never plate candidates. Text that stays put while different vehicles pass is treated as a sign, not a plate.

## 5. Choosing the crops

- **Crop bank.** Every plate crop is kept with its track, frame, timestamp and quality: size, sharpness, contrast, angle and detector score.
  - When the vehicle leaves, the best 12 of up to 64 crops are read.
  - Keeping 20 instead of 12 found no more plates (tested).
- **Quality check** (width ≥ 22 px, height ≥ 8 px, sharpness ≥ 8, contrast ≥ 25).
  - A crop below these is UNREADABLE and is never guessed at.
  - A camera that only produces such crops is a placement finding, and the system reports it as one.
- **Restoration.** Several weak looks become one stronger image:
  1. perspective correction;
  2. ECC registration, which aligns crops of the same plate to sub-pixel accuracy;
  3. fusion (a weighted median, or shift-and-add super-resolution);
  4. glare removal, denoising, deblurring, deskewing and binarising.

  A super-resolved image may vote but can never confirm a plate on its own, because super-resolution can invent detail.

## 6. Reading

| Reader | What it is | Why it is there |
|---|---|---|
| Main CRNN-CTC | `reader_crnn.onnx` (7.7 MB) on ONNX Runtime | Reads a whole line of characters without cutting it up first. Small and fast on a CPU, and ONNX Runtime runs the same file on CPU, NVIDIA, Apple (CoreML) and Intel (OpenVINO) |
| Second CRNN | `reader_crnn_v6.onnx` | Votes too. It can carry a confirmation only when another reader produced the same string and none opposes it |
| Text reader | PaddleOCR PP-OCRv5 recogniser, fine-tuned on Indian plates (`anpr/read/awiros.py`). It runs on the Mac readers and is not in the Docker image | A different family of model that reads the whole crop, two-row plates included, so its errors are independent of the CRNNs' |

- **Why two kinds of reader.** The two CRNNs share their mistakes: one CRNN's wrong last digit was "backed" by the other's. So near-agreement between them no longer counts, while a CRNN agreeing with the text reader does.
  - With the text reader: 16 of 26 legible plates on the 36-clip set.
  - Without it: 13 of 26.
  - Wrong plates: 0 either way.
- **Plate layout.** A squat plate is read both as one row and as two, and the stronger reading is kept.
- **Size floor.** Crops narrower than 24 px are not read. A 32 px floor was tested: same plates, no saving.

## 7. Deciding: never invent a plate

The accuracy comes from these decision layers, not from the OCR model alone ([anpr.md §5](../../docs/anpr.md), [anpr-reading.md](../../docs/anpr-reading.md)):

1. **Indian plate rules** (`anpr/plate_grammar.py`, `config/india_codes.yaml`).
   - A character's position says whether it must be a letter or a digit, so `GJ05JV34S6` can only be `GJ05JV3456`.
   - Repair is a bounded search over known confusions (0/O, 8/B, 6/G, …), held to the format and to the real code lists: 40 state and UT codes and 39 Gujarat RTOs.
   - Formats covered: standard, Delhi's single-digit district, BH series, diplomatic, vintage and temporary.
   - Ties prefer GJ; other states still validate.
2. **Votes.**
   - Each crop's reading votes, weighted by crop quality and reader confidence.
   - A ROVER vote then fuses the vehicle's readings. ROVER comes from speech recognition: line up several outputs and vote character by character.
3. **The confirm rule** (`thresholds.yaml`). A plate is confirmed by one of two paths.
   - **Strong vote.** All of these must hold:
     - fused confidence ≥ 0.75 over ≥ 3 frames;
     - the runner-up under half the winner;
     - the weakest character with ≥ 0.5 of its vote;
     - at least 2 single frames backing it;
     - at least 6 independent variants;
     - the best crop at least 40 px wide.
   - **Cross-reader agreement.** A CRNN and the text reader each read the same string on at least 2 crops, 6 in all.

   Confidence is calibrated (temperature scaling), so 0.75 means the same thing on every camera.
4. **Character shapes in the pixels.** The best 8 crops must show at least 3 character-shaped marks.
   - A truck windscreen was once "confirmed" because every crop of the same glass agreed on an invented string. It shows 2 marks; real plates show 5–11.
   - This check cannot tell a plate from advertising text. The on-screen-text mask and the shape check cover that.
5. **Format checked twice.** Anything implausible is dropped at the edge and never sent, then dropped again centrally.

**Why so strict.** A missing plate costs a lookup; a wrong one costs someone a knock on the door. Each
blocking rule above stops a false plate that was actually seen, and `thresholds.yaml` records the
replay behind each one. **Result:** 36 of the 48 plates a person can read, 0 wrong
(`EW/reports/anpr_benchmark/final2/REPORT.md`).

## 8. From a reading to an alert

- **Ingest.**
  - Plates reach central-api as text: plate, time, camera, confidence, and how many frames agreed.
  - Detection IDs are deterministic and ingest ignores repeats, so a retry or a replay never double-counts.
  - Measured: 164 plate reads/s through one API process, matched against the watchlist in the same transaction. That was on a laptop with SQLite, so treat it as a floor.
- **Matched as it arrives, not when someone searches.** An alert then proves the system knew at that moment, and a watchlist entry added later does not invent alerts for earlier passes.
- **Fuzzy matching, priced by real OCR errors.**
  - Costs:
    - a known confusion pair costs 0.35;
    - a missing or extra character, 0.5;
    - an unrelated substitution, 1.0.
  - The threshold is 1.0, about two plausible misreads.
  - Exact matching would miss a stolen car whose only sighting has one character wrong; plain edit distance treats every error alike.
  - Each alert says separately whether it was exact.
- **Priority, derived, never stored:**
  - **critical:** exact match, stolen or wanted;
  - **high:** exact match, other lists;
  - **review:** near match; look at the crop first.
- **Delivery.**
  - Alerts reach the console within 5 s.
  - Acknowledging, or dismissing with a reason, is audited.
  - A webhook can send each alert onward, signed with HMAC-SHA256 (`X-Vigentra-Signature`). The receiving system can check who sent it and that nothing was changed.
- **Camera health.**
  - Every camera and department system is checked every 20 s.
  - Two misses in a row (about 40 s) raise `CAMERA_OFFLINE`, which the monitor closes itself on recovery.
  - An unreachable department raises one `SOURCE_UNREACHABLE` alert, not one per camera.
- **Movement trace.**
  - Built from plate reads only, with no guessing by colour or shape, so it understates movement rather than inventing it.
  - Repeated reads at one camera collapse into one pass.
  - A leg implying over 200 km/h is flagged, not hidden.
  - Permissions are applied before the route is built.
  - Every trace needs a written reason.

## 9. Security and privacy

- **No camera credentials at the centre.** Vigentra stores no RTSP URL, NVR address or media token, so a breach of the central tier yields no key to any camera.
- **Brokered video.**
  - The browser gets a session link, never the camera's.
  - Every playlist entry is rewritten to come back through the broker, so permission is re-checked on every few-second segment. Revoking access stops a playing stream within one segment.
  - The segment reference coming from the browser is treated as hostile: no scheme, host, leading `/` or `..`. That stops the broker being used as an open proxy.
- **Least privilege.**
  - 13 roles and 25 permissions, scoped by department, city and zone.
  - The edge account that sends plates in cannot read the watchlist back.
- **Video from another unit is asked for.** Grants are personal, time-limited and revocable, and are approved by the operator who runs that unit's cameras.
- **Withheld, not refused.** An account without `plate:read` still gets the alert, with the plate withheld.
- **Retention.** It is enforced when data is read, as well as by deletion, so a failed purge cannot extend access.
- **Everything audited.** The log covers:
  - syncs, reads and footage requests;
  - grants and refusals;
  - plate disclosures and watchlist changes;
  - alerts, including those the matcher raises itself;
  - traces.

  Audit rows commit separately from the request, so a refused attempt is still on record.
- **Sessions.** The session is a signed JWT that the Next.js server keeps in an httpOnly cookie.
  - Page scripts never see it, so an injected script cannot steal it.
  - Any API replica can verify it without shared session state.
- **Tests guard it.**
  - A permission matrix checks refusals as carefully as successes.
  - A secret scanner fails the build if an RTSP URL, credential or internal hostname appears in anything sent to a browser.

## 10. Platform choices

| Choice | Why | Better than |
|---|---|---|
| Python 3.11, edge and centre | The vision tools (PyTorch, Ultralytics, OpenCV, ONNX Runtime) are Python, so the whole backend is one language | Two languages and two toolchains for no gain at this scale |
| FastAPI | Asynchronous: slow department systems, video segments and ingest batches are handled at once without blocking. Requests are validated by Pydantic, and the OpenAPI description comes from the code ([api.md](../../docs/api.md)) | A synchronous framework with validation written by hand |
| SQLAlchemy 2 (async) | Non-blocking all the way to the database. Every write goes through one mapper, so moving to PostGIS is a migration, not a redesign | SQL strings scattered through the routes |
| PostgreSQL | Transactions and constraints for the registry, permissions and audit. JSONB holds each vendor's raw record. Replication and point-in-time recovery serve the recovery targets. It grows by extensions on the same database: `pg_trgm` for fuzzy search, PostGIS for maps, TimescaleDB for detections | A document store with no constraints or joins for permissions and audit, or several databases to run |
| SQLite for the test suite | Tests run with no database server to start | Tests that need a running Postgres |
| Direct HTTP ingest, in batches | Fewest moving parts at this scale; repeat-proof IDs make retries safe | Kafka now: another cluster to run before it is needed. It is planned between ingest and matching past a few thousand cameras, and since the matcher is one function over a list of reads, that is a swap, not a rewrite |
| Next.js 14 + React 18 | Server-side route handlers forward every API call (`/api/vigentra/*`), so the browser holds no token and never learns where the API is | A browser app calling the API directly, with its token in browser storage |
| Tailwind | One consistent look across every page, quickly | |
| Leaflet + OpenStreetMap | Open-source map, open data: no API key, no per-view fees, no vendor account | A commercial map API, with its keys, billing and terms |
| Docker Compose | `docker compose up --build` brings up the whole stack the same way on any machine | Hand-installed services that drift apart |
| Edge image in stages | `base` about 120 MB (mock detector, for CI), `yolo` about 2.5 GB, `anpr` about 3.5 GB. Model weights are mounted, never built in | One 3.5 GB image everywhere, and a rebuild to change a model |
| Kubernetes, at scale (planned) | One namespace per tier, scaling on ingest queue depth rather than CPU | |

**Hardware in the demo.** The whole platform runs on one Apple M1 laptop (16 GB), covering 30 live cameras.
The ANPR readers run on the Mac itself, on its GPU, because Docker on macOS cannot reach the Apple GPU;
everything else runs in Docker. A production edge node would be Linux with an NVIDIA T4-class GPU,
where the reader runs in its container.

## 11. Numbers to quote

| Claim | Number | Source |
|---|---|---|
| Plates a person can read that Vigentra read | 36 of 48, 0 wrong | `final2/REPORT.md` |
| CAM06, midday | 15 of 16 | same |
| Delhi street, hand-held | 13 of 20 | same |
| Before → after the optimisation, same 36 clips | 9 → 16 of 26, 0 wrong both times; 5,784 s → 3,341 s | `docs/anpr-optimisation.md` |
| With / without the text reader | 16 / 13 of 26 | same |
| Speed on one Apple M1 | 720p: 96 ms a frame (1.00× live). 854×480: 58 ms (1.31×). 1080p: 173 ms (0.92×) | same |
| Plate ingest with watchlist matching | 164 reads/s per API process | `tests/test_ingest_volume.py` |
| A camera going dark noticed | about 40 s | `services/health_alerts.py` |
| Network at 80,000 cameras | about 600 Mbps–1 Gbps, against about 160 Gbps for central video | `docs/scalability.md` §3 |
| The whole platform, 30 live cameras | one Apple M1 laptop, 16 GB | `docs/scalability.md` §2 |
| Tests | 338 test functions (209 platform, 129 edge worker) | `tests/`, `EW/tests/` |

Say "estimate" for these:
- about 3,000 T4-class GPUs for 80,000 cameras, with ANPR only where the optics allow;
- about ₹90–120 crore for the edge tier;
- recovery targets RPO 5 min and RTO 1 h, which are design goals not yet tested at scale.

## 12. Tested and dropped

| Tried | What happened | Kept |
|---|---|---|
| Confirm a plate on 3 crops instead of 4 | A wrong plate was confirmed on the Delhi clip | 4 |
| Keep the best 20 crops per vehicle instead of 12 | No more plates, more reading time | 12 |
| Merge vehicle fragments by shared reading guesses | Delhi went from 11 to 9 correct | Merge only on the same reading within 15 s |
| Let the two CRNNs back each other's near misses | They share mistakes: a wrong digit backed a wrong digit | Only a different kind of reader may back |
| Enlarge frames to 1920 px before detecting | Half the vehicles tracked, 1.5–9× slower | Each frame at its own size |
| A 32 px reading floor instead of 24 | Same plates, no saving | 24 |
| The first reader: one frame at a time, EasyOCR | 1 plate from 67 vehicles at about 2.5 s a crop; a confident misread passed every format check | Replaced by reading whole tracks |
| Plate boxes allowed up to 0.9 of the vehicle's width | A hoarding was read as a plate | 0.60 |

## 13. Licences

Everything is open source: no paid licence, vendor account or API key is needed today.

| Component | Licence |
|---|---|
| Ultralytics YOLO11 (detection, tracking) | AGPL-3.0, or Ultralytics' commercial Enterprise licence |
| PaddleOCR | Apache-2.0 |
| ONNX Runtime | MIT |
| OpenCV | Apache-2.0 |
| FFmpeg | LGPL-2.1 or later (GPL if built with GPL parts) |
| FastAPI, SQLAlchemy, PyJWT | MIT |
| PostgreSQL | PostgreSQL Licence |
| Next.js, React, Tailwind | MIT |
| Leaflet | BSD-2-Clause |
| OpenStreetMap data | ODbL (attribution required) |

AGPL-3.0 is copyleft, so whether a state deployment triggers its source-sharing terms is a question
for the department's lawyers. Ultralytics sells a commercial licence that removes it. Either way, the
detectors sit behind two small wrappers (`anpr/detect/vehicle.py`, `anpr/detect/plate.py`), so the
decision does not touch the rest of the system.

## 14. Questions you may be asked

**Why not send all the video to one control room?** About 160 Gbps and 1.7 PB a day for 80,000
cameras, every camera password in one place, and statewide viewing as the default. We move text, 40–250× less.

**How accurate is it, honestly?**
- It read 36 of the 48 plates a person can read, with none wrong.
- On wide overview cameras the plates are 14–31 px wide. Nobody can read those, so it confirms nothing rather than guess.

**Why does it miss 12?**
- The misses are characters misread on plates 45–100 px wide, and plates not found on dark 720p footage.
- That is model work, not threshold tuning. The benchmark exported 175 vehicles and 932 human-checked observations to train it on.

**What about night?**
- On the CAM06 clip recorded at 18:00 (evening, not night) it read 5 of 6.
- On real night footage from the grid at 21:00, plates are too small and dark, and it confirms none, which is the correct answer.
- Camera placement and IR guidance are on the roadmap.

**What if the network drops?**
- The edge holds no state that matters.
- IDs are deterministic and ingest ignores repeats, so replaying is safe.
- The local queue itself is designed but not built; it is on the roadmap.

**Does it need new cameras?**
- No. It reads RTSP from the existing VMS or NVR, and analog cameras through their DVR or encoder.
- Reading plates needs the optics to allow it: a plate about 40 px wide or more.

**How does it reach 80,000 cameras?** All of this is from `docs/scalability.md` §5–8:
- edge GPUs where the optics allow ANPR (about 3,000, an estimate);
- more API replicas;
- Kafka between ingest and matching;
- `pg_trgm`, a bloom filter and PostGIS for the three parts that do not scale as written.

**Is a reading proof?**
- No. It is a probabilistic reading of a photograph.
- An operator looks at the crop before acting, and a near match is marked "review".

**Face recognition?**
- Not built. At these distances, 720p footage carries no usable face.
- The watchlist and alert path will take another kind of reader when the cameras can support one.
