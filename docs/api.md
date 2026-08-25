# Sentinel Central API — specification

**Base URL** — `http://localhost:8000` in the demo · `http://central-api:8000` inside Compose.
**Auth** — `Authorization: Bearer <token>` on every route except `/`, `/health` and
`/api/v1/auth/login`. The dashboard exchanges the token for an httpOnly cookie
before letting the browser make requests.
**Content type** — `application/json` on request bodies; every response is JSON.
**Timestamps** — every timestamp field is ISO-8601 UTC with a trailing `Z`.
**Access boundary** — every response carries `X-Sentinel-Access-Model: METADATA_ONLY`
and `X-Sentinel-Video-Access: NOT_AVAILABLE`. Module 1 exposes no video path.

The interactive OpenAPI is at [`/docs`](http://localhost:8000/docs); this file
is the human-readable version, per-endpoint, with sample bodies.

---

## Roles and permissions

Every route below is annotated with the permission it requires. A role holds a
fixed set of permissions:

| Role | Permissions |
|---|---|
| `state_admin` | `audit:read`, `detection:read`, `health:read`, `installation:decommission`, `installation:read`, `installation:suspend`, `installation:sync`, `policy:read`, `registry:read` |
| `state_registry_viewer` | `health:read`, `policy:read`, `registry:read` |
| `city_admin` | `audit:read`, `detection:read`, `health:read`, `installation:read`, `policy:read`, `registry:read` |
| `department_admin` | `audit:read`, `detection:read`, `health:read`, `installation:read`, `installation:sync`, `policy:read`, `registry:read`, `video:grant`, `video:live`, `video:playback`, `video:request` |
| `traffic_operator` | `detection:read`, `health:read`, `policy:read`, `registry:read`, `video:live`, `video:playback`, `video:request` |
| `municipal_operator` | `detection:read`, `health:read`, `policy:read`, `registry:read`, `video:live`, `video:playback`, `video:request` |
| `health_monitor` | `health:read`, `registry:read` |
| `auditor` | `audit:read`, `detection:read`, `health:read`, `installation:read`, `policy:read`, `registry:read`, `vehicle:read` |
| `ai_operator` | `detection:ingest`, `detection:read`, `health:read`, `registry:read`, `video:live`, `video:playback` |
| `vehicle_registry_viewer` | `vehicle:read` |
| `installation_operator` | `health:read`, `installation:create`, `installation:read`, `installation:submit`, `installation:update`, `policy:read`, `registry:read` |
| `video_access_approver` | `health:read`, `installation:decommission`, `installation:read`, `installation:suspend`, `installation:sync`, `policy:read`, `registry:read`, `video:grant` |
| `system_admin` | `audit:read`, `detection:read`, `health:read`, `installation:decommission`, `installation:read`, `installation:suspend`, `installation:sync`, `policy:read`, `registry:read`, `vehicle:read`, `video:live`, `video:playback`, `video:request` |

Two permissions carry the access model:

- `video:request` — may **ask** another unit for footage.
- `video:grant` — may **answer** such a request, but only for cameras the
  account's own department owns.

`registry:read` is federation-wide: it returns every camera in the federation.
How much of each record comes back depends on whether the reader is in the
owning department (see redaction, below), and whether the footage is viewable
is a separate decision entirely.

Accounts also carry a **department scope**: statewide (`*`) or a single
department name. A departmental account never sees records owned by another
department, regardless of role.

## Error envelope

FastAPI 4xx/5xx responses:

```json
{ "detail": "Human-readable message" }
```

Adapter-translated upstream failures use a structured detail body:

```json
{
  "detail": {
    "code": "source_unavailable",
    "message": "traffic_vms is unreachable",
    "source_system": "traffic_vms",
    "upstream_status": null,
    "detail": "…",
    "retryable": true
  }
}
```

Codes: `source_unavailable`, `source_timeout`, `source_auth_failed`,
`source_state_conflict`, `source_validation_failed`, `source_rate_limited`,
`resource_not_found`, `upstream_protocol_error`.

Footage refusals carry a `state` distinguishing the kinds of no, so a client
can offer the request instead of a dead end:

```json
{ "detail": { "code": "VIDEO_ACCESS_DENIED", "state": "needs_unit_approval",
              "owning_department": "Municipal Corporation",
              "request_access_at": "/api/v1/video-access-requests" } }
```

---

## Table of contents

- [System](#system) — `/`, `/health`
- [Identity](#identity) — `/api/v1/auth/*`
- [Departments](#departments) — `/api/v1/sources`
- [Registry](#camera-registry) — `/api/v1/cameras`
- [Overview](#overview) — `/api/v1/overview`
- [Installation onboarding](#installation-onboarding) — `/api/v1/installation-requests/*`
- [Events](#events) — `/api/v1/events`, `/api/v1/events/correlation`
- [Reports](#reports) — `/api/v1/reports/gap-analysis`
- [Audit](#audit) — `/api/v1/audit`
- [Video (refused)](#video-refused) — the four routes that always return 403

---

## System

### `GET /`

Service banner. Includes what Module 1 explicitly excludes, so an integrator
cannot mistake this service for a video gateway.

### `GET /health`

Platform health. Unauthenticated. Query params: `deep=true|false` (default
true) — deep mode also probes every department system.

```json
{
  "status": "ok",
  "service": "sentinel-central-api",
  "version": "0.2.0",
  "environment": "DEMO / MODULE 1",
  "module": "Module 1 - metadata-only CCTV registry federation",
  "access_model": "METADATA_ONLY",
  "video_access": "NOT_AVAILABLE",
  "time_utc": "2026-08-19T06:52:33Z",
  "dependencies": [
    { "name": "database", "status": "up", "latency_ms": 2.1 },
    { "name": "traffic_vms", "status": "up", "latency_ms": 12.4 },
    { "name": "municipal_vms", "status": "up", "latency_ms": 15.3 }
  ]
}
```

`status` is `ok` when everything is up, `degraded` when the database is up but
at least one department system is down, `down` when the database is out.

---

## Identity

### `POST /api/v1/auth/login`

Unauthenticated. Exchanges demo credentials for a bearer token.

Request:
```json
{ "username": "system.admin", "password": "SysAdmin@2026" }
```

Response (200):
```json
{
  "access_token": "eyJ…",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": {
    "username": "system.admin",
    "display_name": "Sentinel Administrator",
    "role": "system_admin",
    "department": "*",
    "unit": null,
    "permissions": ["audit:read", "health:read", "installation:decommission", "…"],
    "visibility_level": "full",
    "sentinel_video_access": false
  }
}
```

### `GET /api/v1/auth/me`

Requires: valid token. Returns the signed-in user record.

---

## Departments

### `GET /api/v1/sources`

Requires: `registry:read`. Lists federated department systems. Scoped by
department — a departmental account sees only its own.

Response:
```json
[
  {
    "source_system": "traffic_vms",
    "display_name": "Traffic VMS",
    "department": "Traffic Police",
    "adapter": "traffic_adapter",
    "adapter_version": "0.2.0",
    "status": "online",
    "camera_count": 2,
    "pending_requests": 1,
    "endpoint": "traffic-vms:8001",
    "last_sync_at": "2026-08-19T06:52:15Z",
    "last_success_at": "2026-08-19T06:52:15Z",
    "last_error": null,
    "latency_ms": 16.1
  }
]
```

### `POST /api/v1/sources/sync`

Requires: `installation:sync`. Runs a full synchronisation of REGISTERED camera
metadata from every permitted department. Records still in the pipeline are
counted and skipped. Failure isolation: one source going down never fails the
run for another.

Response:
```json
{
  "success": true,
  "metadata_only": true,
  "sources": {
    "traffic_vms": {
      "approved_records_seen": 2, "synchronized": 2, "skipped_unregistered": 1,
      "withdrawn": 1, "created": 0, "updated": 2, "errors": [],
      "latency_ms": 16.1, "status": "online"
    },
    "municipal_vms": { "…": "…" }
  },
  "total_cameras": 6,
  "synced_at": "2026-08-19T06:52:15Z"
}
```

---

## Camera registry

### `GET /api/v1/cameras`

Requires: `registry:read`. Lists canonical cameras. Query params:
`source_system`, `owning_department`, `district`, `status` (health),
`installation_status`, `approval_status`, `q`, `limit`, `offset`.

Response (abridged):
```json
[
  {
    "camera_id": "SENTINEL-TRAFFIC-AHM-0001",
    "external_camera_id": "TRF-AHM-0001",
    "source_system": "traffic_vms",
    "owning_department": "Traffic Police",
    "owning_unit": "Ahmedabad Traffic Zone 1",
    "name": "CG Road East",
    "camera_type": "fixed",
    "location": {
      "district": "Ahmedabad",
      "road_or_junction": "CG Road",
      "landmark": "CG Road East Junction",
      "latitude": 23.0225, "longitude": 72.5714,
      "view_direction": "eastbound"
    },
    "installation": {
      "installation_date": "2026-08-01",
      "commissioning_date": "2026-08-05",
      "installation_status": "COMMISSIONED",
      "installation_request_id": "TRF-REQ-0001"
    },
    "approval": {
      "status": "SYNCHRONIZED",
      "approved_by_role": null,
      "approved_at": "2026-08-06T04:00:00Z"
    },
    "access_policy_summary": {
      "local_video_access": true,
      "sentinel_video_access": false,
      "permitted_local_roles": ["department_operator", "district_supervisor"],
      "footage_custodian": "Traffic Police",
      "metadata_visibility_level": "standard",
      "policy_version": 1
    },
    "health": { "status": "online", "last_frame_utc": "2026-08-19T06:52:00Z" },
    "sentinel_sync": { "status": "SYNCHRONIZED", "synced_at_utc": "2026-08-19T06:52:15Z" },
    "footage_access_via_sentinel": false
  }
]
```

### `GET /api/v1/cameras/{camera_id}`

Requires: `registry:read` + owning-department scope. Full record. The
`redacted_fields` list names any field withheld for the reading role (e.g.
`health_monitor` cannot see `maintenance_agency`).

### `GET /api/v1/cameras/{camera_id}/health`

Requires: `health:read` + owning-department scope. Query: `live=true|false`
(default true) — live mode probes the department system, otherwise the last
sample is returned.

### `GET /api/v1/cameras/{camera_id}/access-policy`

Requires: `policy:read` + owning-department scope. Describes local vs Sentinel
access; `sentinel_video_access` is always `false`.

---

## Overview

### `GET /api/v1/overview`

Requires: `registry:read`. Numbers behind the operations page. Counts are
scoped by department.

---

## Installation onboarding

Full lifecycle: DRAFT → SUBMITTED → (VALIDATION_FAILED | REGISTERED) →
SYNCHRONIZED → (SUSPENDED | DECOMMISSIONED).

There is no approval endpoint. A form that passes its own department's
validation is registered on the spot and reaches the central registry on the
next sync.

### `GET /api/v1/installation-requests`

Requires: `installation:read`. Lists records from every permitted department.
`X-Sentinel-Degraded` header lists departments that could not be reached (rows
served from the last mirror in that case). Query: `status`, `owning_department`.

### `POST /api/v1/installation-requests`

Requires: `installation:create`. Creates a DRAFT in the operator's own
department system.

Request:
```json
{
  "form": {
    "camera_name": "CG Road East",
    "external_camera_id": "TRF-AHM-9999",
    "camera_type": "fixed",
    "installation_purpose": "junction monitoring",
    "owning_department": "Traffic Police",
    "owning_unit": "Ahmedabad Traffic Zone 1",
    "district": "Ahmedabad",
    "latitude": 23.0225, "longitude": 72.5714,
    "road_or_junction": "CG Road",
    "view_direction": "eastbound",
    "source_type": "RTSP",
    "timezone": "Asia/Kolkata",
    "installation_date": "2026-08-19",
    "permitted_local_roles": ["department_operator", "investigator"]
  }
}
```

### `GET /api/v1/installation-requests/{request_id}`

Requires: `installation:read` + owning-department scope.

### `PATCH /api/v1/installation-requests/{request_id}`

Requires: `installation:update` + owning-department scope. Only editable in
DRAFT / VALIDATION_FAILED, and only by the raiser (operators only; departmental
roles above them are unrestricted). Once a record registers it is live centrally
and can no longer be edited by hand.

### `POST /api/v1/installation-requests/{request_id}/submit`

Requires: `installation:submit`. Runs the department's own validation.
Success → REGISTERED, visible centrally after the next sync. Failure →
VALIDATION_FAILED with a populated `validation_errors` list; correct the fields
and submit again.

Validation includes a jurisdiction check: coordinates must fall inside the
department's own area, not merely inside −90..90. With no approver in the loop,
a transposed digit has nothing else to stop it.

### `POST /api/v1/installation-requests/{request_id}/suspend`

Requires: `installation:suspend`. Body: `{ "reason": "optional" }`.

### `POST /api/v1/installation-requests/{request_id}/decommission`

Requires: `installation:decommission`. Body: `{ "reason": "optional" }`.

### `POST /api/v1/installation-requests/{request_id}/sync-metadata`

Requires: `installation:sync`. Publishes one record's metadata into the
central registry. 409 if the record is not REGISTERED or SYNCHRONIZED.

---

## Events

### `GET /api/v1/events`

Requires: `registry:read`. Normalised generic events. Query params:
`camera_id`, `source_system`, `event_type`, `since_hours` (1-168, default 6),
`refresh=true|false` (default true — pulls the latest before returning),
`limit` (1-1000, default 200).

Response element:
```json
{
  "event_id": "sentinel_evt_ab34…",
  "source_system": "municipal_vms",
  "external_event_id": "muni-evt-9002",
  "camera_id": "SENTINEL-MUNICIPAL-AHM-0101",
  "event_type": "camera_tamper",
  "severity": "critical",
  "timestamp_utc": "2026-08-19T06:52:20Z",
  "payload": {
    "event_label": "camera_tamper",
    "source_event_type": "tamper_alarm",
    "attributes": { "cause": "lens-obstruction", "auto_cleared": false }
  },
  "provenance": {
    "adapter": "municipal_adapter",
    "adapter_version": "0.2.0",
    "source_system": "municipal_vms",
    "metadata_only": true
  },
  "camera_name": "Municipal Junction 1",
  "owning_department": "Municipal Corporation",
  "district": "Ahmedabad",
  "ingested_at": "2026-08-19T06:52:33Z"
}
```

### `GET /api/v1/events/correlation`

Requires: `registry:read`. Pairs of events at nearby cameras within a short
time window. Query: `window_seconds` (5-3600, default 180),
`radius_m` (10-20000, default 1500), `since_hours` (default 6),
`refresh=true|false`.

Response:
```json
{
  "window_seconds": 180,
  "radius_m": 1500,
  "considered": 42,
  "pairs": [
    {
      "a": { "…EventOut…": null },
      "b": { "…EventOut…": null },
      "distance_m": 812.4,
      "delta_seconds": 47.0,
      "cross_department": true
    }
  ]
}
```

---

## Reports

### `GET /api/v1/reports/gap-analysis`

Requires: `registry:read`. Coverage stats per district plus ageing
infrastructure list. Query: `min_cameras_per_district` (default 3),
`ageing_years` (default 5). Scoped to permitted departments.

---

## Audit

### `GET /api/v1/audit`

Requires: `audit:read`. Reads the append-only audit log. Query params:
`username`, `action`, `resource_type`, `resource_id`, `department`, `outcome`,
`start`, `end`, `limit` (1-1000, default 200), `offset`.

A departmental auditor sees only their own department's rows plus
platform-level rows with no department attached.

### Actions

`login`, `login_failed`, `installation_form_created`,
`installation_form_updated`, `installation_form_submitted`,
`installation_request_approved`, `installation_request_rejected`,
`installation_request_suspended`, `installation_request_decommissioned`,
`installation_register_viewed`, `installation_request_viewed`,
`camera_metadata_synchronized`, `camera_registry_viewed`,
`camera_details_viewed`, `access_policy_viewed`, `camera_health_viewed`,
`audit_viewed`, `video_access_denied`.

---

## Video

Authorised, brokered, watermarked and audited. Nothing here returns a
department URL, an RTSP address, a credential or an upstream ticket.

### `POST /api/v1/video-sessions`

Requires `video:live` or `video:playback`, and a passing check in
`video_permissions.evaluate` — role, department, city, zone, camera state, and
either own-unit ownership or an active grant.

```json
{
  "camera_id": "SENTINEL-TRAFFIC-AHM-0001",
  "mode": "live",
  "reason": "Routine department monitoring",
  "case_id": "optional"
}
```

For recorded footage, send `mode: "playback"` with the window:

```json
{
  "camera_id": "SENTINEL-TRAFFIC-AHM-0001",
  "mode": "playback",
  "start_time_utc": "2026-08-21T03:30:00Z",
  "end_time_utc": "2026-08-21T03:40:00Z",
  "reason": "Reviewing the reported collision on this approach"
}
```

The response adds `segment_start_seconds` / `segment_end_seconds`: which part
of the owning department's recording answers that window. Playback may be
requested as often as needed — each call is its own audited session. The
bounds are the owner's: retention, maximum window width, and no future
windows.

Returns a `session_id`, an opaque `stream_url` of
`/api/v1/streams/{session_id}`, `expires_in_seconds`, the `watermark` text and
the `audit_id`. Expiry is clamped to the upstream ticket's own lifetime — the
session can never outlive the permission it was built on.

- `403 VIDEO_ACCESS_DENIED` — see the refusal shape above.
- `422 INVALID_PLAYBACK_WINDOW` — bad or over-long playback range.
- `503 SOURCE_ACCESS_NOT_CONFIGURED` — no video path configured for that source.

### `GET /api/v1/streams/{session_id}`

Range-aware proxy. Bound to the operator who opened the session: another
account presenting the same ID gets `403`. Re-authorised on **every** range
request, so a revoked grant or a suspended camera stops playback at the next
read rather than at session expiry. A revoked session returns `410`.

### `GET /api/v1/video-sessions/{session_id}/status`

Countdown and state for the player.

### `DELETE /api/v1/video-sessions/{session_id}`

Ends it now.

---

## Video access requests

Metadata federates automatically; footage does not. To watch a camera another
unit owns, ask that unit.

### `POST /api/v1/video-access-requests`

Requires: `video:request`. Raises a request against the owning department.

```json
{
  "camera_id": "SENTINEL-MUNICIPAL-AHM-0101",
  "reason": "Chain-snatching follow-up on Vasna approach",
  "case_id": "FIR 214/2026",
  "modes": ["live", "playback"]
}
```

`reason` is mandatory (≥ 5 characters) and goes to the audit trail.

- `400` — the camera already belongs to your own unit, so no grant is needed.
- `409` — you already have a pending request or an active grant for it.

### `GET /api/v1/video-access-requests`

Any authenticated account. Returns requests **you raised** plus requests
**against your own unit's cameras**, and nothing else. Optional
`?request_status=requested|granted|denied|revoked|expired`.

### `POST /api/v1/video-access-requests/{grant_id}/grant`

Requires: `video:grant`, **and** the account's department must own the camera.
Body (all optional): `{ "note": "…", "modes": ["playback"], "days": 3 }`.

Omitting `modes` keeps what was asked for; supplying it narrows the grant.
Omitting `days` uses `VIDEO_GRANT_DEFAULT_DAYS` (7).

### `POST /api/v1/video-access-requests/{grant_id}/deny`

Requires: `video:grant` + ownership. Body: `{ "note": "…" }`.

### `DELETE /api/v1/video-access-requests/{grant_id}`

Any authenticated account that is party to it: the owning unit revokes, the
requester withdraws. A revoked grant stops a stream already playing at its next
range request, not at session expiry.

### Refusal shape

A footage refusal names which kind of no it is, so a client can offer the
request instead of a dead end:

```json
{
  "code": "VIDEO_ACCESS_DENIED",
  "state": "needs_unit_approval",
  "message": "…",
  "owning_department": "Municipal Corporation",
  "request_access_at": "/api/v1/video-access-requests"
}
```

`state` is one of `denied`, `needs_unit_approval`, `not_enabled_by_owner`,
`camera_unavailable`.
