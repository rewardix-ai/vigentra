"""Vendor-dialect to canonical-schema translation.

The single place where a department system's vocabulary is mapped onto the
canonical one. Adapters call into it; nothing above the adapter layer needs to
know that one system says `SYNCED` and the other says `synchronized`, or that
one sends IST wall-clock strings while the other sends epoch milliseconds.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..schemas import (
    CameraStatus,
    CameraType,
    InstallationPurpose,
    LocalRole,
    RequestStatus,
    SourceType,
)

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def district_code(district: str | None) -> str:
    """`Ahmedabad` -> `AHM`. Three letters is the convention on Indian asset registers."""
    letters = re.sub(r"[^A-Za-z]", "", district or "")
    return (letters[:3] or "GEN").upper()


def make_camera_id(department_code: str, district: str | None, external_camera_id: str) -> str:
    """Canonical registry ID: `SENTINEL-<DEPT>-<DISTRICT>-<SEQ>`.

    The sequence comes from the LAST segment of the department's own camera ID,
    which is how these registers are numbered in practice. A purely numeric
    segment is zero-padded to four (`TRF-AHM-0001` -> `0001`); a segment that
    carries letters keeps them (`TRF-AHM-T0001` -> `T0001`).

    That distinction is load-bearing. An earlier version searched for trailing
    digits anywhere in the string, so `TRF-AHM-0001` and `TRF-AHM-T0001` both
    reduced to `0001` and the second camera could never be onboarded — the sync
    refused it as a canonical-ID clash, which is safe but looks like the
    onboarding flow is broken. Departments really do issue IDs with a letter in
    the last segment, so a register that cannot accept one is not usable.

    Known limitation: two DIFFERENT source systems can still land on the same
    canonical ID when the department code, district and sequence all coincide -
    `GRID-001` from the Sentinel grid against `TRF-AHM-0001` from the Traffic
    register, both Traffic Police in Ahmedabad. `sync_service` refuses to
    overwrite the incumbent and reports a degraded sync rather than silently
    rebinding the ID, so the register stays correct; the second camera simply
    does not appear until one of the two is renumbered. Folding the source
    system into the ID would remove the case entirely, at the cost of re-keying
    every camera in the registry.
    """
    external = (external_camera_id or "").strip()
    last_segment = re.split(r"[^A-Za-z0-9]+", external)[-1] if external else ""
    if last_segment.isdigit():
        sequence = last_segment.zfill(4)
    elif last_segment:
        sequence = last_segment.upper()
    else:
        sequence = re.sub(r"[^A-Za-z0-9]+", "", external).upper() or "0000"
    return f"SENTINEL-{department_code.upper()}-{district_code(district)}-{sequence}"


# ---------------------------------------------------------------------------
# Lifecycle vocabulary
# ---------------------------------------------------------------------------

#: Every lifecycle word either department system may send, folded onto the
#: canonical nine. Unknown words become DRAFT rather than being treated as
#: approved - failing closed is the only safe default for an approval gate.
REQUEST_STATUS_VOCABULARY: dict[str, RequestStatus] = {
    # canonical spellings
    "draft": RequestStatus.DRAFT,
    "submitted": RequestStatus.SUBMITTED,
    "validation_failed": RequestStatus.VALIDATION_FAILED,
    "registered": RequestStatus.REGISTERED,
    "synchronized": RequestStatus.SYNCHRONIZED,
    "suspended": RequestStatus.SUSPENDED,
    "decommissioned": RequestStatus.DECOMMISSIONED,
    # Traffic VMS dialect
    "synced": RequestStatus.SYNCHRONIZED,
    # Municipal VMS dialect
    "retired": RequestStatus.DECOMMISSIONED,
    # Legacy words from the approval-gated era, folded onto REGISTERED so an
    # older export still imports rather than silently landing in DRAFT.
    "approved": RequestStatus.REGISTERED,
    "pending_approval": RequestStatus.REGISTERED,
    "awaiting_approval": RequestStatus.REGISTERED,
    "active": RequestStatus.REGISTERED,
    "withdrawn": RequestStatus.DECOMMISSIONED,
}


def normalize_request_status(raw: Any) -> RequestStatus:
    if raw is None:
        return RequestStatus.DRAFT
    return REQUEST_STATUS_VOCABULARY.get(str(raw).strip().lower(), RequestStatus.DRAFT)


# ---------------------------------------------------------------------------
# Health vocabulary
# ---------------------------------------------------------------------------

HEALTH_VOCABULARY: dict[str, CameraStatus] = {
    "up": CameraStatus.ONLINE,
    "online": CameraStatus.ONLINE,
    "ok": CameraStatus.ONLINE,
    "available": CameraStatus.ONLINE,
    "down": CameraStatus.OFFLINE,
    "offline": CameraStatus.OFFLINE,
    "unavailable": CameraStatus.OFFLINE,
    "flapping": CameraStatus.DEGRADED,
    "partial": CameraStatus.DEGRADED,
    "degraded": CameraStatus.DEGRADED,
    "maintenance": CameraStatus.DEGRADED,
}


def normalize_health(raw: Any) -> CameraStatus:
    if raw is None:
        return CameraStatus.UNKNOWN
    return HEALTH_VOCABULARY.get(str(raw).strip().lower(), CameraStatus.UNKNOWN)


# ---------------------------------------------------------------------------
# Asset vocabulary
# ---------------------------------------------------------------------------

CAMERA_TYPE_VOCABULARY: dict[str, CameraType] = {
    "fixed": CameraType.FIXED,
    "static": CameraType.FIXED,
    "ptz": CameraType.PTZ,
    "pan-tilt-zoom": CameraType.PTZ,
    "dome": CameraType.DOME,
    "bullet": CameraType.BULLET,
    "anpr": CameraType.ANPR_CAPABLE,
    "anpr-capable": CameraType.ANPR_CAPABLE,
    "thermal": CameraType.THERMAL,
}


def normalize_camera_type(raw: Any) -> CameraType:
    if raw is None:
        return CameraType.OTHER
    return CAMERA_TYPE_VOCABULARY.get(str(raw).strip().lower(), CameraType.OTHER)


PURPOSE_VOCABULARY: dict[str, InstallationPurpose] = {
    "traffic monitoring": InstallationPurpose.TRAFFIC_MONITORING,
    "traffic": InstallationPurpose.TRAFFIC_MONITORING,
    "public safety": InstallationPurpose.PUBLIC_SAFETY,
    "safety": InstallationPurpose.PUBLIC_SAFETY,
    "junction monitoring": InstallationPurpose.JUNCTION_MONITORING,
    "junction": InstallationPurpose.JUNCTION_MONITORING,
    "highway monitoring": InstallationPurpose.HIGHWAY_MONITORING,
    "highway": InstallationPurpose.HIGHWAY_MONITORING,
}


def normalize_purpose(raw: Any) -> InstallationPurpose:
    if raw is None:
        return InstallationPurpose.OTHER
    return PURPOSE_VOCABULARY.get(str(raw).strip().lower(), InstallationPurpose.OTHER)


SOURCE_TYPE_VOCABULARY: dict[str, SourceType] = {
    "rtsp": SourceType.RTSP,
    "onvif": SourceType.ONVIF,
    "vms_api": SourceType.VMS_API,
    "vms-api": SourceType.VMS_API,
    "vmsapi": SourceType.VMS_API,
    "nvr": SourceType.NVR,
}


def normalize_source_type(raw: Any) -> SourceType:
    if raw is None:
        return SourceType.OTHER
    return SOURCE_TYPE_VOCABULARY.get(str(raw).strip().lower(), SourceType.OTHER)


_LOCAL_ROLE_VALUES = {role.value for role in LocalRole}


def normalize_local_roles(raw: Any) -> list[str]:
    """Keep only recognised local-VMS roles, de-duplicated and ordered."""
    if not raw:
        return []
    out: list[str] = []
    for item in raw:
        token = str(item).strip().lower()
        if token in _LOCAL_ROLE_VALUES and token not in out:
            out.append(token)
    return out


DIRECTION_VOCABULARY = {
    "n": "northbound",
    "north": "northbound",
    "s": "southbound",
    "south": "southbound",
    "e": "eastbound",
    "east": "eastbound",
    "w": "westbound",
    "west": "westbound",
}


def normalize_direction(raw: Any) -> str:
    """`north` and `northbound` describe one heading."""
    if not raw:
        return "unknown"
    token = str(raw).strip().lower()
    return DIRECTION_VOCABULARY.get(token, token)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def ist_wallclock_to_utc(value: Any) -> datetime | None:
    """Traffic VMS sends `dd-mm-YYYY HH:MM:SS` as Asia/Kolkata wall-clock."""
    if not value:
        return None
    try:
        return (
            datetime.strptime(str(value), "%d-%m-%Y %H:%M:%S")
            .replace(tzinfo=IST)
            .astimezone(timezone.utc)
        )
    except ValueError:
        return None


def ist_date(value: Any) -> date | None:
    """Traffic VMS sends plain dates as `dd-mm-YYYY`."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%d-%m-%Y").date()
    except ValueError:
        return None


