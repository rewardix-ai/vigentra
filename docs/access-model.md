# Sentinel access model

This is the load-bearing statement of who can see what, why, and how each
guarantee is enforced in code.

One sentence up front, because everything else follows from it:

> **Seeing that a camera exists is a federation question. Watching what it
> sees is the owning unit's decision.**

Those two questions used to be answered together, and answering them together
was the mistake. Camera *metadata* was gated behind a departmental approval
step — so a Traffic officer could not learn that a Municipal camera covered the
junction they were investigating. That made the central registry useless for
the case it exists to serve, while doing nothing for privacy: knowing a camera
is mounted at a public junction is not sensitive. The sensitive thing is the
footage, and the footage was governed by the same single gate.

They are now separate. Metadata federates automatically. Footage is asked for.

---

## 1. The owning department controls actual footage

Every camera is owned by a department: Traffic Police, Municipal Corporation,
or any other that federates later. That department:

- runs its own CCTV/VMS platform;
- holds the NVR recordings, the RTSP addresses, the camera credentials and any
  local viewing tokens;
- decides whether Sentinel may broker its cameras at all
  (`video_access_enabled`, which no Sentinel role can override);
- retains the raw video for its own configured retention period.

Sentinel never stores a stream URL, an NVR address, a credential or a media
token. When a session is permitted, bytes are proxied through
`GET /api/v1/streams/{session_id}` — an opaque, Sentinel-owned address — and
the upstream ticket never leaves the server.

## 2. Installation form creates a camera asset record

A department installer completes a CCTV installation form describing the
camera's identity, ownership, location, technical characteristics, and the
local viewing roles that apply once it is commissioned.

The form lives in the owning department's system as a `DRAFT`. Sentinel mirrors
the record so it is listable and auditable centrally, but the authoritative
copy stays with the department.

## 3. Validation, not approval, gates the registry

```
DRAFT
  ↓ submit
SUBMITTED
  ↓ the department's own validation
VALIDATION_FAILED  ─→  (correct the fields, resubmit)
REGISTERED         ─→  (metadata sync)  ─→  SYNCHRONIZED
SUSPENDED          ─→  (still in registry, marked unavailable)
DECOMMISSIONED     ─→  (retained for record)
```

There is no approval state. The unit had already decided to install the
camera; asking it to also approve its own paperwork added delay without adding
a decision, and left cameras invisible to everyone else in the meantime.

What validation still catches is real: missing required fields, non-numeric or
out-of-jurisdiction coordinates, a duplicate device ID against a camera already
commissioned, an empty local-role list. Because validation is now the *only*
gate, both mock department systems check that coordinates fall inside Gujarat
rather than merely inside the range −90..90 — a transposed digit used to be
something an approver would have caught by eye.

Only `REGISTERED` and `SYNCHRONIZED` records enter the central registry; see
`schemas.PUBLISHABLE_STATUSES`. `SUSPENDED` and `DECOMMISSIONED` records are
retained and marked unavailable, so a withdrawn camera never silently
disappears.

## 4. The registry is federation-wide; the depth is not

Any account with `registry:read` sees every camera in the federation. What
varies is how much of each record it gets — `policy_service.effective_visibility`:

| Reader | Visibility |
|---|---|
| Own department | Whatever the role allows (`full` / `standard` / `limited`) |
| Any other department | Capped at `standard`, whatever the role |

The cap withholds the owning unit's internal operational data: masked serial,
installation vendor, maintenance agency, site-survey attachments. A department
admin is senior *inside their department*, not inside someone else's. Withheld
fields are returned by name in `redacted_fields` so the UI can say "withheld
for your role" rather than showing a blank.

**Derived observations are not metadata.** Detections and events describe what
a camera *saw* — "two motorcycles and a person at this junction at 21:14" is
content about a place at a time, not an asset record. They follow the video
rules and stay with the owning department, via
`policy_service.may_read_detections`, which is deliberately a separate
predicate from `may_read_camera`. Reusing one predicate for both is exactly how
opening the registry would have silently handed every account the whole state's
detection history.

