# Scaling Vigentra to ~80,000 cameras

The challenge asks for a strategy to scale securely and reliably to roughly
80,000 cameras across Gujarat. This is that strategy, with the arithmetic
shown, because a scaling plan whose numbers are not visible cannot be argued
with — and the interesting parts of this problem are all arithmetic.

Two things stated up front, so the rest is read correctly.

**What has actually been run.** Vigentra federates 30 live sandbox cameras
today, plus two mock department systems. Everything at that scale is measured.
Everything above it is engineering estimate, and is labelled as such rather
than presented as a result.

**Where the architecture already scales, and where it does not.** The edge tier
scales horizontally today with no design change — that is a property of putting
inference next to the video and shipping only metadata. The central tier has
three specific things that do not scale as written, named in §8. Pretending
otherwise would be the least useful thing this document could do.

**How this plan maps to the brief** (Step 6 of the problems page; FAQs 24, 35):

| Asked for | Section |
|---|---|
| Hardware & software requirements — central, regional and edge compute | §2 |
| Network & bandwidth planning, low-bandwidth strategies | §3 |
| Storage & retention — hot/warm/cold tiers | §4 |
| AI processing capacity — GPU/accelerator sizing | §5 |
| Load balancing, horizontal scaling, monitoring, logging, health checks | §6 |
| High availability, backup and disaster recovery | §7 |
| Statewide rollout plan | §9 |
| Cost-benefit analysis | §10 |

---

## 1. The shape of the problem

80,000 cameras is not one large system. It is four tiers with very different
constraints, and conflating them is what produces the "we will need a lot of
GPUs" answer.

```
CAMERA          80,000 devices, 26 departments, mixed analog and IP
   |            multiple VMS vendors, AMC periods, retention policies
   v
EDGE            inference next to the video. Frames never leave the site.
   |            ~2,700-3,000 workers. THIS is where the compute is.
   v            ------- only metadata crosses this line -------
REGIONAL        aggregation, buffering, adapter federation
   |            ~33 districts
   v
CENTRAL         registry, watchlist, alerts, tracks, audit, dashboards
                one logical service, horizontally replicated
```

The single most important number in this document is the one on the line
marked `only metadata crosses this line`. Everything else follows from it.

## 2. Hardware and software requirements

**Sizing per tier — estimate, for planning.** Counts follow from §5 (edge) and
§6 (central); unit specifications are mid-range server parts, not a quotation.

| Tier | How many | Per unit | Runs |
|---|---|---|---|
| Edge | ~3,000 accelerator nodes, in department sites | 1× T4-class GPU, 8 vCPU, 32 GB RAM, 256 GB SSD | edge-worker containers: capture, detection, ANPR |
| Regional | 33 districts × 2 nodes | 16 vCPU, 64 GB RAM, 2 TB NVMe | adapters for the district's VMS, ingest buffer, local cache of the registry |
| Central | 2 sites (primary + DR) | API: 6–10 replicas of 4 vCPU / 8 GB; database: 2 × 32 vCPU / 256 GB / 10 TB NVMe plus read replicas; object storage for backups and cold data | central-api, PostgreSQL, console |

**Measured at sandbox scale.** The whole platform for 30 live cameras runs on
one laptop (Apple M1, 16 GB): the API, database, console and mock departments
in Docker, and three ANPR readers on the laptop's own GPU.

**Software — all open source, no licensed component:**

| Layer | Stack |
|---|---|
| Edge | Python 3.11, OpenCV + FFmpeg (RTSP over TCP), Ultralytics YOLO11, ONNX Runtime (CRNN-CTC plate readers), ByteTrack |
| Platform | FastAPI, SQLAlchemy 2 (async), PostgreSQL (TimescaleDB and PostGIS at scale, §8) |
| Console | Next.js 14, React 18, Tailwind, Leaflet + OpenStreetMap |
| Operations | Docker today; Kubernetes, one namespace per tier, at scale; Kafka between ingest and matching past a few thousand cameras (§6) |

## 3. Network and bandwidth planning

### Why bandwidth is not the binding constraint

The obvious statewide-CCTV architecture streams every camera to a central
video wall. At 80,000 cameras that is:

| | |
|---|---|
| Per camera, H.264 1080p @ 15 fps | ~2 Mbps |
| 80,000 cameras | **160 Gbps sustained** |
| Per day | ~1.7 petabytes |

That is not a network anyone is going to build, and it is why Model 4 (fully
central VMS) is the most expensive of the reference models to operate.

Vigentra's edge-first design does not carry that traffic. What crosses the
regional boundary is a detection row:

