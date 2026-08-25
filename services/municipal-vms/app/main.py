"""Municipal VMS - mock source system #2 (Municipal Corporation).

A second STANDALONE department system that shares nothing with the Traffic VMS.
It owns its own installation register, its own approval workflow, and its own
footage. Sentinel reads APPROVED camera METADATA only; the local video
endpoints at the bottom exist to represent what stays in municipal custody.

Vendor dialect (deliberately unlike the Traffic system):
  * nested camelCase submission - identity{} / ownership{} / geo{} / tech{}
  * lifecycle words             - draft / awaiting_approval / synchronized / retired
  * timestamps                  - epoch milliseconds, UTC
  * auth                        - Authorization: Bearer <token>
  * envelope                    - {"ok": true, "data": ..., "meta": {...}}
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

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR.parent / "data"
VIDEO_DIR = Path(os.getenv("MUNICIPAL_VIDEO_DIR", str(APP_DIR.parent / "videos")))

BEARER_TOKEN = os.getenv("MUNICIPAL_VMS_TOKEN", "municipal-demo-token")
PUBLIC_BASE_URL = os.getenv("MUNICIPAL_VMS_PUBLIC_BASE_URL", "http://municipal-vms:8002")
TOKEN_TTL_SECONDS = int(os.getenv("MUNICIPAL_VMS_TOKEN_TTL_SECONDS", "300"))

SERVICE_STARTED_AT = datetime.now(timezone.utc)

app = FastAPI(
    title="Municipal VMS (mock source system)",
    description=(
        "Municipal Corporation demo CCTV/VMS system. Owns its installation "
        "register, its approval workflow and its footage. Publishes approved "
        "camera METADATA to Sentinel. Synthetic data only."
    ),
    version="0.2.0",
)

# --------------------------------------------------------------------------
# Vendor lifecycle vocabulary - lower case, and different words again
# --------------------------------------------------------------------------

LC_DRAFT = "draft"
LC_SUBMITTED = "submitted"
LC_VALIDATION_FAILED = "validation_failed"
# This dialect calls it "registered"; the adapter folds it onto the canonical
# vocabulary. No human approval step in the metadata path.
LC_REGISTERED = "registered"
LC_SYNCHRONIZED = "synchronized"
LC_SUSPENDED = "suspended"
LC_RETIRED = "retired"

COMMISSIONED_STATES = {LC_REGISTERED, LC_SYNCHRONIZED, LC_SUSPENDED, LC_RETIRED}
PUBLISHABLE_STATES = {LC_REGISTERED, LC_SYNCHRONIZED}
EDITABLE_STATES = {LC_DRAFT, LC_VALIDATION_FAILED}

REQUIRED_PATHS = [
    ("identity", "label"),
    ("identity", "deviceId"),
    ("identity", "kind"),
    ("identity", "purpose"),
    ("ownership", "department"),
    ("ownership", "unit"),
    ("ownership", "district"),
    ("geo", "latitude"),
    ("geo", "longitude"),
    ("geo", "junction"),
    ("geo", "facing"),
    ("tech", "transport"),
    ("tech", "timezone"),
    ("tech", "installedOnMs"),
]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


REQUESTS: list[dict[str, Any]] = _load("installation_requests.json")
_RAW_EVENTS: list[dict[str, Any]] = _load("events.json")
HEALTH: dict[str, dict[str, Any]] = {}
GRANTS: dict[str, dict[str, Any]] = {}


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _seed_health() -> None:
    for record in REQUESTS:
        if record["lifecycle"] not in COMMISSIONED_STATES:
            continue
        device_id = record["submission"]["identity"]["deviceId"]
        HEALTH.setdefault(
            device_id,
            {
                "availability": record.get("seedAvailability", "UP"),
                "reconnectCount": record.get("seedReconnects", 0),
                "lastFrameEpochMs": _now_ms(),
            },
        )


_seed_health()


def _shift_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slide the canned event timeline forward so demos always show fresh activity."""
    if os.getenv("MUNICIPAL_VMS_RELATIVE_EVENTS", "1") != "1":
        return [dict(entry) for entry in raw]
    newest = max(entry["at_ms"] for entry in raw)
    delta = (_now_ms() - 150_000) - newest
    out: list[dict[str, Any]] = []
    for entry in raw:
        shifted = dict(entry)
        shifted["at_ms"] = entry["at_ms"] + delta
        out.append(shifted)
    return out