## 5. Footage: authorised, scoped, brokered, audited

`services/video_permissions.py::evaluate` is the single decision point. In
order:

1. account active;
2. role permits viewing at all. `system_admin` does, on every camera: it is
   statewide, so it never takes the ask-a-grant path. That is deliberate - the
   account that operates the broker has to be able to verify a feed - and the
   audit trail is what holds it, not a narrower permission. `state_admin` and
   `city_admin` do NOT, unless `SENTINEL_STATE_ADMIN_VIDEO` /
   `SENTINEL_CITY_ADMIN_VIDEO` is set, so a broad oversight account does not
   silently become a viewing account;
3. camera state — commissioned, owner-enabled, and not offline. Checked
   **before** scope, so a grant can never smuggle access to a suspended camera,
   and an in-scope operator gets the accurate reason;
4. own unit, **or** an active grant from the owning unit;
5. city and zone scope (own unit only);
6. which modes survive — live, playback, or neither.

A refusal is a `403 VIDEO_ACCESS_DENIED` carrying a `state` that distinguishes
the kinds of no:

| `state` | Meaning |
|---|---|
| `needs_unit_approval` | Another unit owns it. **Ask them** — the body names the department and the endpoint. |
| `not_enabled_by_owner` | The owner has brokered video off for this camera. Nothing to request. |
| `camera_unavailable` | Suspended, decommissioned or offline. |
| `denied` | Role, city or zone scope. |

Collapsing `needs_unit_approval` into a flat `denied` costs the operator a
support ticket, which is why the state is carried through rather than dropped.

Permitted sessions are short-lived (clamped to the upstream ticket's own
expiry), watermarked with username and timestamp, bound to the operator who
opened them, and re-authorised on every range request — so revoking a grant
stops a stream already playing at its next read, not at session expiry.

### Live and recorded

**Live** runs continuously for as long as the session holds.

**Recorded playback** takes a date and a from/to time. The request goes to the
owning department, which returns the segment of its recording covering that
window; the player receives it as a media fragment, so a 09:00–09:10 request
and a 02:00–02:15 request return visibly different footage, and the same
window twice returns the same footage.

There is **no quota** on playback: an operator may ask for as many different
windows as the investigation needs. What bounds it is the owning unit, not a
counter:

| Limit | Where it comes from |
|---|---|
| Retention | The camera's own `retention_days`. Older footage is *gone*, and the refusal says so — `422`, not `403`, because nothing is being withheld. |
| Window width | `VIDEO_PLAYBACK_MAX_WINDOW_MINUTES` (default 60). |
| No future windows | Footage only exists for the past. |
| Grant revoked, camera suspended, brokering switched off | Any of these stops the next request, and any stream already running. |

## 6. Asking the owning unit

`services/video_grants.py` and `routers/video_grants.py`.

```
POST   /api/v1/video-access-requests             ask (reason mandatory)
GET    /api/v1/video-access-requests             what I asked / what I owe
POST   /api/v1/video-access-requests/{id}/grant  the owner says yes
POST   /api/v1/video-access-requests/{id}/deny   the owner says no
DELETE /api/v1/video-access-requests/{id}        withdraw, or revoke
```

The properties that make this a real control rather than a formality:

- **Only the owning unit decides.** `video:grant` over another department's
  camera is refused; a requester cannot self-serve.
- **A grant is personal.** It names one username, not a department. A colleague
  who did not ask gets nothing.
- **A reason is mandatory** (min 5 characters) and goes to the audit trail. A
  viewing nobody can explain later is what the trail exists to prevent.
- **Grants are time-boxed** (`VIDEO_GRANT_DEFAULT_DAYS`, default 7). An
  open-ended grant is a standing permission nobody revisits.
- **The owner can narrow on the way through** — asked for live and playback,
  granted playback only.
- **Either side can end it.** The owner revokes; the requester withdraws.
- **No grant is needed inside your own unit.** Department, city and zone scope
  already answer that question; a grant on top would be a second, weaker answer.

## 7. Everything is audited