def epoch_ms_to_utc(value: Any) -> datetime | None:
    """Municipal VMS sends epoch milliseconds."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def epoch_ms_to_date(value: Any) -> date | None:
    parsed = epoch_ms_to_utc(value)
    return parsed.date() if parsed else None


def to_utc(value: datetime | None) -> datetime | None:
    """Treat any naive datetime crossing the boundary as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_to_ist_wallclock(value: datetime) -> str:
    return to_utc(value).astimezone(IST).strftime("%d-%m-%Y %H:%M:%S")  # type: ignore[union-attr]


def date_to_ist_string(value: date | None) -> str | None:
    return value.strftime("%d-%m-%Y") if value else None


def date_to_epoch_ms(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime(value.year, value.month, value.day, tzinfo=IST).timestamp() * 1000)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact_endpoint(base_url: str) -> str:
    """Show operators the host:port of a department system, never a full URL."""
    stripped = re.sub(r"^[a-z]+://", "", base_url or "")
    stripped = stripped.split("/")[0]
    return stripped.split("@")[-1] or "unknown"


def mask_serial(value: Any) -> str | None:
    """Serial numbers identify hardware for theft and warranty purposes.

    They are shown partially so an operator can confirm a match, never in full.
    """
    if not value:
        return None
    text = str(value)
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}{'*' * (len(text) - 6)}{text[-3:]}"


def split_zone(zone: str | None, fallback_district: str) -> tuple[str, str | None]:
    """Municipal VMS ships `"West Zone / Vasna Ward"` instead of separate fields."""
    if not zone:
        return fallback_district, None
    parts = [part.strip() for part in str(zone).split("/") if part.strip()]
    if not parts:
        return fallback_district, None
    if len(parts) == 1:
        return fallback_district, parts[0]
    return fallback_district, " / ".join(parts)


def build_capabilities(*, live: bool, playback: bool, commissioned: bool) -> list[str]:
    """Canonical capability list for a camera.

    `metadata` and `health` are always present - the registry holds those for
    every camera it knows about. `live` and `playback` appear only when the
    owning department serves them AND the asset is still in service, so a
    suspended camera never advertises a viewing capability it cannot honour.
    """
    capabilities = ["metadata", "health"]
    if commissioned and live:
        capabilities.append("live")
    if commissioned and playback:
        capabilities.append("playback")
    return capabilities


def normalize_city(value: str | None) -> str:
    """`  Ahmedabad ` -> `ahmedabad`, for case-insensitive city filtering."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()
