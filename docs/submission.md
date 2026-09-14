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