EVENTS: list[dict[str, Any]] = _shift_events(_RAW_EVENTS)


def _require_bearer(request: Request) -> None:
    """Municipal VMS authenticates with an OAuth-style bearer token."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "missing_bearer_token"})
    if header.split(" ", 1)[1].strip() != BEARER_TOKEN:
        raise HTTPException(status_code=401, detail={"code": "invalid_bearer_token"})


def _record(ref: str) -> dict[str, Any]:
    for entry in REQUESTS:
        if entry["ref"].upper() == ref.upper():
            return entry
    raise HTTPException(status_code=404, detail={"code": "no_such_request", "ref": ref})


def _next_ref() -> str:
    numbers = [int(m.group(1)) for r in REQUESTS if (m := re.search(r"(\d+)$", r["ref"]))]
    return f"SMC-REQ-{(max(numbers, default=9000) + 1):04d}"


def _touch_health(device_id: str) -> dict[str, Any]:
    entry = HEALTH.setdefault(
        device_id, {"availability": "UP", "reconnectCount": 0, "lastFrameEpochMs": _now_ms()}
    )
    if entry["availability"] == "UP":
        entry["lastFrameEpochMs"] = _now_ms()
    elif entry["availability"] == "PARTIAL":
        entry["lastFrameEpochMs"] = _now_ms() - 55_000
    return entry


# --------------------------------------------------------------------------
# Validation - performed by the department system, before Sentinel sees anything
# --------------------------------------------------------------------------

#: Rough bounding box for Gujarat, padded slightly. This is a jurisdiction
#: check, not a geocoder: it rejects the transposed digits and the pasted
#: sample coordinate, which is what actually goes wrong on a form. It lives in
#: the department system rather than in Sentinel because which ground a
#: department may commission cameras on is the department's rule.
GUJARAT_BOUNDS = {"latitude": (20.0, 24.8), "longitude": (68.0, 74.6)}



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


def _validate(submission: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    for group, field in REQUIRED_PATHS:
        value = (submission.get(group) or {}).get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(f"{group}.{field} is required")

    geo = submission.get("geo") or {}
    for axis in ("latitude", "longitude"):
        value = geo.get(axis)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            issues.append(f"geo.{axis} must be numeric")
            continue
        lo, hi = GUJARAT_BOUNDS[axis]
        if not lo <= numeric <= hi:
            issues.append(
                f"geo.{axis} {numeric} is outside this corporation's jurisdiction "
                f"(expected {lo} to {hi})"
            )

    device_id = str((submission.get("identity") or {}).get("deviceId") or "").strip()
    if device_id:
        clash = [
            r for r in REQUESTS
            if r["submission"]["identity"].get("deviceId", "").upper() == device_id.upper()
            and r["lifecycle"] in COMMISSIONED_STATES
        ]
        if clash:
            issues.append(f"identity.deviceId {device_id} is already commissioned")

    roles = (submission.get("policy") or {}).get("localRoles")
    if not roles or not isinstance(roles, list):
        issues.append("policy.localRoles must list at least one local role")

    return issues


def _mask_contact(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return f"******{digits[-4:]}" if len(digits) >= 4 else "******"


def _mask_serial(value: str) -> str:
    text = str(value)
    return text[:3] + "*" * max(0, len(text) - 6) + text[-3:] if len(text) > 6 else "***"


def _public(record: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(record))
    for key in ("seedAvailability", "seedReconnects"):
        out.pop(key, None)
    ownership = out["submission"].get("ownership") or {}
    if ownership.get("adminContact"):
        ownership["adminContactMasked"] = _mask_contact(ownership["adminContact"])
        ownership.pop("adminContact", None)
    identity = out["submission"].get("identity") or {}
    if identity.get("serial"):
        identity["serial"] = _mask_serial(identity["serial"])
    return out


def _camera_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project an installation record into this vendor's device asset shape.

    Carries no media URL, no NVR address and no credential - those never leave
    this service.
    """
    submission = record["submission"]
    identity = submission.get("identity", {})
    ownership = submission.get("ownership", {})
    geo = submission.get("geo", {})
    tech = submission.get("tech", {})
    policy = submission.get("policy", {})
    device_id = identity.get("deviceId")
    health = _touch_health(device_id)

    return {
        "id": device_id,
        "label": identity.get("label"),
        "lifecycle": record["lifecycle"],
        "requestRef": record["ref"],
        "serial": _mask_serial(identity["serial"]) if identity.get("serial") else None,
        "vendor": identity.get("vendor"),
        "model": identity.get("model"),
        "kind": identity.get("kind"),
        "purpose": identity.get("purpose"),
        "ownership": {
            "department": ownership.get("department"),
            "unit": ownership.get("unit"),
            "district": ownership.get("district"),
            "zone": ownership.get("zone"),
            "maintenanceAgency": ownership.get("maintenanceAgency"),
            "installationVendor": ownership.get("installationVendor"),
        },
        "geo": {
            "latitude": geo.get("latitude"),
            "longitude": geo.get("longitude"),
            "landmark": geo.get("landmark"),
            "junction": geo.get("junction"),
            "facing": geo.get("facing"),
            "coverage": geo.get("coverage"),
            "entryExit": geo.get("entryExit"),
        },
        "tech": {
            "transport": tech.get("transport"),
            "vmsName": tech.get("vmsName"),
            "vmsVendor": tech.get("vmsVendor"),
            "resolution": tech.get("resolution"),
            "framesPerSecond": tech.get("framesPerSecond"),
            "codec": tech.get("codec"),
            "retentionDays": tech.get("retentionDays"),
            "timezone": tech.get("timezone"),
            "installedOnMs": tech.get("installedOnMs"),
            "commissionedOnMs": tech.get("commissionedOnMs"),
            # Local viewing capability, reported for policy display only.
            "liveSupported": tech.get("liveSupported", True),
            "playbackSupported": tech.get("playbackSupported", True),
        },
        "policy": {"localRoles": policy.get("localRoles", [])},
        "approval": record.get("approval") or {},
        "syncedAtMs": record.get("syncedAtMs"),
        "availability": health["availability"],
        "lastFrameEpochMs": health["lastFrameEpochMs"],
        "reconnectCount": health["reconnectCount"],
        "dataClassification": "SYNTHETIC_DEMO",
    }