| | |
|---|---|
| One detection (JSON, camera + class + box + timestamp + provenance) | ~400 bytes |
| One plate sighting | ~500 bytes |
| A busy urban camera, sampled every 5th frame | ~15 detections/second peak |
| Per camera, peak | ~6 KB/s = **48 kbps** |
| 80,000 cameras at peak simultaneously | **~3.8 Gbps** |
| Realistic mixed load (most cameras are not busy) | **~600 Mbps – 1 Gbps** |

That is a factor of **40–250× less** than centralising video, and it is a
number the existing state network can carry. The trade is that a central
operator cannot arbitrarily scrub any camera's history — which is the correct
trade, because that capability is also the one with the worst privacy
properties, and Vigentra already treats live viewing as a brokered, per-session,
audited decision rather than an ambient one.

Video is still available. It is pulled on demand, per session, for the cameras
someone has a reason to watch — a handful at a time, not eighty thousand
continuously.

### Low-bandwidth and low-connectivity operation

Camera sites run from dense urban Ahmedabad to border districts a thousand
kilometres out, and the connectivity is not uniform.

- **Edge buffering.** A worker that cannot reach the central API should hold
  detections locally and replay them, not drop them. Detection IDs are already
  deterministic (`camera + instant + class + box`) and ingest is already
  idempotent on them, so replay collapses rather than double-counting — the
  hard part of this is already done, and `tests/test_detections.py` covers it.
  What remains is the local queue itself. **Not yet built.**
- **Backoff, already built.** `grid.ReconnectingCapture` reconnects with
  exponential backoff (2 s → 30 s), tolerates join-time decoder noise, and
  survives the feed loops. Every rule is pinned by a test in
  `tests/test_grid_capture.py`.
- **Sampling as a bandwidth dial.** `YOLO_FRAME_SAMPLE_INTERVAL` trades
  detection density for load, per site, without a redeploy.
- **Fewer, longer connections.** A capture is released when its pass ends, as
  the integrator's guide asks, and passes on cameras that yield plates run
  longer (800–1,200 frames) so a stream is reopened less often.
- **One catalogue read, centrally.** central-api reads the camera catalogue;
  workers capture from the documented stream pattern and never sign in, because
  the gateway allows one web session per account and every extra sign-in ended
  the platform's.
- **RTSP first, HLS for viewing.** Workers capture RTSP and report an
  unreachable port 8554 plainly rather than falling through to an HLS URL that
  needs a browser cookie. HLS serves dashboards and networks that filter 8554;
  a site behind such a filter needs its recorder or a relay to expose RTSP.

**What the sandbox taught about shared gateways.** On 14 September the grid
refused our account in recurring 15–20 minute windows, on both the web
gateway and RTSP, and about six streams per account were available at once.
In production that argues for credentials and stream budgets **per department
and per site**, never one account shared by the whole platform — a single
account is a single point of failure.

## 4. Storage and retention strategy

Vigentra stores no video. Departments keep their own footage under their own
retention policies (7 days, 15 days, or more — they already differ, and the
federation does not need them to agree). What Vigentra stores is metadata.

**Detections.** At ~400 bytes/row, a realistic mixed statewide load produces on
the order of 30–60 billion rows/year if everything is kept. It should not be.
Proposed tiering:

| Tier | Contents | Retention | Store |
|---|---|---|---|
| Hot | detections, sightings, alerts, health | 30 days | PostgreSQL + TimescaleDB, partitioned by day |
| Warm | aggregated counts per camera per hour; sightings only for plates that hit the watchlist | 1 year | same cluster, compressed hypertables |
| Cold | audit log, watchlist history | 7 years | object storage, append-only |

**Plates get their own clock**, and it is shorter than everything else:
`ANPR_PLATE_RETENTION_DAYS`, default 30, already **enforced on read** as well
as by any purge job — so a purge that fails to run cannot quietly extend how
long identifying data stays available. That property is already implemented and
tested; it does not need to be built for scale, only kept.

**The audit log is the one thing that never ages out on a shortened clock.** It
is append-only, it is what makes every other control checkable, and it is small:
one row per act, not per frame.

## 5. AI processing capacity

**Measured.**

| Workload | Hardware | Measured |
|---|---|---|
| YOLO11n object detection | CPU, bundled 4K clip | ~190 ms/frame |
| Track-level ANPR, typical grid camera | Apple M1 GPU, three readers sharing it | 5–10 frames/s per reader (was 3–6: frames are detected at their own resolution and junk plate candidates fell 7x) |
| Track-level ANPR, busiest junctions (cam01, cam30) | same | 0.7–1.2 frames/s before the plate-search floor below |
| Plate-search floor (skip vehicles under 96 px wide), labelled Delhi clip, 300 frames | same | the same 36 readings and 4 of 12 plates exact, in 586 s instead of 878 s |

