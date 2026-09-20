# Vigentra against the Sentinel brief — an evaluator's reading

Written 20 Sep 2026 after reading every page of <https://sentinel.gujarat.gov.in> (home, about,
prize structure, problem statement with all seven steps opened, the integrator's guide under
Resources, schedule, all 50 FAQs, contact) and then auditing this repository the way a jury member
from a knowledge partner would: what is asked, what is shown, what would lose marks.

## 1. What the site says now

**Dates have moved.** Submission closes **28 September 2026**; shortlisting is announced the same day;
the event is **12–13 October** at i-Hub Gujarat; results on 13 October. Our tracker still said
15 September and 22–23 September.

**Prizes.** Phase 1 (sandbox round) pays the top three per category: Category 1 (students, small and
medium startups) ₹4 lakh, ₹2 lakh, ₹1 lakh; Category 2 (large startups and companies) ₹5, ₹3, ₹2 lakh;
four consolation awards of ₹25,000 across both. The six Phase 1 winners go to the finale
(₹16, ₹8, ₹7 lakh, plus ₹50,000 to each other finalist and a ₹50,000 jury award). So a Phase 1 prize
is also the ticket to the finale, and it is won on the **uploaded submission**, judged remotely.

**The brief has sharpened since we last read it.** The problem statement now leads with one sentence
that every deliverable is measured against:

> live video streams integrated with a searchable database of watchlist records … continuous
> AI-powered analysis and automated alert generation whenever a match is detected.

The own-feed demonstration must *clearly illustrate* four things: onboarding, AI detection, correlation
with a watchlist, and an automatic real-time alert. "Footage of your choice" is now allowed for it, so
a camera the team owns is no longer needed. The presentation and the HLD must each describe the
watchlist-correlation method and the alert workflow "including prioritisation, visualisation, and user
interaction". On the day, a **designated registration number is given and the vehicle must be traced
across cameras, with the complete timestamped route**.

**The seven scored areas** (Step 7, FAQ 36): (1) successful test case on the Government feed,
(2) presentation, (3) architecture and HLD, (4) working platform on own and Government feed,
(5) analytics output — "ANPR, vehicle or person detection, intrusion detection, object detection,
timestamps, and output reports", (6) scalability and PoC readiness, (7) submission completeness.
Bonus never compensates for a missing mandatory item.

**The integrator's guide** sets a checklist the evaluation exercises: RTSP over TCP, timing from PTS,
no trust in declared FPS, reconnect with backoff, decoder warnings not fatal, catalogue-driven camera
list, mixed H.264/H.265, sane behaviour across the loop cut, consume only, pace the load, and do not
plan around copies of the footage.

## 2. Scores, as I would give them today

| # | Area | Score | One-line reason |
|---|---|---:|---|
| 1 | Successful test case | 6 / 10 | 30 of 30 cameras onboarded and read; the designated-vehicle trace is exposed by sampling (below) |
| 2 | Presentation | 7 / 10 | Clear hybrid-model argument; figures are from 14 Sep and the alert workflow is thin |
| 3 | Architecture and HLD | 8 / 10 | The strongest part: adapters, brokered video, audit, access model; alert prioritisation missing |
| 4 | Working platform | 7 / 10 | Real software end to end; the submitted video never shows a watchlist match raising an alert |
| 5 | Analytics output | 6.5 / 10 | Honest ANPR with no false plate; daylight only, no intrusion analytics, no face recognition |
| 6 | Scalability and PoC readiness | 8 / 10 | Complete against FAQ 35; no load-test evidence |
| 7 | Submission completeness | 5 / 10 | Links pending, tracker dated, no hosted URL, demo script does not match the brief |

A submission at this level is shortlist material. It is not yet a safe top three, and the three things
that separate the two are all fixable before 28 September.

## 3. Findings, most damaging first

### F1. The designated vehicle will probably not be seen (area 1)

The deployed reader takes 25 frames at every 20th frame — about 20 s of video — from each camera in
turn, then sleeps 120 s. Across 30 cameras that is roughly **1–2 % of each camera's time**. A vehicle
crosses a junction in 3–8 s. The test gives a plate and asks for its route; a system that is looking
at each camera 2 % of the time returns an empty route however good its reader is. The ANPR work of
17–19 Sep (36 of 48 plates, none wrong) is measured on whole clips, which is what the pipeline can
read, not what this schedule lets it see.

The grid makes it worse: over HLS every new session starts the 24.5 h recording from its first minute
(`docs/anpr-cam06-day.md`), so sampled passes over HLS replay the same opening minutes for ever. RTSP
is the guide's live path and the worker prefers it; the HLS fallback must not be what the finale runs on.