# --------------------------------------------------------------------------
# System
# --------------------------------------------------------------------------

@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "municipal-vms",
        "platform": "SMC-CityEye",
        "build": "3.1.0",
        "register": {
            "requests": len(REQUESTS),
            "commissioned": len([r for r in REQUESTS if r["lifecycle"] in COMMISSIONED_STATES]),
            "publishable": len([r for r in REQUESTS if r["lifecycle"] in PUBLISHABLE_STATES]),
        },
        "uptimeSeconds": int((datetime.now(timezone.utc) - SERVICE_STARTED_AT).total_seconds()),
        "serverEpochMs": _now_ms(),
        "footageCustody": "LOCAL_TO_MUNICIPAL_CORPORATION",
        "dataClassification": "SYNTHETIC_DEMO",
    }


# --------------------------------------------------------------------------
# Installation register
# --------------------------------------------------------------------------

@app.post("/vms/installation-requests", tags=["installation"], status_code=201)
def create_request(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Create a draft installation record in the corporation's own register."""
    _require_bearer(request)
    submission = payload.get("submission") or {}
    if not isinstance(submission, dict):
        raise HTTPException(status_code=400, detail={"code": "submission_must_be_object"})

    record = {
        "ref": _next_ref(),
        "lifecycle": LC_DRAFT,
        "raisedBy": payload.get("raisedBy") or "unknown",
        "createdAtMs": _now_ms(),
        "updatedAtMs": _now_ms(),
        "submittedAtMs": None,
        "submittedBy": None,
        "approval": None,
        "rejection": None,
        "syncedAtMs": None,
        "validationIssues": [],
        "submission": submission,
        "attachments": payload.get("attachments", []),
    }
    REQUESTS.append(record)
    return {"ok": True, "data": _public(record)}


@app.get("/vms/installation-requests", tags=["installation"])
def list_requests(request: Request, lifecycle: str | None = Query(default=None)) -> dict[str, Any]:
    _require_bearer(request)
    rows = [r for r in REQUESTS if not lifecycle or r["lifecycle"] == lifecycle.lower()]
    return {"ok": True, "meta": {"returned": len(rows)}, "data": [_public(r) for r in rows]}


@app.get("/vms/installation-requests/{ref}", tags=["installation"])
def get_request(ref: str, request: Request) -> dict[str, Any]:
    _require_bearer(request)
    return {"ok": True, "data": _public(_record(ref))}


@app.patch("/vms/installation-requests/{ref}", tags=["installation"])
def update_request(ref: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _require_bearer(request)
    record = _record(ref)
    if record["lifecycle"] not in EDITABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"code": "not_editable", "lifecycle": record["lifecycle"]}
        )
    submission = payload.get("submission")
    if isinstance(submission, dict):
        for group, values in submission.items():
            if isinstance(values, dict):
                record["submission"].setdefault(group, {}).update(values)
            else:
                record["submission"][group] = values
    if isinstance(payload.get("attachments"), list):
        record["attachments"] = payload["attachments"]
    record["updatedAtMs"] = _now_ms()
    return {"ok": True, "data": _public(record)}