The floor matters for capacity because the cost of ANPR on a busy wide view is
dominated by plate searches on vehicles too far away to read. Skipping them
until they come closer gives the same readings for less compute.

**Estimate.** A single mid-range GPU (T4-class, ~8 TFLOPS fp16) running batched
YOLO11n at 640 px handles roughly 150–200 inferences/second. Sampling every 5th
frame of a 25 fps feed is 5 inferences/second per camera, so **~30 cameras per
GPU** for object detection. ANPR is heavier — vehicle detection, plate search,
several restorations and two readers per track — and realistically halves
that, so **~15 cameras per GPU** where ANPR is enabled.

That gives, for 80,000 cameras:

| Scenario | GPUs |
|---|---|
| Object detection everywhere | ~2,700 |
| ANPR everywhere as well | ~5,300 |
| **ANPR only where it works** (see below) | **~3,000** |

The third row is the recommendation, and it is not a cost dodge — it is what
the optics allow. On 14 September the engine read 236 plates from 12 of the 30
grid cameras, nearly all of them from near-side views; the wide overview
cameras produced almost nothing however long they were read. Running ANPR on
an overview camera spends a GPU to produce almost nothing.

**So the plan is to classify cameras on onboarding.** Model 1's registry
already holds `installation_purpose`, `camera_type` and `view_direction` for
every camera, and the grid reference data already marks five cameras
`ANPR-capable`. That classification drives which analytics run where — an
operational decision made once at onboarding rather than a blanket setting.

Rough capital estimate, at Indian public-procurement pricing: 3,000 T4-class
accelerators plus host hardware is on the order of **₹90–120 crore** for the
edge tier, deployed over the rollout period rather than at once. That is an
estimate for planning, not a quotation.

## 6. Load balancing, horizontal scaling and monitoring

The central API is stateless for ingest, so it scales the ordinary way: run N
replicas behind a load balancer, and let PostgreSQL be the thing that needs
care.

| Concern | Approach |
|---|---|
| Ingest throughput | ~1.2 M detections/minute statewide at peak. Batched inserts (already), partitioned tables, and a write path that touches no shared row. |
| Load balancing | Layer-7 balancer in front of stateless API replicas; sessions are signed tokens, so any replica serves any request. |
| Read/write split | Dashboards, reports and route queries go to read replicas. Ingest goes to the primary. |
| Alert fan-out | The matcher runs in the ingest transaction today. Past a few thousand cameras it moves to a Kafka topic between ingest and matching — the interface is already a single function call over a list of `PlateRead`, so this is a substitution rather than a rewrite. |
| Orchestration | Kubernetes, one namespace per tier, autoscaling on ingest queue depth rather than CPU. |

**Monitoring, logging and health checks — built today:**

- A health monitor polls every department system and every camera on a fixed
  interval and keeps the history (`camera_health`), not just the latest state,
  with the source's last error and latency.
- Camera liveness follows the video: when the grid's web gateway refuses but
  its RTSP server answers, cameras stay online on the last good catalogue
  instead of all going offline.
- Every service has a health endpoint and a container health check; a source
  outage marks only that source's cameras offline, with the reason.
- Each ANPR pass logs its camera, frames, frames per second and plates read;
  a supervisor restarts a reader that exits for any reason.
- The audit log records every act, including refusals and the matcher's own
  alerts.

**At scale — planned:** Prometheus metrics and Grafana dashboards; alerts on
the share of cameras offline, ingest lag and reader frames per second;
centralised logs (Loki or ELK) with the audit log kept separate and
append-only.

## 7. High availability, backup and disaster recovery

Targets — design goals, not yet tested at scale: **RPO 5 minutes, RTO 1 hour.**

| Layer | How it survives a failure |
|---|---|
| Database | Primary with a synchronous standby per region; continuous WAL archiving for point-in-time recovery to object storage; nightly base backups; an asynchronous replica at the DR site. |
| API and console | Stateless replicas across availability zones; a failed replica is replaced by the orchestrator without losing work. |
| Registry | Reconstructible from the department systems by re-running sync — a real advantage of federation: the authoritative copy of every camera record still lives with its owner. |
| Edge | Workers hold no state that matters; deterministic detection IDs make replay safe once the local queue exists (§3). Readers run under a supervisor that restarts them. |
| Audit log | Append-only, replicated with the database, and copied to cold object storage for seven years. |