`installation_form_created`, `installation_form_submitted`,
`installation_request_suspended`, `installation_request_decommissioned`,
`camera_metadata_synchronized`, `camera_registry_viewed`,
`camera_details_viewed`, `access_policy_viewed`, `camera_health_viewed`,
`audit_viewed`, `video_access_requested`, `video_access_granted`,
`video_access_refused`, `video_access_revoked`, `video_session_opened`,
`video_stream_accessed`, `video_access_denied`.

Each lands in `audit_logs` with operator, role, department, resource identifier
and outcome. Denials are recorded as deliberately as successes.

## 8. Number plates

ANPR was originally out of scope and was added deliberately. Because a
registration number identifies a vehicle and, through the registry, a person,
it is the most sensitive thing the platform produces and is treated separately
from the detection that carries it.

| Control | How |
|---|---|
| Off by default | The edge worker reads plates only when `ANPR_ENABLE` is set. |
| Vehicles only | A person detection is never cropped or read. |
| No guesses stored | A read that does not parse as an Indian registration — including a state code that is not a real state — is discarded at the edge and never transmitted. Half-read text looks like evidence and is not. |
| Its own permission | `plate:read`. Held by traffic operators, department admins, the platform account and auditors. **Not** by the AI operator that ingests them, nor by statewide metadata viewers. |
| Withheld, not refused | An account without the permission still gets the detection, with `plate_withheld: true`. Counting vehicles and identifying their owners are different questions. |
| Shorter retention | `ANPR_PLATE_RETENTION_DAYS`, default 30, enforced on read as well as by any purge — so a failed purge cannot quietly extend how long identifying data is available. |
| Audited disclosure | `plate_data_viewed`, recorded only when a plate is actually disclosed. Seeing that a vehicle passed is not the same act as learning which vehicle it was. |
| Never leaves the device as an image | OCR runs at the edge. The central API receives characters and a box, never a frame. |

See [`docs/anpr.md`](anpr.md) for setup and the accuracy caveats.

## 9. Watchlist matching and movement history

This section describes a **reversal**. The previous version of this document
said, plainly, that watchlist matching and cross-camera identity association
were out of scope, and that a plate was an observation at one camera that was
never followed anywhere. That is no longer true, and pretending otherwise in a
document whose whole job is to state what the system does would be worse than
the change itself.

### Why it changed

The platform is answering a policing problem, and the problem it is being
measured against is: given a registration number, show where that vehicle has
been across the network, and raise an alert the moment a watchlisted vehicle is
seen. Those are the two things the earlier scope specifically excluded. A
system that federates fifty cameras, reads plates off them, and then refuses to
answer "where did this stolen car go" is not a cautious system — it is an
incomplete one, and it leaves the question to be answered by someone exporting
rows into a spreadsheet, where none of the controls below exist.

So the capability moved in scope, deliberately, in the same way ANPR did: as a
decision on the record, with the controls designed at the same time rather than
retrofitted after someone asks.

### What it does

- Every ingested plate becomes a **sighting**: one vehicle, read once, at one
  camera, at one instant. Stored separately from the detection that produced
  it, because a detection is an observation of an object and a sighting is an
  assertion about an identity — different retention, different permissions,
  different consequences when wrong.
- Each sighting is matched against the **active watchlist** on ingest. A hit
  raises an **alert**.
- A **track** orders one plate's sightings by time and reconstructs the route.

### What it still does not do

| Not done | Why |
|---|---|
| Face recognition, biometrics, gait | Out of scope, and staying out. |
| Vehicle re-identification by appearance | A track is built from plate reads **only**. A vehicle whose plate was not read contributes nothing to its own route — which understates movement rather than inventing it, and that is the correct direction to be wrong in. |
| Any join to the vehicle reference registry | The registry is still a standalone reference dataset with no owner column. A sighting is never joined to it. |
| Automatic enforcement | An alert reaches a human, and stops. Nothing is issued, no barrier moves, no notice is generated. |
| Retroactive alerts | Matching happens at ingest. A watchlist entry added *after* a vehicle passed does not manufacture an alert for that pass. An alert asserts that the system knew at a moment in time, and a query-time matcher cannot make that claim. |

