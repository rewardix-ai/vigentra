"""Traffic VMS - mock source system #1 (Gujarat Traffic Police).

This is a STANDALONE department system. It owns:

  * the CCTV installation/onboarding register for its own cameras
  * the approval workflow that commissions a camera
  * the raw footage, the NVR recordings, the RTSP URLs and the VMS credentials

Sentinel never receives footage from this service. It reads APPROVED camera
METADATA only. The local video endpoints at the bottom of this file exist to
represent what the owning department keeps to itself - Sentinel does not call
them and does not proxy them.

Vendor dialect (deliberately unlike the Municipal system):
  * flat snake_case form fields   - cam_code / cam_name / lat / lng
  * request states                - DRAFT / REGISTERED / SYNCED / ...
  * timestamps                    - "dd-mm-YYYY HH:MM:SS" in Asia/Kolkata
  * auth                          - X-API-Key header
  * envelope                      - {"status": "OK", "records": [...]}
"""
from __future__ import annotations

import json
import os
import re
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR.parent / "data"
VIDEO_DIR = Path(os.getenv("TRAFFIC_VIDEO_DIR", str(APP_DIR.parent / "videos")))

API_KEY = os.getenv("TRAFFIC_VMS_API_KEY", "traffic-demo-key")
PUBLIC_BASE_URL = os.getenv("TRAFFIC_VMS_PUBLIC_BASE_URL", "http://traffic-vms:8001")
TICKET_TTL_SECONDS = int(os.getenv("TRAFFIC_VMS_TICKET_TTL_SECONDS", "300"))
IST = ZoneInfo("Asia/Kolkata")

SERVICE_STARTED_AT = datetime.now(timezone.utc)

app = FastAPI(
    title="Traffic VMS (mock source system)",
    description=(
        "Gujarat Traffic Police demo CCTV/VMS system. Owns its installation "
        "register, its approval workflow and its footage. Publishes approved "
        "camera METADATA to Sentinel. Synthetic data only."
    ),
    version="0.2.0",
)

# --------------------------------------------------------------------------
# Vendor lifecycle vocabulary
# --------------------------------------------------------------------------

# This vendor's own words. Note SYNCED rather than SYNCHRONIZED - the adapter
# is responsible for folding this onto the canonical vocabulary.
STATE_DRAFT = "DRAFT"
STATE_SUBMITTED = "SUBMITTED"
STATE_VALIDATION_FAILED = "VALIDATION_FAILED"
# Validation still gates entry - bad coordinates must not reach the registry -
# but a form that passes it is registered immediately. There is no human
# approval step in the metadata path.
STATE_REGISTERED = "REGISTERED"
STATE_SYNCED = "SYNCED"
STATE_SUSPENDED = "SUSPENDED"
STATE_DECOMMISSIONED = "DECOMMISSIONED"

# States for which a camera asset record exists at all.
COMMISSIONED_STATES = {STATE_REGISTERED, STATE_SYNCED, STATE_SUSPENDED, STATE_DECOMMISSIONED}
# States whose metadata Sentinel is allowed to take into its registry.
PUBLISHABLE_STATES = {STATE_REGISTERED, STATE_SYNCED}

EDITABLE_STATES = {STATE_DRAFT, STATE_VALIDATION_FAILED}

REQUIRED_FORM_FIELDS = [
    "cam_name",
    "cam_code",
    "cam_kind",
    "purpose",
    "dept",
    "unit",
    "district",
    "lat",
    "lng",
    "road",
    "direction",
    "feed_type",
    "tz",
    "installed_on",
]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


REQUESTS: list[dict[str, Any]] = _load("installation_requests.json")

_RAW_EVENTS: list[dict[str, Any]] = _load("events.json")

# camera code -> live health counters, seeded when a camera is commissioned
HEALTH: dict[str, dict[str, Any]] = {}

# media ticket -> grant (local video only; never issued to Sentinel)
TICKETS: dict[str, dict[str, Any]] = {}


def _now_local() -> str:
    return datetime.now(timezone.utc).astimezone(IST).strftime("%d-%m-%Y %H:%M:%S")