The DR failover is **tested, not just designed**, in rollout phase 3 (§9).

## 8. What does not scale as written, and what replaces it

Three things. Naming them is more useful than a diagram that implies they are
solved.

**Fuzzy plate search is O(n) in Python.** `track_service.reconstruct` and
`search_plates` narrow candidates by time and scope in SQL, then compute
confusion-weighted edit distance row by row. At sandbox scale that is
comfortably fast. At statewide scale it is not.

*Replacement:* a trigram index (`pg_trgm`) on `plate_normalised` as a cheap
prefilter, cutting candidates by two or three orders of magnitude before the
weighted distance runs on what survives. The weighted distance is what gives
the ranking its quality and should not be replaced by trigram similarity — it
should be fed by it. A generated column holding a canonicalised form (digits
folded to their most likely letter and vice versa) narrows it further.

**The watchlist matcher loads the full active list per batch.** Cached for ten
seconds and invalidated on write, which is right for a list of hundreds. For a
list of hundreds of thousands it becomes a per-batch scan.

*Replacement:* a bloom filter over exact plates for the common negative case,
plus the trigram prefilter above for near matches. Exact hits are the majority
of real alerts and should not pay for fuzzy matching at all.

**Coordinates are plain `float` columns, not PostGIS.** Fine for the map we
render and for 30 cameras. "Nearest camera to this point", "cameras within this
polygon" and route-corridor queries all get materially harder without it — and
those are exactly the queries a mature tracking feature wants.

*Replacement:* PostGIS with a GiST index on a `geography(Point, 4326)` column.
This is the organisers' own suggested stack, and it is a migration rather than
a redesign because every write already goes through one mapper.

## 9. Statewide rollout plan

Phased, and ordered so that each phase is useful on its own rather than being a
step toward something that only works at the end.

| Phase | Scope | What it proves |
|---|---|---|
| 1 | ~50 cameras, one district, all four models exercised | The integration works end to end on real feeds. **This is the hackathon.** |
| 2 | ~2,000 cameras, 3 districts, 2 departments | Regional aggregation, adapter federation across genuinely different VMS vendors, a real operations rhythm |
| 3 | ~15,000 cameras, all districts, Home Department only | Statewide network load, DR failover tested rather than designed |
| 4 | ~80,000 cameras, all 26 departments | Steady state |

Model 1 — the registry and GIS foundation — deploys first and in full at every
phase, because everything else needs to know what cameras exist and who owns
them. It is also the phase that produces value with no analytics at all: an
accurate statewide CCTV inventory with coverage gap analysis is useful on the
day it exists, and nobody currently has one.

## 10. Cost-benefit

The costs are dominated by one line; the benefits by what is not built.

**Costs (estimates).** The edge accelerators of §5 — **₹90–120 crore**, spread
across the rollout phases. The central and regional tiers of §2 are ordinary
server and storage procurement, small beside the edge tier, and are priced at
tender rather than guessed here.

**What the design avoids buying:**

| Avoided | Central-video design | Vigentra | Why |
|---|---|---|---|
| Sustained WAN into the state data centre | ~160 Gbps — sixteen 10 Gbps links | ~1 Gbps — one link | only metadata crosses (§3) |
| Central video storage | ~1.7 PB per day | none | departments keep their footage (§4) |
| VMS replacement | 26 departments migrated | none | adapters federate what exists (HLD §4) |

**What it delivers, and when:** an accurate statewide camera inventory with
coverage-gap analysis from phase 1, before any analytics; vehicle routes
assembled across departments in one query instead of footage requests to each;
and watchlist alerts raised at the moment of the read, with a complete audit
trail of who saw what.

## 11. Security at scale

The controls do not change with camera count. They are listed here because
"how does this stay safe at 80,000 cameras" has a specific answer, and the
answer is that none of it is per-camera work.

- **Feed credentials never reach the console.** No RTSP URL, NVR address,
  credential or media token is ever sent to a browser — at 30 cameras or
  80,000. Viewing goes through the broker.
- **Every footage session is brokered, opaque, short-lived and re-authorised
  per segment.** Revoking a grant stops a playing stream within one segment.
- **Scope is three-dimensional** (department, city, zone) and checked per
  request, not per session.
- **The audit log is append-only and complete**, including refusals. A denied
  attempt is the record you most want to survive, so audit rows commit
  independently of the request that produced them.
- **Plates and tracks carry their own permissions and their own retention**, as
  set out in `docs/access-model.md` §9.

The thing that scales badly in most surveillance systems is not the compute —
it is the number of people who can see everything. That is governed here by
role scope and grant, and neither gets looser as cameras are added.
