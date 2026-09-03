# Scaling Vigentra to ~80,000 cameras

The challenge asks for a strategy to scale securely and reliably to roughly
80,000 cameras across Gujarat. This is that strategy, with the arithmetic
shown, because a scaling plan whose numbers are not visible cannot be argued
with — and the interesting parts of this problem are all arithmetic.

Two things stated up front, so the rest is read correctly.

**What has actually been run.** Vigentra federates 30 live sandbox cameras
today, plus two mock department systems. Everything below 30 is measured.
Everything above it is engineering estimate, and is labelled as such rather
than presented as a result.

**Where the architecture already scales, and where it does not.** The edge tier
scales horizontally today with no design change — that is a property of putting
inference next to the video and shipping only metadata, and it was true before
this document existed. The central tier has three specific things that do not
scale as written, named in §7. Pretending otherwise would be the least useful
thing this document could do.

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
   |            ~2,700 workers. THIS is where the compute is.
   v            ------- only metadata crosses this line -------
REGIONAL        aggregation, buffering, adapter federation
   |            ~33 districts
   v
CENTRAL         registry, watchlist, alerts, tracks, audit, dashboards
                one logical service, horizontally replicated
```

The single most important number in this document is the one on the line
marked `only metadata crosses this line`. Everything else follows from it.

## 2. Why bandwidth is not the binding constraint

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

## 3. Edge compute: where the money goes

Measured on the bundled 4K clip, CPU only:

| Workload | Measured |
|---|---|
| YOLO11n object detection | ~190 ms/frame |
| Consensus ANPR, full pipeline | not yet measured on grid footage — see `docs/anpr.md` §8 |

Sampling every 5th frame of a 25 fps feed is 5 inferences/second per camera.

**Estimate.** A single mid-range GPU (T4-class, ~8 TFLOPS fp16) running batched
YOLO11n at 640px handles roughly 150–200 inferences/second, so **~30 cameras
per GPU** for object detection. ANPR is heavier — the ROI pass plus several
restoration variants plus OCR — and realistically halves that, so **~15 cameras
per GPU** where ANPR is enabled.

That gives, for 80,000 cameras:

| Scenario | GPUs |
|---|---|
| Object detection everywhere | ~2,700 |
| ANPR everywhere as well | ~5,300 |
| **ANPR only where it works** (see below) | **~3,000** |

The third row is the recommendation, and it is not a cost dodge — it is what
the optics allow. A camera positioned for ANPR (near-side approach, plate
filling a meaningful fraction of the frame) reads plates well. A general-purpose
overview camera mounted for situational awareness does not, and running ANPR on
it spends a GPU to produce almost nothing. `docs/anpr.md` §8 records one plate
per 67 vehicles on wide footage with the single-frame reader.

**So the plan is to classify cameras on onboarding.** Model 1's registry
already holds `installation_purpose`, `camera_type` and `view_direction` for
every camera, and the grid reference data already distinguishes red-light
violation units from general PTZ and bullet cameras. That classification drives
which analytics run where — an operational decision made once at onboarding
rather than a blanket setting.

Rough capital estimate, at Indian public-procurement pricing: 3,000 T4-class
accelerators plus host hardware is on the order of **₹90–120 crore** for the
edge tier, deployed over the rollout period rather than at once. That is an
estimate for planning, not a quotation.

## 4. Storage

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

## 5. Network and low-connectivity operation

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
- **HLS where RTSP is filtered.** Already implemented: port 8554 is blocked on
  many networks, and the integrator's guide sanctions HLS for exactly that
  case. The worker probes and falls back.

## 6. Central tier

The central API is stateless for ingest, so it scales the ordinary way: run N
replicas behind a load balancer, and let PostgreSQL be the thing that needs
care.

| Concern | Approach |
|---|---|
| Ingest throughput | ~1.2 M detections/minute statewide at peak. Batched inserts (already), partitioned tables, and a write path that touches no shared row. |
| Read/write split | Dashboards, reports and route queries go to read replicas. Ingest goes to the primary. |
| Alert fan-out | The matcher runs in the ingest transaction today. Past a few thousand cameras it moves to a Kafka topic between ingest and matching — the interface is already a single function call over a list of `PlateRead`, so this is a substitution rather than a rewrite. |
| High availability | Primary + synchronous standby per region; PITR to object storage. |
| Disaster recovery | RPO 5 min, RTO 1 hour. The registry is reconstructible from the department systems by re-running sync, which is a real advantage of the federation design: the authoritative copy of every camera record still lives with its owner. |
| Orchestration | Kubernetes, one namespace per tier, HPA on ingest queue depth rather than CPU. |

## 7. What does not scale as written, and what replaces it

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
a redesign because every write already goes through one mapper. It was flagged
in the readiness audit as a decision to take early, and taking it is still the
recommendation.

## 8. Rollout

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

## 9. Security at scale

The controls do not change with camera count. They are listed here because
"how does this stay safe at 80,000 cameras" has a specific answer, and the
answer is that none of it is per-camera work.

- **Feed credentials never centralise.** Vigentra stores no RTSP URL, no NVR
  address, no credential, no media token — at 30 cameras or 80,000. A breach of
  the central tier does not yield a key to any camera.
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
