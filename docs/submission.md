# Submission tracker — Steps 5 and 6

What the organisers ask for, what answers it, and what is still open. Update
this file in the same commit as any change that moves a number or a status
below, and add a row to the change log at the end.

**Deadline.** Upload by **28 September 2026**; shortlisting is announced the
same day; the hackathon runs **12–13 October** at i-Hub Gujarat, results on
13 October (<https://sentinel.gujarat.gov.in/schedule>, read 20 Sep — the
dates moved from 15 and 22–23 September). What a juror would mark us down for
today, and the plan to the deadline: [`hackathon-evaluation.md`](hackathon-evaluation.md).

**Sources.** Steps 5 and 6 of <https://sentinel.gujarat.gov.in/problems>, and
FAQs 24 and 29–38 of <https://sentinel.gujarat.gov.in/faqs>, which spell the
steps out.

---

## Step 5 — Prepare & Submit

| Deliverable | What is asked (FAQ) | Our artefact | Status |
|---|---|---|---|
| Solution presentation | PPT/PDF: model chosen with justification, solution overview, key features (29) | [`deliverables/Vigentra_Solution_Presentation.pdf`](../deliverables/Vigentra_Solution_Presentation.pdf), 15 slides, built from [`deliverables/presentation/index.html`](../deliverables/presentation/index.html) | Built; rebuild the PDF whenever a figure on slide 9 moves |
| High-level design | Architecture with diagrams; heterogeneous cameras/VMS (IP, analog, multi-vendor, varied protocols); geographically dispersed sites (bandwidth, connectivity, edge vs central); analytics (ANPR, cross-camera tracking); scalability to ~80,000; department-level details needed for feasibility (29, 30) | [`docs/hld.md`](hld.md), exported to PDF for upload | Current with the 14 Sep changes; dispersed sites and analog cameras now covered (§10). PDF export pending |
| Own-feed demonstration | 2–3 min screen recording on **our own** feed: onboarding, live/recorded viewing, vehicle detection/ANPR. Working software only — no mock-ups or animations (31, 32) | Screen recording by the team, following a script in [`docs/demo-script.md`](demo-script.md) | Script to write; **needs a feed the team owns** (a phone or IP camera, or footage the team shot) — the synthetic mock-VMS clips do not qualify |
| Government-feed demonstration | Live demo on the government feed: onboarding, viewing, analytics output — ANPR, vehicle/person/intrusion/object detection (31) | Screen recording by the team, following a script in [`docs/demo-script.md`](demo-script.md) | Platform live on all 30 grid cameras; script to write; recording pending |
| Video & output report | The government-feed recording plus an **output report of detected vehicles or number plates with timestamps** (33) | `scripts/anpr_report.py` → `reports/anpr_report.md`, `.csv` (plates) and `_vehicles.csv`, from the platform's own API; annotated detection video `deliverables/Vigentra_ANPR_Demo.mp4` (CAM06 and the Delhi clip, not in git — upload it) | Plates with timestamps and vehicles per camera and class with first/last sighting; final export on submission day with an account that can read every department |
| Submission links | Unlisted YouTube, or Google Drive/OneDrive with viewer access; optional hosted URL with test credentials and a repository link (34) | [Links](#links) below | Repository known; video links pending |

## Reference-model deliverables (our hybrid: Model 1 + 3 + 2)

Each model on <https://sentinel.gujarat.gov.in/problems> lists the deliverables it expects; a hybrid is
judged against those of the models it combines.

| Model | Expected deliverable | Our artefact |
|---|---|---|
| 1 Registry & GIS | Working registry portal with GIS map view | Dashboard *Registry* (table and map), `/registry` |
| 1 | Bulk and manual camera-onboarding demonstration | *Installations → New* and *Bulk upload*; demo script appendix steps 1–7 |
| 1 | Sample onboarded camera-metadata dataset | [`deliverables/samples/camera_registry_sample.csv`](../deliverables/samples/camera_registry_sample.csv) (70 cameras) |
| 1 | Registry API documentation | [`docs/api.md`](api.md), plus OpenAPI at `/docs` on the running API |
| 1 | Sample gap-analysis report | [`deliverables/samples/gap_analysis_sample.md`](../deliverables/samples/gap_analysis_sample.md); live at *Reports → Gap analysis* |
| 3 VMS federation | Working middleware demo federating at least two different systems | Three: the Sentinel grid, the Traffic Police VMS and the Municipal VMS, in different dialects (HLD §4) |
| 3 | Unified event-correlation dashboard | *Events*, *Incidents* and *Alerts* across every source; the plate route across cameras (*Plates*) |
| 3 | Adapter/plugin architecture documentation | [`docs/adapter-contract.md`](adapter-contract.md), HLD §4 |
| 3 | Sample federated analytics report | [`deliverables/samples/federated_analytics_sample.md`](../deliverables/samples/federated_analytics_sample.md) and `federated_analytics_by_camera.csv` |
| 2 Unified viewing | Unified viewer connected to feeds from at least two systems | *Live wall*: 50 feeds from three systems |
| 2 | ANPR demonstration on live or recorded feeds | Live ANPR on grid CAM06 and the Delhi clip; `deliverables/Vigentra_CAM06_Noon_ANPR.mp4`, `Vigentra_Delhi_ANPR.mp4` |
| 2 | Searchable metadata dashboard | *Detections*, *Plates* (fuzzy search), *Reports → ANPR* |
| 2 | Architecture note: existing departmental systems unaffected | HLD §4, "Existing departmental systems stay as they are" |

The samples are regenerated from the running platform with `python3 scripts/export_samples.py`.

## Step 6 — Plan for Scale (~80,000 cameras)

[`docs/scalability.md`](scalability.md) is the plan. FAQ 35 says it must
include each of the following; FAQ 24 adds infrastructure sizing,
cost-benefit, department-wise requirements and a roadmap.

| Page item | FAQ 35 wording | Where it is answered | Status |
|---|---|---|---|
| Hardware & software requirements | central, regional and edge-compute requirements | scalability §2 — per-tier sizing (estimate) and the software stack | Complete |
| Network & bandwidth planning | network-bandwidth planning and low-bandwidth strategies | scalability §3 | Complete |
| Storage & retention strategy | hot/warm/cold storage tiers based on retention | scalability §4 | Complete |
| AI processing capacity | GPU/accelerator capacity for analytics | scalability §5 — measured ANPR throughput plus the GPU estimate | Complete |
| Disaster recovery strategy | high availability, backup and disaster recovery; load balancing, horizontal scaling, monitoring/logging/health checks | scalability §6 (scaling, monitoring) and §7 (HA, backup, DR) | Complete; targets untested at scale, and said so |
| Statewide rollout plan | phased statewide rollout plan | scalability §9 | Complete |
| (FAQ 24) cost-benefit | cost-benefit analysis | scalability §10 | Complete; central tiers priced at tender, not guessed |

## Evaluation areas (FAQ 36) and what answers each

| Area | Answered by |
|---|---|
| Successful test case — onboarding + analytics on the government feed; on the day, a designated vehicle tracked across cameras (26–28) | All 30 grid cameras onboarded and read live; route reconstruction with timestamped, location-wise history (HLD §8) |
| Solution presentation | The presentation above |
| Solution architecture | `docs/hld.md` |
| Working platform | The two recordings |
| Video analytics output | The output report and the measured figures below |
| Scalability and PoC readiness | `docs/scalability.md` |
| Submission completeness | This tracker |

Bonus (FAQ 38) — hybrid architecture, cross-camera tracking, edge processing,
bandwidth optimisation, cybersecurity and auditability, operational
dashboards, automated alerts, health monitoring, integration-ready APIs — is
all implemented and should be named on a slide of its own. Bonus does not
compensate for a missing mandatory item (FAQ 37), so the table above comes
first.

## Measured so far

Update these whenever they move; quote nothing that is not in this table or
labelled as an estimate.

| Figure | Value | When |
|---|---|---|
| Grid cameras onboarded and online | 30 of 30 | 14 Sep |
| Plate readings on the government feed | 236 from 12 cameras, 9 with confidence ≥ 0.5 | 14 Sep, 00:00–16:30 IST |
| Vehicle/object detections on the government feed | 95,287 from 20 cameras | 14 Sep, 00:00–16:30 IST |
| Labelled Delhi clip, 300 frames | 4 of 12 plates read exactly, 1 wrong confirmation | 14 Sep |
| Plates read across all 38 recorded clips | 36 of the 48 a person can read, none wrong; on the 36 clips of the 18 Sep set, 16 of 26 (was 9) | 19 Sep, `docs/anpr-optimisation.md` |
| Grid cam06 at noon, 2 min, 84 vehicles (141 tracker ids, fragments linked and checked by eye) | 15 of 16 plates, none wrong | 23 Sep |
| Delhi clip, whole clip | 13 of 20 plates, none wrong (was 7) | 18 Sep |
| Grid cam06, 1080p recording | 3 of 5 plates, none wrong (was 2) | 18 Sep |
| Vehicle tracks across all clips | 1 868 on the 36-clip set (was 1 524), 2 100 on all 38; the 36 processed in 2 779 s (was 5 784 s) | 19 Sep |
| Automated tests | 201 passed, 5 skipped (platform); 131 passed (edge worker) | 20 Sep |
| Plate ingest with watchlist matching | 164 reads/s, one API process, 200 watched plates | 20 Sep |

## Links

| | |
|---|---|
| Source repository | <https://github.com/rewardix-ai/vigentra> — branch `review-fixes-24x7-detection-branding` |
| Own-feed recording | *pending* |
| Government-feed recording | *pending* |
| Output report | *pending — generated on submission day* |
| Hosted platform and test credentials | *optional* |

## Change log

| Date | Change (commit) | Deliverables updated |
|---|---|---|
| 14 Sep | Tracker created | this file |
| 6 Oct | Camera-health alerts: a camera offline for two health checks in a row (~40 s) raises `CAMERA_OFFLINE`, an unreachable department system one `SOURCE_UNREACHABLE` (not one per camera); closed by the monitor when it answers. Alerts page section and badge, `health:acknowledge` for the NOC desk and owning units, signed webhook `camera_health_alerts`, audited. `/api/v1/health-alerts`. Tests: 4 new in `tests/test_health_alerts.py` | HLD §7, `docs/api.md`, process map (M3) |
| 6 Oct | Shortlisted for Phase 1 (on-site 12–13 Oct, i-Hub Gujarat). Venue-network fallback: where RTSP port 8554 is blocked, `SENTINEL_GRID_HLS_FALLBACK=1` lets the edge worker read each grid camera as HLS through central-api's video broker, over the audited session it already opens (no second grid sign-in); off by default, an unreachable 8554 still raises without it. Tests: 5 new in `test_open_capture.py` | HLD §protocols, `docs/sentinel-grid.md`, `.env.example`, compose |
| 8 Oct | Object detection page: a Vehicles view (default) beside the frame list. `GET /api/v1/detections/vehicles` groups frames into vehicles by tracker id (gaps under 2 min), with type, time seen, frames, closest width, plate (confirmed or for review) or why there is none (too far to read: under 96 px; or plate not readable), and fair counts: vehicles seen, near enough to read, identified, identified share of the near ones. Same camera scoping, plate permission, retention and audit as the frame list. Tests: 2 in `tests/test_vehicle_plate.py` | tracker |
| 8 Oct | Collection watchdog (`scripts/collect_plates.py`): light mode can stay alive while most cameras get no frames (22 of 30 idle overnight after long pauses). The supervisor reads the reader's per-minute idle list and restarts it when 5+ cameras are idle 3 minutes running (5-minute grace after each start); refusal windows keep their own handling. An outage now costs about 3 minutes, not hours | tracker |
| 8 Oct | Footage time on the traced route: each point of `GET /api/v1/plates/{plate}/track` carries `video_time`, the date and time printed on that camera's footage at the read (from the sampled on-screen clocks, `scripts/camera_plates.sql`), shown in the route table and map popup beside our read time. Given only near a clock sample of the same camera from the same grid run (under 1 h, no restart between), since across a restart or loop the estimate drifts; empty otherwise. The supervisor now re-samples every camera's clock every 30 min in light mode so most reads qualify | tracker |
| 8 Oct | Live reader, where plates were lost: a per-minute tally of every closed track's outcome showed 132 of 137 ending `no_plate_detected` (the readers never ran). Saved crops showed most are side-on or headlight-glare views with no plate, but also autos whose yellow plates the default detector misses outright. Light mode now (a) uses the plate detector fine-tuned on grid footage (`plate_det_grid_clean.pt`): on 70 missed near vehicles it boxed 3 real plates through the full prior/threshold chain, the default 0; (b) gives GPU priority to cameras with a plate box in view rather than any vehicle >= 96 px (cam01/02 buses had taken 350-450 frames/min while 15+ cameras sat idle); plate-bearing share of closed tracks rose from 4-8 % to 10-20 % in the first minutes. YOLO-World (6 vehicle types incl. auto rickshaw and scooter) was tried on the same crops and named no auto or scooter correctly at grid resolution; not adopted | tracker |
| 8 Oct | Plate-detector fine-tuning, step 1 (training data): `collect_plates.py --harvest DIR` makes light mode save frames with a vehicle >= 120 px (one per camera per 10 s, 8,000 at most), with the engine's vehicle and plate boxes, for training only (research repo; never searched for a vehicle). `ANPR/tools/harvest_to_plate_set.py` cuts each vehicle exactly as the engine does, has four detectors propose plate boxes, drops any crop reading within two edits of a benchmark target plate, and splits by camera and 10-minute block. A first sheet showed every proposal needs eye review: all four detectors agreed on GSRTC bus lettering | tracker |
| 7 Oct | Object detection shows the vehicle's plate on every frame of that vehicle: a plate is decided once, when the track closes, so 312,870 grid detections in 12 h carried a plate on 16 rows. The edge now sends each detection's tracker id, and central writes a settled plate onto the same vehicle's earlier detections (same camera, same tracker id, the 3 minutes before); other vehicles untouched, plate permission unchanged. Tests: `tests/test_vehicle_plate.py` (63 platform tests in that run and the edge-worker suite pass) | tracker |
| 7 Oct | Night reading, measured (`night_base` / `night_clahe` in EW/reports/anpr_benchmark): searching dark vehicle crops again after CLAHE found 3 more plate boxes and no more plates on four night clips (CAM06 18:00 5 of 6 either way; grid cam07 21:00 0 of 1, its plate now found but misread; cam13 and cam15 hold no plate a person can read), so it was not kept. Light mode gets the PaddleOCR text reader back, one shared copy for all 30 cameras: the reader that recovers the hardest plates | tracker |
| 7 Oct | Light mode for the event (one Mac, every camera live): `services/edge-worker/tools/light_readers.py` runs all 30 grid cameras in one process (one thread per camera on the worker's own pass, shared model weights, every camera's engine kept, one GPU lock), vehicles at 640 px with YOLO11n, plates at full resolution, CRNN readers only. An attention scheduler gives the GPU to cameras with a vehicle near enough to read (>=96 px: continuous), approaching (>=48 px: every 1 s), else every 6 s. Measured: ~13 frames/s across 30 cameras, ~30 ms a quiet frame, ~90 ms a near one, 3.3 GB. `collect_plates.py --mode light` (default) supervises it, switches grid accounts on refusal and samples clocks through the other account | tracker |
| 7 Oct | API method reference `docs/api-methods.md` (+ `api-methods.html`): all 61 central-API operations (39 GET, 17 POST, 2 PATCH, 3 DELETE, no PUT), the dashboard proxy and the mock VMS APIs, each with why that method and not another; status codes used | docs |
| 7 Oct | Repo tidied for the event: removed the superseded v2 keynote and proposal (PDFs and page HTML), the six CCTV stills only the v2 pages used, and the shorter duplicates of the code map and readiness audit (the Detailed versions stay); all recoverable from git history. README brought up to date: test count, repository layout (deliverables/, scripts/) and the real feeds Vigentra reads (Sentinel grid, TfL, own clips) | tracker, README |
| 7 Oct | Time in the video for every grid reading: the grid's cameras are looping recordings on their own dates (13 June 21:00 for most, cam06 17 June 18:00, cam16 13 June 14:49, cam20/25 3 Aug, cam24/30 8 Aug, ...) and the server restarts them all every ~30-40 min, the restarts being the RTSP-401 refusal windows (confirmed: after two windows cam05 and cam16 were back at 21:00 and 14:49). `scripts/osd/clock.py` reads each camera's on-screen clock with Apple Vision (two frames 3 s apart must agree) into `camera_plates.video_clock`; the supervisor records each restart and samples after every resume; each camera table gains `read_at_video`. Consequence for the event: only each recording's first ~10-15 minutes are ever served | tracker |
| 7 Oct | Plate collection on the 30 grid cameras until 11 Oct 23:00 IST, for the designated-vehicle trace: `scripts/collect_plates.py` runs four ANPR readers on the Mac's GPU (reading time weighted toward cameras whose plates are legible), restarts any that exits, freezes them during the grid's RTSP-401 refusal windows with one test login every few minutes, and at the deadline restarts the four Docker readers it replaced. Every grid reading is also copied by a trigger into its camera's own table, `camera_plates.cam01`-`cam30` (`scripts/camera_plates.sql`; the 589 earlier grid readings backfilled). Unconfirmed readings above the review floor are kept, flagged `confirmed = false`. A check of the 1,148 earlier readings found no real vehicle read on two cameras (the 11 multi-camera strings were misreads, impossible speeds or demo data) | tracker |
| 7 Oct | Keynote technical brief `deliverables/anpr-keynote/TECHNICAL.md`: every layer's choice, its settings, why it was chosen and what it beats, numbers to quote with sources, what was tested and dropped, licences (Ultralytics YOLO11 is AGPL-3.0: a legal check before procurement) and likely questions. Three "Under the hood" scenes in both decks (built with · why this, not that · tested and dropped); the control room now has 21 scenes, the story edition 19. HLD §9 counts brought up to date: 25 permissions (was 22), 338 test functions (209 platform, 129 edge worker). The brief also as a standalone styled page, `TECHNICAL.html` (`tools/technical_html.py`) | keynote, HLD |
| 6 Oct | On-stage keynote `deliverables/anpr-keynote` (interactive HTML, 18 scenes): Vigentra-branded; two live flowcharts (how a plate is read, with the refusals; how the system connects); the deployed models' real boxes replayed over the playing CAM06 video; grid cam01–cam16, CAM06 and Delhi footage only (no London feeds). Claims re-checked against the reports: the clip labelled "CAM06 night" is stamped 18:00 in June, so night is shown with grid cam07 and cam15 at 21:00. Review rounds: no glow effects, no drawn number plate (readings as plain characters), flowcharts with the real data at each step and Yes/No decisions; every video scene moves on by itself after a set time (A to hold); the blur-refused crops hold no visible plate, so a blurred Delhi plate the readers could not agree on (no plate saved) is shown instead. The replay now runs the deployed detector settings (class-agnostic NMS: one box per vehicle; it had shown a car box and a truck box on the same car) and labels each vehicle with the class of its most confident detection (the evidence car: car 0.91). 7 Oct: redesigned as a Vigentra control room, 16 scenes: monitors of real footage, a frame-by-frame event log of what the deployed models saw, schematics with a live log, a tally of the 136 real readings and the saved text-only record. Added what makes Vigentra different (six design choices with evidence) and the roadmap (from docs/scalability.md and measured limits), and a second deck, the story edition (lens.html), in an editorial look after datalense.app | deliverables/anpr-keynote |
| 28 Sep | Delhi own-feed verification clip `deliverables/Vigentra_Delhi_ANPR.mp4` (+ `_720p`): 13 of 20 legible plates confirmed, none wrong, each shown beside the crop it was read from; no vehicle count, because in this dense street scene fragments cannot be linked reliably. Government-feed output report `deliverables/samples/cam06_noon_output_report.md` + `cam06_noon_vehicles.csv`: 84 vehicles, 15 plates, camera-clock timestamps. Site re-read: dates and requirements unchanged; Model 1/2/3 sample deliverables exported (`scripts/export_samples.py`) | tracker, submission folder |
| 23 Sep | Vehicles are counted once: `anpr/track/vehicle_count.py` links the tracker ids that are one vehicle — an id switch along its path, a double box, a parked vehicle re-acquired — and drops boxes that never moved and never looked like a vehicle (lane markings, the time overlay). CAM06 noon: 141 tracker ids are 84 vehicles, every merge and every doubtful entry checked by eye; the verification clip numbers each vehicle (V1, V2, …) and keeps the number through id switches. The noon figure was quoted as 127 vehicles and is corrected in the presentation and proposal v3 | tracker, presentation, proposal, day report |
| 23 Sep | CAM06 noon clip re-rendered as a verification view (`tools/annotate_video.py --verify`): the whole clip is read first exactly as the worker reads it, then every vehicle is drawn with an interpolated box, its settled class and the plate the engine read for it while it is on screen, beside the crop whose own reading is that plate. 126 vehicles, 15 plates confirmed, none wrong. 1080p H.264 46 MB, 720p 15 MB | tracker, government-feed demo material |
| 22 Sep | Continuous reader run live (`anpr-live`, Docker, CPU only): the Delhi camera's plates were confirmed and uploaded as they settled (31 sightings, 18 plates); **grid CAM06 gave none** — corrected 28 Sep: a CAM06 pass took 13–32 h in Docker on this laptop (no GPU, three other detectors on the same CPU), far too slow to settle a plate, and passes that outlived their sign-in lost their uploads (fixed 28 Sep). Live grid ANPR needs the GPU host the camera-speed figures were measured on. Annotated CAM06 noon clip rendered with the full engine: 15 of 16 legible plates, none wrong — `deliverables/Vigentra_CAM06_Noon_ANPR.mp4` (+ `_720p`, not in git; copies in `~/Downloads/Vigentra_Submission`). `tools/annotate_video.py` now samples with the camera's burst sampler like the worker; a plain stride had confirmed two plates wrongly | tracker, government-feed demo material |
| 20 Sep | Keynote 3 (20 slides: slide 14 now 36 of 48 plates, 0 false, 332 tests; new slide 19, from a read to an alert) and technical proposal v3 (55 pages: Addendum B — measured ANPR, alert workflow, continuous readers, intrusion, faces, 164 reads/s ingest). Built by whole-page patching with `deliverables/tools/pdfpatch.swift`; the proposal keeps its 58 outline entries and 108 links. Solution presentation rebuilt, 15 slides. Copies in `~/Downloads/Vigentra_Submission` | keynote, proposal, presentation |
| 20 Sep | Signed alert webhook (`ALERT_WEBHOOK_URL`); measured plate ingest with watchlist matching, 164 reads/s per API process (`tests/test_ingest_volume.py`); opt-in `anpr-text` image target with the text reader (`EDGE_ANPR_TARGET=anpr-text`, not built here) | scalability §5–6, HLD, presentation |
| 20 Sep | Alert priority (critical / high / review, derived from category and exactness, console sorted by it); continuous plate readers for legible cameras (`EDGE_CONTINUOUS`, compose profile `anpr-live`); intrusion zones per camera raised as `INTRUSION` incidents. 131 edge-worker tests, 30 watchlist tests pass; the dashboard changes build with the image and were not type-checked on the host | HLD §6–7, evaluation plan, tracker |
| 20 Sep | Site re-read in full: deadline now 28 Sep, event 12–13 Oct; the brief now centres on watchlist correlation and real-time alerts, and allows footage of our choice for the own-feed demo. Evaluation written as a juror would (`docs/hackathon-evaluation.md`); demo scripts rewritten to the brief's four beats; a settled plate is uploaded at once and the alerts page refreshes every 5 s (`9479690`) | tracker, demo script, evaluation |
| 19 Sep | A plate read by both a CRNN and the text reader confirms without a vote-share majority (cam06 noon 14 → 15 of 16); the noon and night cam06 recordings join the regression set; the whole-clip benchmark no longer paces itself like a live camera. Full 38-clip regression: 36 of 48, none wrong, the 18 Sep numbers reproduced exactly on their 36 clips | HLD, reading doc, optimisation report, tracker |
| 18 Sep | ANPR optimised across the whole estate and measured on every recorded clip: 16 of 26 readable plates against 9, none wrong, 1 868 vehicle tracks against 1 524, 2.1x the speed. Audit, baseline, per-camera configuration, failure analysis and a per-vehicle dataset: `docs/anpr-audit.md`, `docs/anpr-baseline.md`, `docs/anpr-optimisation.md` | HLD, scalability plan, tracker |
| 14 Sep | HLD brought up to date: readers and runtime, the catalogue read once centrally, RTSP-backed liveness, the 96 px plate-search floor, 295 tests, 14 Sep measurements, and a new section on dispersed sites and thin links (FAQ 30) | HLD |
| 14 Sep | Scalability plan reorganised around FAQ 35's list: per-tier hardware and software, monitoring and health checks, HA/backup/DR, cost-benefit; measured ANPR throughput replaces "not yet measured"; stale HLS-fallback claim corrected | scalability plan |
| 14 Sep | Output report covers vehicles as well as plates (FAQ 33): per camera and class, first and last sighting, counted in the database | output report |
| 14 Sep | Solution presentation built: model choice and justification, architecture, integration, analytics, alerts and tracking, security, 14 Sep measurements, scale, rollout, bonus features | presentation |
| 14 Sep | Worker resolves any grid camera the registry names, not only cam01–cam30, so the ~50-camera event grid needs no worker change (Step 4) | sentinel-grid doc |
| 14 Sep | A grid camera the survey never saw is listed once, under Traffic Police, instead of once per department (Step 4). Needs a central-api rebuild before the event | tracker |
| 14 Sep | Presentation: "Our perspective" slide added — what the team believes and what the government feed taught us (draft in the team's voice, for the team to edit) | presentation |
| 14 Sep | Presentation: closing slide says what was built in the time available and what upgraded hardware (edge GPUs, cameras placed for ANPR) would add | presentation |
| 14 Sep | Own-feed demonstration path: the Traffic Police's own VMS federates beside the grid as a third source, takes installation forms, and plays the Delhi test clip for the camera onboarded (FAQ 31) | tracker; demo script to follow once deployed |
| 14 Sep | Annotated detection video: the engine's own boxes and readings on the Delhi test clip (6 of 12 labelled plates confirmed exactly, one wrong) and CAM06 (GJ23H1546 and GJ11S7924 confirmed), built by `tools/annotate_video.py` and `tools/assemble_demo_video.py` | video & output report |
| 14 Sep | Live wall plays department-VMS feeds in full motion, and asks for the password whenever one is on it (f039ac6) | tracker |
| 14 Sep | 50 cameras in service: the Traffic Police VMS beside the grid now carries the Delhi own-feed clip and 19 public TfL JamCam feeds (Powered by TfL Open Data), labelled as London cameras; the 19 grid recordings tried first were decommissioned, not deleted | tracker, walkthrough video |
| 14 Sep | App walkthrough video (5:55): recorded live in the running app as the joint control room: 50-camera registry, live wall (reason + password, every tile audited), Vigentra's ANPR on the Delhi clip (DL13CA2927, DL1CQ5334, DL7CR3761, DL1LT1087 among 136 reads) and CAM06, trace, ANPR output report, events, incidents, audit log. deliverables/Vigentra_App_Walkthrough.mp4, not in git; grid tiles wait for a frame because the grid refused connections while recording | tracker, walkthrough video |
| 14 Sep | Live wall carries the edge engine's annotated output: the Delhi own feed plays Vigentra's annotated Delhi clip, and a new REC-C06A feed plays an annotated CAM06 recording (boxes and plate reads drawn in); the last TfL slot was withdrawn to keep 50 in service | tracker, walkthrough video |
| 14 Sep | Dashboard tidied after a page-by-page review: the alert badge reads a count (no plates, no audit row, so the audit log no longer fills with polling), header names 'all departments', registry and installation requests open on what is in service with decommissioned one pick away, registry table fits the page, map frames Gujarat, wall hides sub-10% plate reads | tracker |
| 14 Sep | Live wall: click any full-motion feed to focus it full screen, Esc returns; the same session grows, so no second password or audited access | tracker, walkthrough video |
| 14 Sep | Header logo no longer jumps on the first page after sign-in: the mark and the lockup declare their size, so the wordmark does not shift when the image lands | tracker |
| 14 Sep | Pre-rendered annotated feeds withdrawn: the Delhi own feed plays its plain clip again, REC-C06A is decommissioned and the 19th TfL feed is back (50 in service); detection now happens live instead | tracker |
| 14 Sep | Live detection on the live wall: the frame service draws YOLO11n boxes on every frame a tile is shown, serves the Traffic VMS feeds beside the grid through the VMS's authorised handle, and with --anpr runs the full ANPR engine live on chosen feeds (Delhi and CAM06: vehicle tracks, plate boxes, settled reads). The wall needs only a reason; a clicked tile fills the screen. ANPR live needs the Mac GPU, so for the demo the frame service runs on the host like the readers (EDGE_SNAPSHOT_URL=http://host.docker.internal:9100) | tracker, walkthrough video |
| 14 Sep | Logo tagline drawn as text, not SVG textLength: Safari scrambled the stretched line under the wordmark | tracker |
| 14 Sep | Pages keep their inputs across a reload (live wall incl. a running snapshot wall, registry filters, detections, trace, ANPR report; never a password); Enter in a password box submits the live wall and the camera player | tracker |
| 14 Sep | Live wall no longer starves the API: the snapshot route releases its DB connection before fetching a frame (10-connection pool; health 5-15 s -> 0.02-0.1 s), and the frame service draws detections in the background so tiles are answered in ~2 ms | tracker |
| 14 Sep | Walkthrough video re-recorded (no password needed now): 50-camera live wall with live vehicle detection on every tile, Delhi feed and grid CAM06 full screen with live ANPR, then camera pages, detections, trace, ANPR report, events, incidents, audit, overview, registry. ANPR readers now run only on CAM06 and the Delhi clip. deliverables/Vigentra_App_Walkthrough.mp4 (+ _720p), not in git | tracker, walkthrough video |
| 14 Sep | 2:42 submission demo cut from the walkthrough: overview, registry, live wall with live detection, Delhi and CAM06 live ANPR full screen, detections, trace, ANPR report, audit. deliverables/Vigentra_Demo_Short.mp4, not in git | tracker, demo video |
| 15 Sep | Submission demo re-recorded zoomed in (a 1440x810 page drawn at 1920x1080) and starting at the sign-in page: login, overview, registry, live wall with live detection, Delhi and CAM06 live ANPR full screen, detections, trace, ANPR report, audit. deliverables/Vigentra_Demo_Short.mp4 (2:48) | video |
| 15 Sep | Keynote 2 and technical proposal v2: slide 14 figures (50/50 cameras, 278,362 detections, 870 plate readings across 15 cameras, 301 tests), live wall and live ANPR slides, updated close; proposal pages 16, 17, 40 and 43 corrected, plus a two-page addendum. Untouched pages are the originals | keynote, proposal |
| 15 Sep | README test count brought up to date (301: 196 platform, 105 edge worker); the working branch merged into main for the public release | README |
| 16 Sep | Edge worker demotes a proposed plate box wider than 0.60 of its vehicle (the old cut-off, 0.9, caught almost nothing). A registration never spans its vehicle: of 323 proposed boxes graded by eye in the ANPR research project, the 279 real plates reach 0.57 at most, while 37 of the 44 that were not plates (a hoarding's phone number, a light bar) sit above 0.60. On a sparse night camera the old rule let a hoarding be read as TS57SDB3008; the new one does not. Edge-worker tests 105 -> 109 | HLD, anpr-reading doc, README |
| 16 Sep | Edge worker no longer confirms a plate read off a blank surface: a confirmation now also needs glyph-shaped marks in the track's best crops (floor 3). On the Delhi clip a truck windscreen had been CONFIRMED as DL11AB3684, every crop of the glass "agreeing" on an invented string; it shows 2 marks and is now a candidate, while every correct confirmation shows 5-11 and stands. Measured on 182 hand-graded tracks in the ANPR research project and replayed through the edge worker's own engine. It does not tell a plate from advertising text, which the overlay mask and the width rule cover. Edge-worker tests 109 -> 113 | HLD, anpr-reading doc, README |
