# Event-day playbook: tracing the designated vehicle, and what we show if the plate will not read

For the on-site test of 12-13 Oct 2026: about 50 grid cameras, one registration number named on the
day, its complete timestamped route on the map. This sets out what to expect honestly, how the
operator runs the test, the backups, and the evidence behind every claim. Numbers are measured in this
repository unless marked as an estimate.

## 1. The honest expectation

| Outcome | Evidence | Our estimate |
|---|---|---|
| Plate read on **every** camera it passes | In 25 h of live collection on 30 cameras, 42 plates read, none on two cameras; a quiet camera gets a frame every ~6 s in light mode | low, about 10-20 % |
| Plate read on **at least one** camera | Fed enough frames, the pipeline confirms 15 of 17 readable plates on grid clips; the organisers will most likely pick a vehicle whose plate is legible | moderate, about 40-60 % |
| A **wrong** route shown | A plate is confirmed only with 4+ agreeing frames, a full Indian-format read and a plate at least 40 px wide; everything weaker is shown as candidate or possible | very low, by design |

Why reading is hard on this grid, with evidence anyone can check:
- **Most plates are physically unreadable.** By-eye ground truth on grid clips (`data/eval/anpr_gt/`): most
  plates are 14-31 px wide at 720p and 250-1200 kbps; on cam01, 02, 04, 05, 08 and 09 not one plate in the
  clip is legible to a person.
- **Where reads are lost is logged per minute.** About 90 % of vehicles end with no plate box at all:
  side-on views, headlight glare, vehicles too far away. Saved crops confirm there is no plate to read.
- **The grid serves live only.** It refuses each account for ~20 minutes at a time and replays from the
  start (we switch accounts and resume automatically), and footage cannot be rewound.

## 2. Running the test (operator steps)

1. **Trace a vehicle** page: type the plate, write the reason (audited), **Search sightings**.
2. **Follow this vehicle live** (pursuit mode). From that moment:
   - every camera checks each vehicle it closes against the plate ("is this plate P?"), which accepts a
     blurrier plate than reading one open-ended (measured: 0 false matches in 487 decoy checks);
   - the cameras where it was seen in the last 20 minutes, and the 3 nearest each on the map, get first
     claim on the GPU, so the vehicle gets the frame rate at which we read 15 of 17 plates on clips.
3. As it is seen, **Reconstruct this vehicle's movement**: the route on the map, each point with our
   read time, **the time printed on that camera's footage**, camera, location, department and frames agreed.
4. **Possible sightings** (pursuit matches) appear on the route marked *possible*, with the plate crop.
   Open the crop, confirm or discard. Nothing unconfirmed is presented as fact.
5. Watch **Alerts** too: add the plate to the watchlist and every sighting raises a live alert.

## 3. If the plate does not read: what we show, and why it is still the strongest solution

| Judges will look for | What we demonstrate | Proof |
|---|---|---|
| A route on GIS with timestamps | Rehearsed live: trace of GJ11E5402 (cam06) and GJ39CB0189 (cam30) with map, footage time, camera and department | Trace page, rehearsal 8 Oct |
| That the system does not invent | Possible vs confirmed vs candidate shown apart; implausible legs flagged; every trace reason audited | `track_service.py`, audit log |
| Why a camera missed it | Per-minute log of where plates are lost; snapshot of the vehicle when no plate was visible (incident) | light reader log; NO_PLATE_VISIBLE incidents |
| Live analytics beyond plates | Incidents with snapshots: rider without a helmet, vehicle without a visible plate, wrong side (keep-left rule, Rules of the Road reg. 2/17, MV Act s. 184), possible collision, stopped in lane | Incidents page |
| Honest measurement | Incidents audited by eye on live snapshots; rules that failed were retired (sudden stop) or rebuilt (wrong way: 274 to 6 in a 6 h replay) | `docs/submission.md` |
| Scale and integration | 30 grid cameras on one Mac, federation of two department VMS, onboarding, role and department access, video grants, retention | HLD, scalability note |
| Resilience | Grid refusal windows survived by switching accounts; idle-camera watchdog; clock re-sampling | supervisor log |

## 4. Answers to the questions we expect

- **"Why did you not read it at camera X?"** Show the snapshot or crop: no plate visible / too small /
  glare. The plate box sizes on that camera are in the ground truth; no system reads a 15 px plate.
- **"Could a better model fix it?"** We measured four plate detectors, three readers and YOLO26; the
  grid-trained detector we use finds the yellow commercial plates the generic one misses (3 vs 0 of 70
  missed vehicles). The limit is the pixels, and we show it rather than hide it.
- **"Why not record the feeds and search later?"** The integrator's guide asks for live consumption;
  we read live, and we follow the vehicle live (pursuit) instead of searching copies.
- **"Is the route reliable?"** Every point carries how many frames agreed and whether it was exact,
  a near match or a possible; impossible legs (speed no road vehicle reaches) are flagged, not hidden.
- **"How does this scale to 80,000 cameras?"** One edge reader per site, readers sign in per department,
  central keeps only plates, detections and incidents; see the scalability note.

## 5. Before the venue

- Run the trace and a pursuit once on the venue network; if port 8554 is blocked, switch on the HLS
  fallback (`SENTINEL_GRID_HLS_FALLBACK=1`).
- Open the map once so its tiles are cached.
- Keep this Mac on power; the collection supervisor keeps it awake.