@app.post("/vms/installation-requests/{ref}/submit", tags=["installation"])
def submit_request(ref: str, request: Request, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_bearer(request)
    record = _record(ref)
    if record["lifecycle"] not in EDITABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"code": "not_submittable", "lifecycle": record["lifecycle"]}
        )

    issues = _validate(record["submission"])
    record["submittedBy"] = payload.get("submittedBy") or record.get("raisedBy")
    record["submittedAtMs"] = _now_ms()
    record["updatedAtMs"] = _now_ms()
    record["validationIssues"] = issues

    if issues:
        record["lifecycle"] = LC_VALIDATION_FAILED
    else:
        record["lifecycle"] = LC_REGISTERED
        record["registeredAtMs"] = _now_ms()
        record["submission"].setdefault("tech", {}).setdefault("commissionedOnMs", _now_ms())
        _touch_health(record["submission"]["identity"]["deviceId"])

    return {"ok": not issues, "data": _public(record), "meta": {"validationIssues": issues}}


@app.post("/vms/installation-requests/{ref}/suspend", tags=["installation"])
def suspend_request(ref: str, request: Request, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_bearer(request)
    record = _record(ref)
    if record["lifecycle"] not in PUBLISHABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"code": "not_commissioned", "lifecycle": record["lifecycle"]}
        )
    record["lifecycle"] = LC_SUSPENDED
    record["suspensionReason"] = (payload.get("reason") or "").strip() or None
    record["updatedAtMs"] = _now_ms()
    HEALTH.setdefault(record["submission"]["identity"]["deviceId"], {})["availability"] = "DOWN"
    return {"ok": True, "data": _public(record)}