**Improve.** Continuous readers, not passes, on every camera whose plates are legible (cam06, cam07,
cam12 today; re-profile the ~50 on the day with `tools/anpr_profile_cameras.py`); count-only passes
elsewhere. One 720p camera is real time on one M1-class core-set (`docs/anpr-optimisation.md`, "At
camera speed"), so the finale needs one reader process per legible camera and the hardware to match —
say so in the PoC-readiness slide with the measured figure. Keep a fuzzy route query (already built:
one-glyph-away matches are shown as near matches) because a designated plate will be misread on some
cameras.

### F2. The demo video does not show the one thing the brief is about (areas 4, 7)

`Vigentra_Demo_Short.mp4` shows sign-in, overview, registry, live wall, live ANPR, detections, trace,
report, audit. It never adds a plate to the watchlist and never shows the alert arriving. The brief
lists exactly that as two of the four things the own-feed demo must show. `docs/demo-script.md` is a
script for the access-request story and contains neither the word watchlist nor alert.

**Improve.** Re-record to the new scripts in `docs/demo-script.md`: add a plate to the watchlist with
a reason, start the feed, let the alert arrive on screen, acknowledge it, open the route. Under three
minutes. Do the same beat on the Government feed with a plate the engine is known to read on cam06
by day.

### F3. "Real-time" alerts were minutes late (areas 1, 4, 5) — fixed today

The matcher runs at ingest, but a plate that settled mid-pass waited for a 50-row batch or the end of
the pass, and the alerts page refreshed every 30 s. **Fixed in `9479690`**: a settled plate is uploaded
with the frame that settled it, and the page refreshes every 5 s.

### F4. Alerts have no priority and leave the building by no channel (areas 2, 3)

Both documents must describe "alert generation and notification workflow, including prioritisation".
An alert carries a category (stolen, wanted, blacklist, missing, suspect) and exact/near, but no
priority, and the only delivery is the console. **Improve:** derive a priority from category and
exactness (stolen or wanted and exact = critical; near match = review), sort and colour by it, and
offer one outbound channel — a signed webhook is enough and is what an integration-ready API means.
Then draw that workflow as one diagram in the HLD and one slide.

### F5. Analytics breadth: the brief names intrusion detection and face recognition (area 5)

We show ANPR, vehicle/person/object detection and incidents. "Intrusion detection" is in the scoring
sentence and we have nothing called that. It is cheap to add honestly: a per-camera polygon and
hours, and a person or vehicle inside it raises an incident — the detector and the incident pipeline
already exist. Face recognition we should **not** fake: say in the HLD that the watchlist schema and
the matcher are entity-agnostic, that a face matcher plugs in as another reader behind the same
alert path, and that 720p night footage at these distances does not carry faces — the same honesty
the ANPR section already earns marks for.

### F6. The shipped image reads fewer plates than the numbers we quote (areas 4, 5)

The configuration asks for the whole-crop text reader; the Docker image cannot load it (PaddlePaddle,
~1 GB) and silently falls back to the two CRNNs: 13 of 26 instead of 16, and the noon clip's
cross-reader rule cannot fire at all. Either ship it in the ANPR image or quote 13. A jury member who
runs the repository should get the number on the slide.

### F7. Stale figures and dates (areas 2, 7)

Tracker deadline (fixed today). Keynote slide 14 and presentation slide "Plate readings per camera,
14 Sep" predate the optimisation; the headline should now be the measured one: *36 of the 48 plates a
person can read, none wrong, across 38 clips; 15 of 16 on the busiest daylight clip*. The brief says
"Reference Model 1–5" in three places and "four models" in the FAQ; our slide should say "Models 1 + 3
+ 2 (hybrid)" and not count them.

### F8. Night footage (area 5) — say it first, before the jury finds it

Twelve of fifteen grid clips are 720p night footage at 250–1 200 kbps with plates of 14–31 px. Nobody
reads those, and our system correctly reads none. The day sweep shows 8–14 plates per 2 minutes by
daylight and none from 20:00 to 06:00. Put that table on a slide with the sentence "a false plate
costs more than a missed one: zero false across 38 clips". It turns a weakness into the jury's reason
to trust the alerts.

### F9. Submission hygiene (area 7)

No unlisted-video links yet, no hosted URL with test credentials (optional, but it is the cheapest way
to score "working platform" with a remote jury), HLD and scalability not yet exported to PDF at their
current text, README test count behind (124 edge-worker tests now).

### What is already strong — keep it in front

- Model 1 foundation is complete against its own deliverable list: registry portal with GIS map, bulk
  and manual onboarding, sample dataset, API documentation (`docs/api.md`), gap-analysis report.
- The integrator's checklist is met in code: RTSP over TCP, PTS-driven timing with measured FPS,
  reconnect with backoff, catalogue-driven cameras, discontinuity handling (`app/grid.py`,
  `app/worker.py`). Say so on one slide, item by item — it is the organisers' own checklist.
- Access model, brokered video, retention-on-read and the audit trail are beyond what most teams will
  show, and are listed bonus items.
- Measured, reproducible ANPR evidence with by-eye ground truth and a regression run. Almost no
  competitor will have this.

## 4. Plan to 28 September

| # | Change | Who | Status |
|---|---|---|---|
| 1 | Upload a settled plate at once; alerts page every 5 s | code | **done** `9479690` |
| 2 | Tracker: new dates, own-feed rule, open items | docs | **done** today |
| 3 | Demo scripts rewritten to the brief's four beats, own feed and Government feed | docs | **done** today |
| 4 | Alert priority from category and exactness; console sorts and colours by it | code | **done** `1e9a9a5` |
| 5 | Continuous-reader mode for legible cameras and a compose profile for it (`anpr-live`) | code | **done** `78afbaa`; **needs one live run by the team** — the stack was down while it was written |
| 6 | Intrusion zones: polygon + hours per camera, raised through the incident pipeline | code | **done** `6279929`; draw a zone on one camera for the demo |
| 7 | Signed webhook for alerts | code | after 4 |
| 8 | Text reader in the ANPR image, or quote the CRNN-only figure | **team decision** (≈1 GB image) | open |
| 9 | Presentation and keynote: new headline figures, alert-workflow slide, guide-checklist slide, night-footage slide | docs, then PDF rebuild | after 4–6 |
| 10 | HLD: alert workflow with prioritisation, face-recognition position, continuous readers, intrusion | docs | **done** today; re-export the PDF |
| 11 | Re-record both demos to the new scripts; upload unlisted; fill the links | **team** | after 4–6 |
| 12 | Hosted URL with a read-only test login | **team decision** | open |