def _parse_local(value: str) -> datetime:
    return datetime.strptime(value, "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)


def _seed_health() -> None:
    for record in REQUESTS:
        if record["state"] not in COMMISSIONED_STATES:
            continue
        code = record["form"]["cam_code"]
        HEALTH.setdefault(
            code,
            {
                "status": record.get("seed_health", "UP"),
                "reconnects": record.get("seed_reconnects", 0),
                "uptime_pct": record.get("seed_uptime_pct", 99.0),
                "last_frame_at": _now_local(),
            },
        )


_seed_health()


def _shift_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slide fixture events forward so the demo always shows recent activity.

    The JSON fixture stays readable with fixed timestamps; on boot we translate
    the whole timeline so its newest entry sits ~2 minutes in the past. Turn
    off with TRAFFIC_VMS_RELATIVE_EVENTS=0 if you want the literal fixture.
    """
    if os.getenv("TRAFFIC_VMS_RELATIVE_EVENTS", "1") != "1":
        return [dict(entry) for entry in raw]
    parsed = [
        (entry, datetime.strptime(entry["ts"], "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST))
        for entry in raw
    ]
    newest = max(dt for _, dt in parsed)
    delta = (datetime.now(timezone.utc) - timedelta(minutes=2)) - newest
    out: list[dict[str, Any]] = []
    for entry, dt in parsed:
        shifted = dict(entry)
        shifted["ts"] = (dt + delta).astimezone(IST).strftime("%d-%m-%Y %H:%M:%S")
        out.append(shifted)
    return out


EVENTS: list[dict[str, Any]] = _shift_events(_RAW_EVENTS)


def _require_key(api_key: str | None) -> None:
    """Traffic VMS authenticates with a shared API key header."""
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail={"error": "INVALID_API_KEY"})


def _request(request_ref: str) -> dict[str, Any]:
    for record in REQUESTS:
        if record["request_ref"].upper() == request_ref.upper():
            return record
    raise HTTPException(
        status_code=404, detail={"error": "REQUEST_NOT_FOUND", "request_ref": request_ref}
    )


def _next_ref() -> str:
    numbers = [int(m.group(1)) for r in REQUESTS if (m := re.search(r"(\d+)$", r["request_ref"]))]
    return f"TRF-REQ-{(max(numbers, default=0) + 1):04d}"


def _touch_health(code: str) -> dict[str, Any]:
    """Refresh the vendor heartbeat so the demo never shows a stale last frame."""
    entry = HEALTH.setdefault(
        code, {"status": "UP", "reconnects": 0, "uptime_pct": 99.0, "last_frame_at": _now_local()}
    )
    now = datetime.now(timezone.utc)
    if entry["status"] == "UP":
        entry["last_frame_at"] = _now_local()
    elif entry["status"] == "FLAPPING":
        entry["last_frame_at"] = (now - timedelta(seconds=42)).astimezone(IST).strftime(
            "%d-%m-%Y %H:%M:%S"
        )
    return entry


# --------------------------------------------------------------------------
# Validation - runs at the department system, before anything reaches Sentinel
# --------------------------------------------------------------------------

#: Rough bounding box for Gujarat, padded slightly. This is a jurisdiction
#: check, not a geocoder: it rejects the transposed digits and the pasted
#: sample coordinate, which is what actually goes wrong on a form. It lives in
#: the department system rather than in Sentinel because which ground a
#: department may commission cameras on is the department's rule.
GUJARAT_BOUNDS = {"lat": (20.0, 24.8), "lng": (68.0, 74.6)}



#: Length of the clip standing in for this camera's continuous recording.
ARCHIVE_CLIP_SECONDS = 26.8
#: Never hand back a sliver nobody can see anything in.
MIN_SEGMENT_SECONDS = 3.0


def _archive_segment(start: datetime | None, end: datetime | None) -> dict[str, Any]:
    """Map a requested wall-clock window onto a segment of the recording.

    Takes real datetimes, not strings: this vendor has its own timestamp
    dialect, and parsing belongs at the edge that owns it. Passing the raw
    string here once meant every window silently produced the same segment,
    because the parse failed and the fallback covered the whole clip.

    Deterministic on the window: asking for the same minutes twice returns the
    same footage, which is what an archive does and what makes the result
    checkable. Different windows land on different offsets, so the operator can
    see that the request mattered.
    """
    if start is None:
        return {"segment_start_seconds": 0.0, "segment_end_seconds": ARCHIVE_CLIP_SECONDS}

    span_seconds = (end - start).total_seconds() if end else None

    # A longer window shows more of the recording, but the clip is the ceiling.
    if not span_seconds or span_seconds <= 0:
        length = ARCHIVE_CLIP_SECONDS
    else:
        # One minute of wall clock maps to one second of archive, so a typical
        # 5-30 minute request produces a segment you can actually watch.
        length = min(ARCHIVE_CLIP_SECONDS, max(MIN_SEGMENT_SECONDS, span_seconds / 60.0))

    latest_offset = max(0.0, ARCHIVE_CLIP_SECONDS - length)
    if latest_offset <= 0:
        offset = 0.0
    else:
        digest = hashlib.sha256(start.isoformat().encode("utf-8")).digest()
        offset = round((int.from_bytes(digest[:4], "big") / 0xFFFFFFFF) * latest_offset, 2)

    return {
        "segment_start_seconds": offset,
        "segment_end_seconds": round(min(ARCHIVE_CLIP_SECONDS, offset + length), 2),
    }


def _validate(form: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for field in REQUIRED_FORM_FIELDS:
        value = form.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{field} is required")

    for axis in ("lat", "lng"):
        value = form.get(axis)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            errors.append(f"{axis} must be numeric")
            continue
        lo, hi = GUJARAT_BOUNDS[axis]
        if not lo <= numeric <= hi:
            errors.append(
                f"{axis} {numeric} is outside this department's jurisdiction "
                f"(expected {lo} to {hi})"
            )

    code = str(form.get("cam_code") or "").strip()
    if code:
        clash = [
            r for r in REQUESTS
            if r["form"].get("cam_code", "").upper() == code.upper()
            and r["state"] in COMMISSIONED_STATES
        ]
        if clash:
            errors.append(f"cam_code {code} is already commissioned in this system")

    roles = form.get("roles_allowed")
    if not roles or not isinstance(roles, list):
        errors.append("roles_allowed must list at least one local role")

    return errors


def _camera_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project an installation request into this vendor's camera asset shape.

    Note what is NOT here: no stream URL, no NVR address, no credential. Those
    stay inside this service. Sentinel reads this projection and nothing else.
    """
    form = record["form"]
    code = form["cam_code"]
    health = _touch_health(code)
    return {
        "cam_code": code,
        "cam_name": form.get("cam_name"),
        "state": record["state"],
        "request_ref": record["request_ref"],
        "serial_no": form.get("serial_no"),
        "make": form.get("make"),
        "model_no": form.get("model_no"),
        "cam_kind": form.get("cam_kind"),
        "purpose": form.get("purpose"),
        "dept": form.get("dept"),
        "unit": form.get("unit"),
        "district": form.get("district"),
        "ps_zone": form.get("ps_zone"),
        "maint_agency": form.get("maint_agency"),
        "install_vendor": form.get("install_vendor"),
        "lat": form.get("lat"),
        "lng": form.get("lng"),
        "address": form.get("address"),
        "road": form.get("road"),
        "direction": form.get("direction"),
        "coverage": form.get("coverage"),
        "entry_exit": form.get("entry_exit"),
        "feed_type": form.get("feed_type"),
        "vms": form.get("vms"),
        "vms_make": form.get("vms_make"),
        "res": form.get("res"),
        "fps": form.get("fps"),
        "codec": form.get("codec"),
        "retention": form.get("retention"),
        "tz": form.get("tz"),
        "installed_on": form.get("installed_on"),
        "commissioned_on": form.get("commissioned_on"),
        # Local viewing capability. Reported so Sentinel can show the policy
        # summary - never so Sentinel can act on it.
        "live_ok": form.get("live_ok", True),
        "playback_ok": form.get("playback_ok", True),
        "roles_allowed": form.get("roles_allowed", []),
        "approved_by": record.get("approved_by"),
        "approved_by_role": record.get("approved_by_role"),
        "approved_on": record.get("approved_on"),
        "synced_on": record.get("synced_on"),
        "status": health["status"],
        "last_frame_at": health["last_frame_at"],
        "reconnects": health["reconnects"],
        "uptime_pct": health["uptime_pct"],
        "data_classification": "SYNTHETIC_DEMO",
    }


def _public_request(record: dict[str, Any]) -> dict[str, Any]:
    """A request as returned over the API.

    `admin_contact` is redacted: it is operational contact data that the
    department keeps, not something every federated viewer needs.
    """
    out = json.loads(json.dumps(record))
    out.pop("seed_health", None)
    out.pop("seed_reconnects", None)
    out.pop("seed_uptime_pct", None)
    contact = out["form"].get("admin_contact")
    if contact:
        out["form"]["admin_contact_masked"] = _mask_contact(contact)
        out["form"].pop("admin_contact", None)
    serial = out["form"].get("serial_no")
    if serial:
        out["form"]["serial_no"] = _mask_serial(serial)
    return out


def _mask_contact(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 4:
        return f"******{digits[-4:]}"
    return "******"


def _mask_serial(value: str) -> str:
    text = str(value)
    return text[:3] + "*" * max(0, len(text) - 6) + text[-3:] if len(text) > 6 else "***"


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    """Unauthenticated liveness probe."""
    commissioned = [r for r in REQUESTS if r["state"] in COMMISSIONED_STATES]
    return {
        "service": "traffic-vms",
        "status": "OK",
        "vendor": "GTP-VMS",
        "api_version": "2.0",
        "installation_requests": len(REQUESTS),
        "commissioned_cameras": len(commissioned),
        "publishable_cameras": len([r for r in REQUESTS if r["state"] in PUBLISHABLE_STATES]),
        "uptime_seconds": int((datetime.now(timezone.utc) - SERVICE_STARTED_AT).total_seconds()),
        "server_time": _now_local(),
        "footage_custody": "LOCAL_TO_TRAFFIC_POLICE",
        "data_classification": "SYNTHETIC_DEMO",
    }


# --------------------------------------------------------------------------
# Installation register
# --------------------------------------------------------------------------

@app.post("/traffic/installation-requests", tags=["installation"], status_code=201)
def create_request(
    payload: dict[str, Any] = Body(...),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Create a DRAFT installation record in the department's own register."""
    _require_key(x_api_key)
    form = payload.get("form") or {}
    if not isinstance(form, dict):
        raise HTTPException(status_code=400, detail={"error": "FORM_MUST_BE_OBJECT"})

    record = {
        "request_ref": _next_ref(),
        "state": STATE_DRAFT,
        "created_by": payload.get("created_by") or "unknown",
        "created_on": _now_local(),
        "updated_on": _now_local(),
        "submitted_by": None,
        "submitted_on": None,
        "approved_by": None,
        "approved_by_role": None,
        "approved_on": None,
        "rejected_by": None,
        "rejected_on": None,
        "rejection_reason": None,
        "synced_on": None,
        "validation_errors": [],
        "form": form,
        "docs": payload.get("docs", []),
    }
    REQUESTS.append(record)
    return {"status": "OK", "record": _public_request(record)}


@app.get("/traffic/installation-requests", tags=["installation"])
def list_requests(
    state: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Every installation record this department holds, at any lifecycle stage."""
    _require_key(x_api_key)
    rows = [r for r in REQUESTS if not state or r["state"].upper() == state.upper()]
    return {"status": "OK", "count": len(rows), "records": [_public_request(r) for r in rows]}


@app.get("/traffic/installation-requests/{request_ref}", tags=["installation"])
def get_request(request_ref: str, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_key(x_api_key)
    return {"status": "OK", "record": _public_request(_request(request_ref))}


@app.patch("/traffic/installation-requests/{request_ref}", tags=["installation"])
def update_request(
    request_ref: str,
    payload: dict[str, Any] = Body(...),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Edit a record that has not yet left the drafting stage."""
    _require_key(x_api_key)
    record = _request(request_ref)
    if record["state"] not in EDITABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail={"error": "NOT_EDITABLE", "state": record["state"], "request_ref": request_ref},
        )
    form = payload.get("form")
    if isinstance(form, dict):
        record["form"].update(form)
    if isinstance(payload.get("docs"), list):
        record["docs"] = payload["docs"]
    record["updated_on"] = _now_local()
    return {"status": "OK", "record": _public_request(record)}


@app.post("/traffic/installation-requests/{request_ref}/submit", tags=["installation"])
def submit_request(
    request_ref: str,
    payload: dict[str, Any] = Body(default={}),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Validate the form locally, then move it into the approval queue."""
    _require_key(x_api_key)
    record = _request(request_ref)
    if record["state"] not in EDITABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"error": "NOT_SUBMITTABLE", "state": record["state"]}
        )

    errors = _validate(record["form"])
    record["submitted_by"] = payload.get("submitted_by") or record.get("created_by")
    record["submitted_on"] = _now_local()
    record["updated_on"] = _now_local()
    record["validation_errors"] = errors

    if errors:
        record["state"] = STATE_VALIDATION_FAILED
    else:
        # Registered on the spot. The department has already decided to install
        # the camera; asking it to also approve its own paperwork added delay
        # without adding a decision.
        record["state"] = STATE_REGISTERED
        record["registered_on"] = _now_local()
        record["form"].setdefault("commissioned_on", datetime.now(IST).strftime("%d-%m-%Y"))
        _touch_health(record["form"]["cam_code"])

    return {
        "status": "OK" if not errors else "VALIDATION_FAILED",
        "record": _public_request(record),
        "validation_errors": errors,
    }


@app.post("/traffic/installation-requests/{request_ref}/suspend", tags=["installation"])
def suspend_request(
    request_ref: str,
    payload: dict[str, Any] = Body(default={}),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Take a commissioned camera out of service without deleting its record."""
    _require_key(x_api_key)
    record = _request(request_ref)
    if record["state"] not in PUBLISHABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"error": "NOT_COMMISSIONED", "state": record["state"]}
        )
    record["state"] = STATE_SUSPENDED
    record["suspension_reason"] = (payload.get("reason") or "").strip() or None
    record["updated_on"] = _now_local()
    HEALTH.setdefault(record["form"]["cam_code"], {})["status"] = "DOWN"
    return {"status": "OK", "record": _public_request(record)}


@app.post("/traffic/installation-requests/{request_ref}/decommission", tags=["installation"])
def decommission_request(
    request_ref: str,
    payload: dict[str, Any] = Body(default={}),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Permanently retire a camera asset."""
    _require_key(x_api_key)
    record = _request(request_ref)
    if record["state"] not in PUBLISHABLE_STATES | {STATE_SUSPENDED}:
        raise HTTPException(
            status_code=409, detail={"error": "NOT_COMMISSIONED", "state": record["state"]}
        )
    record["state"] = STATE_DECOMMISSIONED
    record["decommission_reason"] = (payload.get("reason") or "").strip() or None
    record["updated_on"] = _now_local()
    HEALTH.setdefault(record["form"]["cam_code"], {})["status"] = "DOWN"
    return {"status": "OK", "record": _public_request(record)}


@app.post("/traffic/installation-requests/{request_ref}/mark-synchronized", tags=["installation"])
def mark_synchronized(
    request_ref: str,
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Acknowledge that Sentinel has taken this record's metadata."""
    _require_key(x_api_key)
    record = _request(request_ref)
    if record["state"] not in PUBLISHABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"error": "NOT_APPROVED", "state": record["state"]}
        )
    record["state"] = STATE_SYNCED
    record["synced_on"] = _now_local()
    record["updated_on"] = _now_local()
    return {"status": "OK", "record": _public_request(record)}


# --------------------------------------------------------------------------
# Camera asset register (metadata that Sentinel is allowed to read)
# --------------------------------------------------------------------------

@app.get("/traffic/approved-cameras", tags=["cameras"])
def approved_cameras(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Camera asset records for everything that reached commissioning.

    Includes SUSPENDED and DECOMMISSIONED entries, carrying their state, so the
    central registry can mark a withdrawn camera unavailable rather than losing
    track of it. Records still in the pipeline are not published at all.
    """
    _require_key(x_api_key)
    rows = [_camera_record(r) for r in REQUESTS if r["state"] in COMMISSIONED_STATES]
    return {
        "status": "OK",
        "count": len(rows),
        "generated_at": _now_local(),
        "metadata_only": True,
        "records": rows,
    }


@app.get("/traffic/cameras/{camera_id}/health", tags=["cameras"])
def camera_health(camera_id: str, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Health counters for one commissioned camera."""
    _require_key(x_api_key)
    for record in REQUESTS:
        if (
            record["form"].get("cam_code", "").upper() == camera_id.upper()
            and record["state"] in COMMISSIONED_STATES
        ):
            entry = _touch_health(record["form"]["cam_code"])
            return {
                "status": "OK",
                "cam_code": record["form"]["cam_code"],
                "health": {
                    "state": entry["status"],
                    "last_frame_at": entry["last_frame_at"],
                    "reconnects": entry["reconnects"],
                    "uptime_pct": entry["uptime_pct"],
                    "firmware": record["form"].get("firmware", "GTP-VMS 2.0.1"),
                },
            }
    raise HTTPException(
        status_code=404, detail={"error": "CAMERA_NOT_FOUND", "camera_code": camera_id}
    )


@app.get("/traffic/cameras/{camera_id}/events", tags=["events"])
def camera_events(
    camera_id: str,
    start: str | None = Query(default=None, description="ISO-8601 or dd-mm-YYYY HH:MM:SS"),
    end: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Generic (non-AI) events recorded by the traffic VMS for one camera."""
    _require_key(x_api_key)
    known = any(
        r["form"].get("cam_code", "").upper() == camera_id.upper() and r["state"] in COMMISSIONED_STATES
        for r in REQUESTS
    )
    if not known:
        raise HTTPException(
            status_code=404, detail={"error": "CAMERA_NOT_FOUND", "camera_code": camera_id}
        )

    def _bound(value: str | None) -> datetime | None:
        if not value:
            return None
        for fmt in ("%d-%m-%Y %H:%M:%S",):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=IST)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400, detail={"error": "BAD_TIME_FORMAT", "value": value}
            )
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=IST)

    lo, hi = _bound(start), _bound(end)
    rows = []
    for event in EVENTS:
        if event["cam"].upper() != camera_id.upper():
            continue
        when = datetime.strptime(event["ts"], "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST)
        if lo and when < lo:
            continue
        if hi and when > hi:
            continue
        rows.append(event)
    rows.sort(
        key=lambda e: datetime.strptime(e["ts"], "%d-%m-%Y %H:%M:%S"), reverse=True
    )
    return {
        "status": "OK",
        "camera_code": camera_id,
        "count": len(rows),
        "tz": "Asia/Kolkata",
        "events": rows,
    }


@app.post("/traffic/admin/cameras/{camera_id}/status", tags=["system"])
def set_camera_status(
    camera_id: str,
    state: str = Query(..., pattern="^(UP|DOWN|FLAPPING)$"),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Demo hook: flip a camera's vendor status to rehearse degraded states."""
    _require_key(x_api_key)
    entry = HEALTH.setdefault(
        camera_id.upper(),
        {"status": "UP", "reconnects": 0, "uptime_pct": 99.0, "last_frame_at": _now_local()},
    )
    entry["status"] = state
    return {"status": "OK", "cam_code": camera_id, "new_state": state}


# --------------------------------------------------------------------------
# LOCAL video - department custody only
#
# These endpoints model what the Traffic Police keep inside their own
# environment. Sentinel does not call them, does not proxy them, and never
# hands a client a URL that reaches them. They exist so the demo can show that
# footage genuinely lives here and not in the federation layer.
# --------------------------------------------------------------------------

def _iter_file(path: Path, start: int, end: int, chunk: int = 64 * 1024) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = handle.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _grant_media(form: dict[str, Any]) -> tuple[str, str, datetime]:
    """Issue a short-lived ticket for one camera's bundled clip."""
    media_file = Path(form.get("local_stream", "/videos/traffic_01.mp4")).name
    ticket = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(seconds=TICKET_TTL_SECONDS)
    TICKETS[ticket] = {"file": media_file, "expires_at": expires}
    return media_file, ticket, expires


def _commissioned_form(camera_id: str) -> dict[str, Any]:
    for record in REQUESTS:
        form = record["form"]
        if (
            form.get("cam_code", "").upper() == camera_id.upper()
            and record["state"] in PUBLISHABLE_STATES
        ):
            return form
    raise HTTPException(
        status_code=404, detail={"error": "CAMERA_NOT_FOUND", "camera_code": camera_id}
    )


@app.get("/traffic/cameras/{camera_id}/live", tags=["video"])
def camera_live(camera_id: str, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Authorized live feed handle.

    Modelled on how a real VMS hands back an RTSP/HLS address: a short-lived
    ticketed URL that is useless once the ticket expires. Sentinel relays the
    bytes and never passes the ticket to a browser.
    """
    _require_key(x_api_key)
    form = _commissioned_form(camera_id)
    if not form.get("live_ok", True):
        raise HTTPException(
            status_code=503, detail={"error": "LIVE_NOT_ENABLED", "camera_code": camera_id}
        )
    media_file, ticket, expires = _grant_media(form)
    return {
        "status": "OK",
        "cam_code": camera_id,
        "feed_type": "live",
        "protocol": "http-mp4",
        "playback_url": f"{PUBLIC_BASE_URL}/traffic/local/media/{media_file}?ticket={ticket}",
        "ticket_expires": expires.astimezone(IST).strftime("%d-%m-%Y %H:%M:%S"),
        "custody": "TRAFFIC_POLICE_LOCAL",
        "note": "SYNTHETIC_DEMO_FEED",
    }


@app.get("/traffic/cameras/{camera_id}/playback", tags=["video"])
def camera_playback(
    camera_id: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Authorized recorded playback for a window.

    The bundled clip stands in for the archive segment a real NVR would cut.
    """
    _require_key(x_api_key)
    form = _commissioned_form(camera_id)
    if not form.get("playback_ok", True):
        raise HTTPException(
            status_code=503, detail={"error": "PLAYBACK_NOT_ENABLED", "camera_code": camera_id}
        )
    media_file, ticket, expires = _grant_media(form)
    # This vendor speaks "dd-mm-YYYY HH:MM:SS" in IST. Parse before mapping.
    segment = _archive_segment(
        _parse_local(start) if start else None,
        _parse_local(end) if end else None,
    )
    return {
        "status": "OK",
        "cam_code": camera_id,
        "feed_type": "playback",
        "protocol": "http-mp4",
        "requested_window": {"start": start, "end": end},
        "playback_url": f"{PUBLIC_BASE_URL}/traffic/local/media/{media_file}?ticket={ticket}",
        "ticket_expires": expires.astimezone(IST).strftime("%d-%m-%Y %H:%M:%S"),
        "custody": "TRAFFIC_POLICE_LOCAL",
        "note": "SYNTHETIC_DEMO_FEED",
        **segment,
    }


@app.get("/traffic/local/cameras/{camera_id}/live", tags=["local-video"])
def local_live(camera_id: str, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Local live feed grant. Issued only inside the department's own system."""
    _require_key(x_api_key)
    for record in REQUESTS:
        form = record["form"]
        if form.get("cam_code", "").upper() == camera_id.upper() and record["state"] in PUBLISHABLE_STATES:
            media_file = Path(form.get("local_stream", "/videos/traffic_01.mp4")).name
            ticket = secrets.token_urlsafe(24)
            TICKETS[ticket] = {
                "file": media_file,
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=TICKET_TTL_SECONDS),
            }
            return {
                "status": "OK",
                "cam_code": form["cam_code"],
                "custody": "TRAFFIC_POLICE_LOCAL",
                "playback_url": f"{PUBLIC_BASE_URL}/traffic/local/media/{media_file}?ticket={ticket}",
                "note": "LOCAL_DEPARTMENT_ACCESS_ONLY - not federated to Sentinel",
            }
    raise HTTPException(status_code=404, detail={"error": "CAMERA_NOT_FOUND"})


@app.get("/traffic/local/media/{filename}", tags=["local-video"])
def local_media(filename: str, request: Request, ticket: str = Query(...)) -> Response:
    """Ticketed local media. Department custody; Sentinel never receives a ticket."""
    entry = TICKETS.get(ticket)
    if entry is None or entry["expires_at"] < datetime.now(timezone.utc):
        TICKETS.pop(ticket, None)
        raise HTTPException(status_code=401, detail={"error": "INVALID_OR_EXPIRED_TICKET"})
    if entry["file"] != Path(filename).name:
        raise HTTPException(status_code=403, detail={"error": "TICKET_FILE_MISMATCH"})

    path = VIDEO_DIR / Path(filename).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"error": "MEDIA_NOT_FOUND"})

    size = path.stat().st_size
    range_header = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}

    if range_header and range_header.startswith("bytes="):
        spec = range_header.split("=", 1)[1].split(",")[0].strip()
        start_raw, _, end_raw = spec.partition("-")
        start = int(start_raw) if start_raw else max(0, size - int(end_raw or 0))
        end = min(int(end_raw), size - 1) if end_raw and start_raw else size - 1
        if start >= size:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        return StreamingResponse(
            _iter_file(path, start, end),
            status_code=206,
            media_type="video/mp4",
            headers={
                **headers,
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(end - start + 1),
            },
        )

    return StreamingResponse(
        _iter_file(path, 0, size - 1),
        media_type="video/mp4",
        headers={**headers, "Content-Length": str(size)},
    )


@app.exception_handler(HTTPException)
async def vendor_error(_: Request, exc: HTTPException) -> JSONResponse:
    """Vendor-shaped error envelope - the adapter has to translate this."""
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"status": "ERROR", **detail})