@app.post("/vms/installation-requests/{ref}/decommission", tags=["installation"])
def decommission_request(ref: str, request: Request, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    _require_bearer(request)
    record = _record(ref)
    if record["lifecycle"] not in PUBLISHABLE_STATES | {LC_SUSPENDED}:
        raise HTTPException(
            status_code=409, detail={"code": "not_commissioned", "lifecycle": record["lifecycle"]}
        )
    record["lifecycle"] = LC_RETIRED
    record["decommissionReason"] = (payload.get("reason") or "").strip() or None
    record["updatedAtMs"] = _now_ms()
    HEALTH.setdefault(record["submission"]["identity"]["deviceId"], {})["availability"] = "DOWN"
    return {"ok": True, "data": _public(record)}


@app.post("/vms/installation-requests/{ref}/mark-synchronized", tags=["installation"])
def mark_synchronized(ref: str, request: Request) -> dict[str, Any]:
    """Acknowledge that Sentinel has taken this record's metadata."""
    _require_bearer(request)
    record = _record(ref)
    if record["lifecycle"] not in PUBLISHABLE_STATES:
        raise HTTPException(
            status_code=409, detail={"code": "not_approved", "lifecycle": record["lifecycle"]}
        )
    record["lifecycle"] = LC_SYNCHRONIZED
    record["syncedAtMs"] = _now_ms()
    record["updatedAtMs"] = _now_ms()
    return {"ok": True, "data": _public(record)}


# --------------------------------------------------------------------------
# Device asset register (metadata Sentinel may read)
# --------------------------------------------------------------------------

@app.get("/vms/approved-cameras", tags=["cameras"])
def approved_cameras(request: Request) -> dict[str, Any]:
    """Device asset records for everything that reached commissioning.

    Suspended and retired devices are included, carrying their lifecycle, so
    the central registry can mark them unavailable instead of losing them.
    """
    _require_bearer(request)
    rows = [_camera_record(r) for r in REQUESTS if r["lifecycle"] in COMMISSIONED_STATES]
    return {
        "ok": True,
        "meta": {"returned": len(rows), "epochMs": _now_ms(), "metadataOnly": True},
        "data": rows,
    }


@app.get("/vms/cameras/{camera_id}/health", tags=["cameras"])
def camera_health(camera_id: str, request: Request) -> dict[str, Any]:
    _require_bearer(request)
    for record in REQUESTS:
        identity = record["submission"]["identity"]
        if (
            identity.get("deviceId", "").upper() == camera_id.upper()
            and record["lifecycle"] in COMMISSIONED_STATES
        ):
            entry = _touch_health(identity["deviceId"])
            return {
                "ok": True,
                "data": {
                    "id": identity["deviceId"],
                    "availability": entry["availability"],
                    "lastFrameEpochMs": entry["lastFrameEpochMs"],
                    "reconnectCount": entry["reconnectCount"],
                    "deviceModel": identity.get("model"),
                },
            }
    raise HTTPException(status_code=404, detail={"code": "no_such_camera", "id": camera_id})


@app.get("/vms/events", tags=["events"])
def events(
    request: Request,
    camera_id: str | None = Query(default=None),
    from_: int | None = Query(default=None, alias="from", description="epoch milliseconds"),
    to: int | None = Query(default=None, description="epoch milliseconds"),
) -> dict[str, Any]:
    """Generic (non-AI) device events. Times are epoch milliseconds, UTC."""
    _require_bearer(request)
    if camera_id:
        known = any(
            r["submission"]["identity"].get("deviceId", "").upper() == camera_id.upper()
            and r["lifecycle"] in COMMISSIONED_STATES
            for r in REQUESTS
        )
        if not known:
            raise HTTPException(status_code=404, detail={"code": "no_such_camera", "id": camera_id})
    rows = []
    for event in EVENTS:
        if camera_id and event["cam_ref"].upper() != camera_id.upper():
            continue
        if from_ is not None and event["at_ms"] < from_:
            continue
        if to is not None and event["at_ms"] > to:
            continue
        rows.append(event)
    rows.sort(key=lambda e: e["at_ms"], reverse=True)
    return {
        "ok": True,
        "meta": {"returned": len(rows), "time_base": "epoch_ms_utc"},
        "data": rows,
    }


@app.post("/vms/admin/cameras/{camera_id}/availability", tags=["system"])
def set_availability(
    camera_id: str, request: Request, state: str = Query(..., pattern="^(UP|DOWN|PARTIAL)$")
) -> dict[str, Any]:
    """Demo hook: flip a device's availability to rehearse degraded states."""
    _require_bearer(request)
    entry = HEALTH.setdefault(
        camera_id.upper(),
        {"availability": "UP", "reconnectCount": 0, "lastFrameEpochMs": _now_ms()},
    )
    entry["availability"] = state
    return {"ok": True, "data": {"id": camera_id, "availability": state}}


# --------------------------------------------------------------------------
# LOCAL video - municipal custody only. Sentinel never calls these.
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


def _grant_media(submission: dict[str, Any]) -> tuple[str, str, datetime]:
    """Issue a short-lived token for one device's bundled clip."""
    media_file = Path(
        (submission.get("tech") or {}).get("localStream", "/videos/municipal_01.mp4")
    ).name
    token = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS)
    GRANTS[token] = {"file": media_file, "expiresAt": expires}
    return media_file, token, expires


def _commissioned_submission(camera_id: str) -> dict[str, Any]:
    for record in REQUESTS:
        identity = record["submission"]["identity"]
        if (
            identity.get("deviceId", "").upper() == camera_id.upper()
            and record["lifecycle"] in PUBLISHABLE_STATES
        ):
            return record["submission"]
    raise HTTPException(status_code=404, detail={"code": "no_such_camera", "id": camera_id})


