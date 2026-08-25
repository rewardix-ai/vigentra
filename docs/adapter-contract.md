# Adapter contract

Every federated department system is reached through one adapter class. The
adapter is the only component in Sentinel that knows a vendor's field names,
authentication scheme, lifecycle vocabulary, timestamp format or error
envelope. Above the adapter boundary, everything is canonical.

## Responsibilities

An adapter converts the department's dialect into the canonical models
defined in `services/central-api/app/schemas.py`, and converts canonical
requests back into the department's dialect on the way out.

Concretely, it implements `SurveillanceAdapter`:

```
list_installation_requests()
get_installation_request(request_id)
create_installation_request(form, created_by=)
update_installation_request(request_id, patch, updated_by=)
submit_installation_request(request_id, submitted_by=)
suspend_installation_request(request_id, actor=, reason=)
decommission_installation_request(request_id, actor=, reason=)
mark_synchronized(request_id)

list_approved_cameras()
get_camera_health(external_camera_id)

check_source_health()
```

There is deliberately no `approve_` or `reject_` method. Passing the
department's own validation is what registers a camera; there is no second
human step, because the unit installing the camera had already made the
decision.

Note what the contract does **not** define: any method that hands Sentinel a
stream URL, an NVR address, a credential or a long-lived media token. Video is
brokered separately (`video_adapters/`), one short-lived session at a time,
against a ticket that never leaves the server.
`services/central-api/app/adapters/base.py` is where you look if you doubt it.

## Canonical camera schema

Adapter output for one commissioned camera (Pydantic model
`schemas.CameraMetadata`):

```json
{
  "camera_id": "SENTINEL-TRAFFIC-AHM-0001",
  "external_camera_id": "TRF-AHM-0001",
  "source_system": "traffic_vms",
  "installation_request_id": "TRF-REQ-0001",
  "owning_department": "Traffic Police",
  "owning_unit": "Ahmedabad Traffic Zone 1",
  "name": "CG Road East",
  "vendor": "Demo Vendor", "model": "Demo IP Camera X1",
  "camera_type": "fixed",
  "installation_purpose": "traffic monitoring",
  "camera_serial_masked": "GTP*******5577",
  "district": "Ahmedabad",
  "road_or_junction": "CG Road",
  "landmark": "CG Road East Junction",
  "latitude": 23.0225, "longitude": 72.5714,
  "view_direction": "eastbound",
  "coverage_description": "Eastbound carriageway and pedestrian crossing",
  "entry_exit_zone_description": "Junction entry from Panchvati approach",
  "source_type": "VMS_API", "vms_name": "Traffic VMS Demo",
  "resolution": "1920x1080", "fps": 25, "codec": "H.264",
  "retention_days": 30, "timezone_name": "Asia/Kolkata",
  "installation_date": "2026-08-01", "commissioning_date": "2026-08-05",
  "installation_status": "COMMISSIONED",
  "request_status": "SYNCHRONIZED",
  "approved_at": "2026-08-06T04:00:00Z",
  "local_video_access": true,
  "permitted_local_roles": ["department_operator", "district_supervisor"],
  "health_status": "online",
  "last_frame_utc": "2026-08-19T06:00:00Z",
  "provenance": {
    "adapter": "traffic_adapter",
    "adapter_version": "0.2.0",
    "source_system": "traffic_vms",
    "metadata_only": true
  }
}
```

No `stream_url`, no `rtsp_url`, no `media_token`, no `session_id`. The
canonical schema has nowhere to put them. `local_video_access` records whether
the department serves this camera at all — it is a fact about their system, not
a grant, and Sentinel enforces its own decision separately.

## Canonical installation request

Everything an operator raised and the department's system stored and
validated:

```json
{
  "request_id": "TRF-REQ-0001",
  "source_system": "traffic_vms",
  "owning_department": "Traffic Police",
  "status": "SYNCHRONIZED",
  "camera_name": "CG Road East",
  "external_camera_id": "TRF-AHM-0001",
  "district": "Ahmedabad",
  "created_by": "traffic.operator",
  "created_at": "2026-08-01T03:44:00Z",
  "submitted_by": "traffic.operator",
  "submitted_at": "2026-08-01T05:32:00Z",
  "approved_at": "2026-08-06T04:00:00Z",
  "synchronized_at": "2026-08-06T04:02:00Z",
  "validation_errors": [],
  "form": { … canonicalised installation form … },
  "attachments": [ { "document_type": "site_survey", "reference": "DOC-TRF-SS-0001" } ],
  "sentinel_video_access": false
}
```

`approved_at` is a legacy column name kept for records created before the
approval stage was removed; for anything registered since, it is the moment
validation passed. `sentinel_video_access` on an *installation record* is
always false — a form is not a camera, and nothing is viewable until it has
registered and synchronised.

## Error behaviour

Adapters translate every failure into one of these canonical exceptions:

| Exception | Central HTTP | Meaning |
|---|---|---|
| `SourceUnavailableError` (incl. `SourceTimeoutError`) | 503 | Department system unreachable |
| `SourceAuthError` | 502 | Sentinel's own credential for the department was rejected |
| `ResourceNotFoundError` | 404 | The department does not know this record |
| `SourceValidationError` | 422 | The department rejected the payload (bad form) |
| `SourceConflictError` | 409 | Lifecycle transition refused (wrong state) |
| `SourceRateLimitedError` | 429 | Department is rate-limiting Sentinel |
| `UpstreamProtocolError` | 502 | Response shape not understood by the adapter |

The central error handler in `app/main.py::adapter_error_handler` returns
`{"detail": {"code", "message", "source_system", "upstream_status",
"detail", "retryable"}}`. Vendor error envelopes never leak upward.

## Video-session behaviour

Video is deliberately **not** part of `SurveillanceAdapter`. Metadata adapters
mirror records; video adapters broker one short-lived session at a time. They
are separate classes in `services/central-api/app/video_adapters/` so that a
department can federate its register without exposing any footage at all —
`video_access_enabled` is the owning department's switch and no Sentinel role
overrides it.

A video adapter returns a ticket that never leaves the server. Sentinel hands
the client an opaque `/api/v1/streams/{session_id}` and proxies range requests
against it, re-authorising each one. Refusals are `403 VIDEO_ACCESS_DENIED`
carrying a `state` — see [`access-model.md`](access-model.md).

## Adding a new department adapter (Phase 2 checklist)

1. Subclass `SurveillanceAdapter` in `services/central-api/app/adapters/`.
2. Implement every abstract method above, translating the department's
   dialect through `services/central-api/app/services/normalization.py`.
   Add new vocabulary mappings there rather than in the adapter — the
   normaliser is the shared translation table.
3. Register the class in `adapters/__init__.py::ADAPTER_REGISTRY`.
4. Add a `SourceSettings` entry in `config.Settings.sources`.
5. Add the credential to `.env.example` and Compose.
6. Add tests: adapter normalisation (both directions), one-source-offline
   isolation, and metadata-only assertions.

Nothing in the routers, the dashboard, the sync service or the audit trail
needs to change to bring a third department system on line.

## Migrations

Module 1 creates the schema on startup because the demo tears its database
down constantly. Phase 2, when persistent state matters, replaces
`init_models()` in `services/central-api/app/database.py` with Alembic. The
model changes needed will be limited to *adding* tables and columns for new
detection results; the canonical camera and installation schemas are the
substrate other modules read.