### The controls

| Control | How |
|---|---|
| Four permissions, not one | `watchlist:read` (oversight), `watchlist:manage` (a standing statewide instruction), `alert:read` / `alert:acknowledge` (operational), `track:read` (the most revealing query here). A role may hold any without the others. |
| Municipal holds none of them | Civic monitoring counts vehicles. It does not identify their owners, and it does not trace them. |
| The edge account holds none of them | `ai_operator` ingests plates and raises alerts as a side effect. A worker that can read the watchlist back is a worker that can exfiltrate it. |
| A watchlist entry needs a reason | Mandatory, non-blank, recorded with the username. A standing instruction to flag a vehicle statewide that nobody can account for is the one that should never have been added. |
| An entry should have an end date | Optional but pressed for in the UI, and expiry is applied **on read** rather than by a purge job — so a job that fails to run cannot quietly keep a vehicle flagged for ever. |
| Entries are stood down, never deleted | Deactivation records who and why. The trail is the point. |
| A trace needs a stated reason | Enforced at the API, not in the UI. `GET /plates/{plate}/track` refuses without one, and the reason is written to the audit log with the plate, the account and the time. |
| Camera scope applies before assembly | A route is never built from a sighting the caller could not have read. Filtering after assembly would produce a route with holes attributed to the vehicle rather than to the reader's permissions. |
| Plates withheld, alerts not refused | An account with `alert:read` but not `plate:read` sees that a stolen-category vehicle was flagged at a camera, with `plate_withheld: true`, and not which vehicle. The operational fact and the identifying fact are separable, so they are separated. |
| Near matches are stored and shown | Matching is fuzzy by necessity (see below). The distance that admitted each hit is recorded, and the console shows exact and near differently. Suppressing near matches to keep a console tidy is how a stolen vehicle passes a camera and nobody hears. |
| Impossible legs are flagged, not dropped | If two consecutive sightings imply 400 km/h, one of the reads is probably a different vehicle. That is a finding about the track's reliability, so it is surfaced in the API, the table and the map — not quietly removed to make the line look cleaner. |
| Everything is audited | `watchlist_entry_added`, `watchlist_entry_deactivated`, `watchlist_viewed`, `watchlist_alert_raised`, `alerts_viewed`, `alert_acknowledged`, `alert_dismissed`, `plate_searched`, `vehicle_movement_viewed`. Note that `watchlist_alert_raised` is written by the machine, not by a person: the trail has to show what the system concluded as well as what people did. |

### Why matching is fuzzy, and why that is the safer choice

Exact-match lookup over OCR output is a trap. If the only camera that saw a
vehicle read one character wrong, an exact query returns nothing and the
vehicle looks like it was never there.

But plain edit distance is too blunt — it rates `GJ01AB1234` equally far from
`GJ01A81234` and `GJ01AX1234`, when the first is a pair the reader is known to
swap and the second is not. So substitutions are priced by whether the two
glyphs are a known confusion pair (0.35) or unrelated (1.0), and a dropped
character is priced below an unrelated substitution (0.5) because losing a
character to glare or a frame edge is a commonplace OCR failure while
substituting a different digit usually means a different car.

The default threshold is 1.0: about two plausible OCR errors. Raising it does
not find more stolen vehicles. It finds more innocent ones.

### The line that has not moved

An alert is never an identification, and the system says so on every surface
that shows one. It is a probabilistic reading of a photograph, matched fuzzily
against a list, and if it matters to a case a human should look at the frame.
Nothing here is evidence on its own.

## 10. What is still out of scope

Face recognition, biometric identification, gait recognition, make/model/colour
verification, vehicle re-identification by appearance, any join between a
sighting and the vehicle reference registry, and any automatic enforcement
action.

Object detection is generic (person / vehicle class / bicycle), runs at the
edge, and the central API carries no computer-vision dependency at all — the
plate matcher included, which works on characters and never on pixels.
