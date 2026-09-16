# Submission tracker — Steps 5 and 6

What the organisers ask for, what answers it, and what is still open. Update
this file in the same commit as any change that moves a number or a status
below, and add a row to the change log at the end.

**Deadline.** Upload by **15 September 2026**; shortlisting is announced that
evening; the hackathon runs **22–23 September** at i-Hub Gujarat
(<https://sentinel.gujarat.gov.in/schedule>).

**Sources.** Steps 5 and 6 of <https://sentinel.gujarat.gov.in/problems>, and
FAQs 24 and 29–38 of <https://sentinel.gujarat.gov.in/faqs>, which spell the
steps out.

---

## Step 5 — Prepare & Submit

| Deliverable | What is asked (FAQ) | Our artefact | Status |
|---|---|---|---|
| Solution presentation | PPT/PDF: model chosen with justification, solution overview, key features (29) | [`deliverables/Vigentra_Solution_Presentation.pdf`](../deliverables/Vigentra_Solution_Presentation.pdf), 13 slides, built from [`deliverables/presentation/index.html`](../deliverables/presentation/index.html) | Built; rebuild the PDF whenever a figure on slide 9 moves |
| High-level design | Architecture with diagrams; heterogeneous cameras/VMS (IP, analog, multi-vendor, varied protocols); geographically dispersed sites (bandwidth, connectivity, edge vs central); analytics (ANPR, cross-camera tracking); scalability to ~80,000; department-level details needed for feasibility (29, 30) | [`docs/hld.md`](hld.md), exported to PDF for upload | Current with the 14 Sep changes; dispersed sites and analog cameras now covered (§10). PDF export pending |
| Own-feed demonstration | 2–3 min screen recording on **our own** feed: onboarding, live/recorded viewing, vehicle detection/ANPR. Working software only — no mock-ups or animations (31, 32) | Screen recording by the team, following a script in [`docs/demo-script.md`](demo-script.md) | Script to write; **needs a feed the team owns** (a phone or IP camera, or footage the team shot) — the synthetic mock-VMS clips do not qualify |
| Government-feed demonstration | Live demo on the government feed: onboarding, viewing, analytics output — ANPR, vehicle/person/intrusion/object detection (31) | Screen recording by the team, following a script in [`docs/demo-script.md`](demo-script.md) | Platform live on all 30 grid cameras; script to write; recording pending |
| Video & output report | The government-feed recording plus an **output report of detected vehicles or number plates with timestamps** (33) | `scripts/anpr_report.py` → `reports/anpr_report.md`, `.csv` (plates) and `_vehicles.csv`, from the platform's own API; annotated detection video `deliverables/Vigentra_ANPR_Demo.mp4` (CAM06 and the Delhi clip, not in git — upload it) | Plates with timestamps and vehicles per camera and class with first/last sighting; final export on submission day with an account that can read every department |
| Submission links | Unlisted YouTube, or Google Drive/OneDrive with viewer access; optional hosted URL with test credentials and a repository link (34) | [Links](#links) below | Repository known; video links pending |

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
| Automated tests | 192 passed, 5 skipped (platform); 103 passed (edge worker) | 14 Sep, commit 5bb6a2c |

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