@app.get("/vms/live/{camera_id}", tags=["video"])
def device_live(camera_id: str, request: Request) -> dict[str, Any]:
    """Authorized live feed grant, in this vendor's own envelope."""
    _require_bearer(request)
    submission = _commissioned_submission(camera_id)
    if not (submission.get("tech") or {}).get("liveSupported", True):
        raise HTTPException(status_code=503, detail={"code": "live_not_enabled", "id": camera_id})
    media_file, token, expires = _grant_media(submission)
    return {
        "ok": True,
        "cam": camera_id,
        "playback": {
            "kind": "live",
            "transport": "http-mp4",
            "url": f"{PUBLIC_BASE_URL}/vms/local/media/{media_file}?token={token}",
            "expires_epoch_ms": int(expires.timestamp() * 1000),
            "custody": "MUNICIPAL_CORPORATION_LOCAL",
        },
        "meta": {"note": "SYNTHETIC_DEMO_FEED"},
    }


@app.get("/vms/recording/{camera_id}", tags=["video"])
def device_recording(
    camera_id: str,
    request: Request,
    from_: int | None = Query(default=None, alias="from"),
    to: int | None = Query(default=None),
) -> dict[str, Any]:
    """Authorized archive grant for a window (epoch-ms, this vendor's dialect)."""
    _require_bearer(request)
    submission = _commissioned_submission(camera_id)
    if not (submission.get("tech") or {}).get("playbackSupported", True):
        raise HTTPException(
            status_code=503, detail={"code": "playback_not_enabled", "id": camera_id}
        )
    media_file, token, expires = _grant_media(submission)
    # This vendor speaks epoch-ms; the shared helper takes ISO, so convert at
    # the boundary rather than teaching the helper two dialects.
    segment = _archive_segment(
        datetime.fromtimestamp(from_ / 1000, tz=timezone.utc) if from_ else None,
        datetime.fromtimestamp(to / 1000, tz=timezone.utc) if to else None,
    )
    return {
        "ok": True,
        "cam": camera_id,
        "playback": {
            "kind": "recording",
            "transport": "http-mp4",
            "url": f"{PUBLIC_BASE_URL}/vms/local/media/{media_file}?token={token}",
            "expires_epoch_ms": int(expires.timestamp() * 1000),
            "window": {"from": from_, "to": to},
            "custody": "MUNICIPAL_CORPORATION_LOCAL",
            "segmentStartSeconds": segment["segment_start_seconds"],
            "segmentEndSeconds": segment["segment_end_seconds"],
        },
        "meta": {"note": "SYNTHETIC_DEMO_FEED"},
    }


@app.get("/vms/local/live/{camera_id}", tags=["local-video"])
def local_live(camera_id: str, request: Request) -> dict[str, Any]:
    """Local live feed grant, issued only inside the corporation's own system."""
    _require_bearer(request)
    for record in REQUESTS:
        identity = record["submission"]["identity"]
        if identity.get("deviceId", "").upper() == camera_id.upper() and record["lifecycle"] in PUBLISHABLE_STATES:
            media_file = Path(
                record["submission"].get("tech", {}).get("localStream", "/videos/municipal_01.mp4")
            ).name
            token = secrets.token_urlsafe(24)
            GRANTS[token] = {
                "file": media_file,
                "expiresAt": datetime.now(timezone.utc) + timedelta(seconds=TOKEN_TTL_SECONDS),
            }
            return {
                "ok": True,
                "data": {
                    "id": identity["deviceId"],
                    "custody": "MUNICIPAL_CORPORATION_LOCAL",
                    "url": f"{PUBLIC_BASE_URL}/vms/local/media/{media_file}?token={token}",
                    "note": "LOCAL_DEPARTMENT_ACCESS_ONLY - not federated to Sentinel",
                },
            }
    raise HTTPException(status_code=404, detail={"code": "no_such_camera"})


@app.get("/vms/local/media/{filename}", tags=["local-video"])
def local_media(filename: str, request: Request, token: str = Query(...)) -> Response:
    """Tokenised local media. Municipal custody; Sentinel never receives a token."""
    entry = GRANTS.get(token)
    if entry is None or entry["expiresAt"] < datetime.now(timezone.utc):
        GRANTS.pop(token, None)
        raise HTTPException(status_code=401, detail={"code": "invalid_or_expired_token"})
    if entry["file"] != Path(filename).name:
        raise HTTPException(status_code=403, detail={"code": "token_scope_mismatch"})

    path = VIDEO_DIR / Path(filename).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "media_missing"})

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
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": detail})
