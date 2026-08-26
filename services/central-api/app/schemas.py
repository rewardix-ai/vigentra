"""Canonical Pydantic v2 schemas - the vendor-neutral, METADATA-ONLY contract.

Module 1 federates camera *records*, not camera *feeds*. There is no stream URL,
no RTSP address, no media token and no video-session model anywhere in this
file, and `tests/test_no_video_access.py` asserts that stays true.

A department system's own field names, lifecycle vocabulary, timestamp format
and error envelope stop at its adapter and never reach the database, the API or
the dashboard.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


def iso_z(value: datetime | None) -> str | None:
    """Serialise as UTC with a trailing Z. All internal time is UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

class CameraType(str, Enum):
    FIXED = "fixed"
    PTZ = "PTZ"
    DOME = "dome"
    BULLET = "bullet"
    ANPR_CAPABLE = "ANPR-capable"
    THERMAL = "thermal"
    OTHER = "other"


class InstallationPurpose(str, Enum):
    TRAFFIC_MONITORING = "traffic monitoring"
    PUBLIC_SAFETY = "public safety"
    JUNCTION_MONITORING = "junction monitoring"
    HIGHWAY_MONITORING = "highway monitoring"
    OTHER = "other"


class SourceType(str, Enum):
    """How the OWNING DEPARTMENT reaches the camera. Sentinel never uses it."""

    RTSP = "RTSP"
    ONVIF = "ONVIF"
    VMS_API = "VMS_API"
    NVR = "NVR"
    OTHER = "other"


class LocalRole(str, Enum):
    """Roles inside the owning department's VMS.

    A separate namespace from Sentinel's own roles. These describe who may see
    FOOTAGE in the department's system; Sentinel only records the summary.
    """

    DEPARTMENT_OPERATOR = "department_operator"
    DISTRICT_SUPERVISOR = "district_supervisor"
    STATE_SUPERVISOR = "state_supervisor"
    INVESTIGATOR = "investigator"
    SYSTEM_ADMIN = "system_admin"
    AUDITOR = "auditor"


class RequestStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    #: Passed the owning department's validation and is live in the registry.
    #: There is no human approval step between SUBMITTED and here.
    REGISTERED = "REGISTERED"
    SYNCHRONIZED = "SYNCHRONIZED"
    SUSPENDED = "SUSPENDED"
    DECOMMISSIONED = "DECOMMISSIONED"


#: Only these two states may enter the central camera registry as active.
PUBLISHABLE_STATUSES = {RequestStatus.REGISTERED, RequestStatus.SYNCHRONIZED}
#: These were commissioned once, so the registry keeps them, marked unavailable.
WITHDRAWN_STATUSES = {RequestStatus.SUSPENDED, RequestStatus.DECOMMISSIONED}


class InstallationStatus(str, Enum):
    COMMISSIONED = "COMMISSIONED"
    SUSPENDED = "SUSPENDED"
    DECOMMISSIONED = "DECOMMISSIONED"


class CameraStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class SyncStatus(str, Enum):
    SYNCHRONIZED = "SYNCHRONIZED"
    PENDING = "PENDING"
    WITHDRAWN = "WITHDRAWN"
    FAILED = "FAILED"


class AttachmentType(str, Enum):
    SITE_SURVEY = "site_survey"
    INSTALLATION_CERTIFICATE = "installation_certificate"
    CAMERA_PHOTOGRAPH = "camera_photograph"
    APPROVAL_DOCUMENT = "approval_document"


# ---------------------------------------------------------------------------
# Installation form
# ---------------------------------------------------------------------------

class AttachmentRef(BaseModel):
    """A REFERENCE to a document held by the owning department.

    Sentinel stores the pointer, never the file, and never any footage.
    """

    document_type: AttachmentType
    reference: str = Field(max_length=128, description="Department document reference number")
    filename: str | None = Field(default=None, max_length=255)


class InstallationForm(BaseModel):
    """The CCTV installation/onboarding form.

    Submitted by a department installer, validated and approved inside that
    department's own system. Only after approval does any of it reach Sentinel.
    """

    model_config = ConfigDict(use_enum_values=True)

    # --- camera identity ------------------------------------------------
    camera_name: str = Field(min_length=2, max_length=120)
    external_camera_id: str = Field(min_length=2, max_length=64)
    camera_serial_number: str | None = Field(default=None, max_length=64)
    camera_vendor: str | None = Field(default=None, max_length=120)
    camera_model: str | None = Field(default=None, max_length=120)
    camera_type: CameraType = CameraType.FIXED
    installation_purpose: InstallationPurpose = InstallationPurpose.PUBLIC_SAFETY

    # --- ownership and administration -----------------------------------
    owning_department: str = Field(max_length=120)
    owning_unit: str = Field(max_length=160)
    district: str = Field(max_length=120)
    police_station_or_zone: str | None = Field(default=None, max_length=160)
    # Held by the department. Sentinel receives it masked and never in full.
    local_admin_contact: str | None = Field(default=None, max_length=120)
    maintenance_agency: str | None = Field(default=None, max_length=160)
    installation_vendor: str | None = Field(default=None, max_length=160)

    # --- location and orientation ---------------------------------------
    latitude: float
    longitude: float
    address_or_landmark: str | None = Field(default=None, max_length=255)
    road_or_junction: str = Field(max_length=160)
    view_direction: str = Field(max_length=64)
    coverage_description: str | None = Field(default=None, max_length=500)
    entry_exit_zone_description: str | None = Field(default=None, max_length=500)

    # --- technical metadata ---------------------------------------------
    source_type: SourceType = SourceType.VMS_API
    vms_name: str | None = Field(default=None, max_length=120)
    vms_vendor: str | None = Field(default=None, max_length=120)
    resolution: str | None = Field(default=None, max_length=32)
    fps: int | None = Field(default=None, ge=1, le=240)
    codec: str | None = Field(default=None, max_length=32)
    # Stored so the policy summary can be shown. Sentinel exposes no viewing
    # link either way - see access_policy_summary.sentinel_video_access.
    supports_live: bool = True
    supports_playback: bool = True
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    timezone: str = "Asia/Kolkata"
    installation_date: date
    commissioning_date: date | None = None

    # --- local permission policy ----------------------------------------
    permitted_local_roles: list[LocalRole] = Field(
        min_length=1,
        description=(
            "Roles permitted to view FOOTAGE inside the owning department's VMS. "
            "Sentinel records this summary and grants no video access of its own."
        ),
    )

    # --- attachments ------------------------------------------------------
    attachments: list[AttachmentRef] = Field(default_factory=list)

    @field_validator("latitude")
    @classmethod
    def _check_latitude(cls, value: float) -> float:
        if not -90.0 <= value <= 90.0:
            raise ValueError("latitude must be between -90 and 90")
        return value

    @field_validator("longitude")
    @classmethod
    def _check_longitude(cls, value: float) -> float:
        if not -180.0 <= value <= 180.0:
            raise ValueError("longitude must be between -180 and 180")
        return value


class InstallationFormPatch(BaseModel):
    """Partial update of a draft. Every field optional; validation runs on submit."""

    model_config = ConfigDict(use_enum_values=True, extra="forbid")

    camera_name: str | None = None
    external_camera_id: str | None = None
    camera_serial_number: str | None = None
    camera_vendor: str | None = None
    camera_model: str | None = None
    camera_type: CameraType | None = None
    installation_purpose: InstallationPurpose | None = None
    owning_department: str | None = None
    owning_unit: str | None = None
    district: str | None = None
    police_station_or_zone: str | None = None
    local_admin_contact: str | None = None
    maintenance_agency: str | None = None
    installation_vendor: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    address_or_landmark: str | None = None
    road_or_junction: str | None = None
    view_direction: str | None = None
    coverage_description: str | None = None
    entry_exit_zone_description: str | None = None
    source_type: SourceType | None = None
    vms_name: str | None = None
    vms_vendor: str | None = None
    resolution: str | None = None
    fps: int | None = None
    codec: str | None = None
    supports_live: bool | None = None
    supports_playback: bool | None = None
    retention_days: int | None = None
    timezone: str | None = None
    installation_date: date | None = None
    commissioning_date: date | None = None
    permitted_local_roles: list[LocalRole] | None = None
    attachments: list[AttachmentRef] | None = None


class InstallationRequestCreate(BaseModel):
    """Create a draft. The department is inferred from the signed-in operator."""

    form: InstallationForm


class InstallationRequestPatch(BaseModel):
    form: InstallationFormPatch


class RejectionRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500, description="Mandatory - a bare refusal is not auditable")


class WithdrawalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class InstallationRequestOut(BaseModel):
    """An installation record as Sentinel presents it."""

    model_config = ConfigDict(use_enum_values=True)

    request_id: str
    source_system: str
    owning_department: str
    owning_unit: str | None = None
    status: RequestStatus
    camera_name: str | None = None
    external_camera_id: str | None = None
    district: str | None = None

    created_by: str | None = None
    created_at: datetime | None = None
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    approved_by: str | None = None
    approved_by_role: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    withdrawal_reason: str | None = None
    synchronized_at: datetime | None = None
    updated_at: datetime | None = None

    validation_errors: list[str] = Field(default_factory=list)
    form: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    #: Always false in Module 1, on every record, without exception.
    sentinel_video_access: bool = False

    @field_serializer(
        "created_at", "submitted_at", "approved_at", "rejected_at", "synchronized_at", "updated_at"
    )
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


# ---------------------------------------------------------------------------
# Canonical camera - metadata only
# ---------------------------------------------------------------------------

class VideoMode(str, Enum):
    LIVE = "live"
    PLAYBACK = "playback"


class VideoAccessState(str, Enum):
    """What the *current* user may do with this camera's footage."""

    LIVE_AND_PLAYBACK = "live_and_playback"
    LIVE_ONLY = "live_only"
    PLAYBACK_ONLY = "playback_only"
    DENIED = "denied"
    #: The owning department has not enabled brokered video for this camera.
    NOT_ENABLED_BY_OWNER = "not_enabled_by_owner"
    #: Another unit owns it - ask them. This is a route to access, not a wall.
    NEEDS_UNIT_APPROVAL = "needs_unit_approval"
    #: Camera is suspended, decommissioned or offline.
    CAMERA_UNAVAILABLE = "camera_unavailable"


class CameraLocation(BaseModel):
    city: str = ""
    district: str
    zone: str | None = None
    road_or_junction: str | None = None
    landmark: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    view_direction: str = "unknown"


class TechnicalSummary(BaseModel):
    resolution: str | None = None
    fps: int | None = None
    codec: str | None = None
    timezone: str = "Asia/Kolkata"
    source_type: SourceType | None = None
    retention_days: int | None = None


class InstallationSummary(BaseModel):
    installation_date: date | None = None
    commissioning_date: date | None = None
    installation_status: InstallationStatus = InstallationStatus.COMMISSIONED
    installation_request_id: str | None = None


class ApprovalSummary(BaseModel):
    status: RequestStatus
    approved_by_role: str | None = None
    approved_at: datetime | None = None

    @field_serializer("approved_at")
    def _ser_approved(self, value: datetime | None) -> str | None:
        return iso_z(value)


class AccessPolicySummary(BaseModel):
    """What the owning department permits, and what Sentinel permits.

    `sentinel_video_access` is the OWNING DEPARTMENT's decision about whether
    Sentinel may broker footage for this camera at all. It is a precondition,
    not a grant: a true value still has to survive the role, department, city,
    zone and camera-state checks in `services/video_permissions.py`.
    """

    local_video_access: bool = Field(description="Does the owning department's VMS serve footage for this camera")
    sentinel_video_access: bool = Field(
        default=False, description="Owner permits Sentinel to broker video for this camera"
    )
    permitted_local_roles: list[str] = Field(default_factory=list)
    footage_custodian: str = Field(description="Department that holds the footage")
    metadata_visibility_level: str = "standard"
    policy_version: int = 1

class CameraHealthSummary(BaseModel):
    status: CameraStatus = CameraStatus.UNKNOWN
    last_heartbeat_utc: datetime | None = None
    last_frame_utc: datetime | None = None
    last_metadata_sync_utc: datetime | None = None
    latency_ms: float | None = None
    reconnect_count: int | None = None

    @field_serializer("last_heartbeat_utc", "last_frame_utc", "last_metadata_sync_utc")
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


class SentinelSyncSummary(BaseModel):
    status: SyncStatus = SyncStatus.PENDING
    synced_at_utc: datetime | None = None
    source_request_id: str | None = None

    @field_serializer("synced_at_utc")
    def _ser_synced(self, value: datetime | None) -> str | None:
        return iso_z(value)


class Camera(BaseModel):
    """The canonical camera record. Identical shape for every department system.

    Contains no stream URL, no NVR address, no credential and no video token.
    """

    model_config = ConfigDict(use_enum_values=True)

    camera_id: str = Field(description="Canonical registry ID, e.g. SENTINEL-TRAFFIC-AHM-0001")
    external_camera_id: str = Field(description="The ID the owning department uses")
    source_system: str
    owning_department: str
    department_code: str = ""
    owning_unit: str | None = None
    name: str
    vendor: str | None = None
    model: str | None = None
    camera_type: CameraType = CameraType.FIXED
    installation_purpose: InstallationPurpose | None = None
    location: CameraLocation
    source_type: SourceType | None = None
    vms_name: str | None = None
    technical_summary: TechnicalSummary
    installation: InstallationSummary
    approval: ApprovalSummary
    access_policy_summary: AccessPolicySummary
    health: CameraHealthSummary
    sentinel_sync: SentinelSyncSummary

    capabilities: list[str] = Field(default_factory=list)
    is_demo_data: bool = True

    #: What the CURRENT reader may do with this camera's footage. Computed per
    #: request from role + department + city + zone + camera policy, so the same
    #: camera reads `denied` for one account and `live_and_playback` for another.
    video_access: VideoAccessState = VideoAccessState.DENIED
    video_access_reason: str | None = None


class CameraDetail(Camera):
    """Camera plus the fields the detail page shows, subject to role visibility."""

    camera_serial_masked: str | None = None
    police_station_or_zone: str | None = None
    maintenance_agency: str | None = None
    installation_vendor: str | None = None
    coverage_description: str | None = None
    entry_exit_zone_description: str | None = None
    address_or_landmark: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    first_synced_at: datetime | None = None
    last_synced_at: datetime | None = None
    visibility_level: str = "standard"
    redacted_fields: list[str] = Field(default_factory=list)

    @field_serializer("first_synced_at", "last_synced_at")
    def _ser_sync_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


class CameraHealthOut(CameraHealthSummary):
    camera_id: str
    source_system: str
    owning_department: str
    detail: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime | None = None

    @field_serializer("checked_at")
    def _ser_checked(self, value: datetime | None) -> str | None:
        return iso_z(value)


class AccessPolicyOut(BaseModel):
    """The full access-policy view for one camera."""

    camera_id: str
    camera_name: str
    owning_department: str
    source_system: str
    sentinel_metadata_access: str = Field(description="How the signed-in role may read this record")
    sentinel_video_access: bool = False
    sentinel_video_access_note: str = (
        "Sentinel brokers footage only for cameras the owning department has "
        "enabled, and only for authenticated users whose role, department, city "
        "and zone scope match the camera. Sessions are short-lived and audited."
    )
    #: What the reader themselves may do, after every scope check.
    caller_video_access: str = "denied"
    caller_video_access_reason: str | None = None
    local_video_access_enabled: bool
    permitted_local_roles: list[str] = Field(default_factory=list)
    footage_custodian: str
    local_vms_name: str | None = None
    policy_version: int = 1
    approved_by_role: str | None = None
    approved_at: datetime | None = None

    @field_serializer("approved_at")
    def _ser_approved(self, value: datetime | None) -> str | None:
        return iso_z(value)


class CameraProvenance(BaseModel):
    """Where a camera record came from. Carries no secret."""

    source_system: str
    external_camera_id: str
    provider: str
    provider_version: str
    imported_at_utc: datetime | None = None

    @field_serializer("imported_at_utc")
    def _ser_imported(self, value: datetime | None) -> str | None:
        return iso_z(value)


class ExternalCameraRecord(BaseModel):
    """A camera as a provider hands it over, already normalised.

    This is the boundary type between a provider and the registry. It has no
    field for a password, a stream URL, an NVR host or a media token — a
    provider that tried to pass one would have nowhere to put it.
    """

    model_config = ConfigDict(use_enum_values=True)

    external_camera_id: str
    source_system: str

    department: str
    department_code: str
    city: str
    district: str
    zone: str | None = None

    camera_name: str
    camera_type: CameraType = CameraType.FIXED
    vendor: str | None = None
    model: str | None = None
    installation_purpose: InstallationPurpose | None = None
    camera_serial_masked: str | None = None

    owning_unit: str | None = None
    maintenance_agency: str | None = None
    installation_vendor: str | None = None

    latitude: float | None = None
    longitude: float | None = None
    address_or_landmark: str | None = None
    road: str | None = None
    view_direction: str = "unknown"
    coverage_description: str | None = None
    entry_exit_zone_description: str | None = None

    source_type: SourceType | None = None
    vms_name: str | None = None
    vms_vendor: str | None = None
    resolution: str | None = None
    fps: int | None = None
    codec: str | None = None
    retention_days: int | None = None
    timezone_name: str = "Asia/Kolkata"

    capabilities: list[str] = Field(default_factory=lambda: ["metadata", "health"])
    installation_status: InstallationStatus = InstallationStatus.COMMISSIONED
    request_status: RequestStatus = RequestStatus.REGISTERED
    installation_request_id: str | None = None
    installation_date: date | None = None
    commissioning_date: date | None = None
    approved_by_role: str | None = None
    approved_at: datetime | None = None

    health_status: CameraStatus = CameraStatus.UNKNOWN
    last_seen_utc: datetime | None = None
    reconnect_count: int | None = None

    #: Whether the OWNING DEPARTMENT permits Sentinel to broker video.
    video_access_enabled: bool = False
    permitted_local_roles: list[str] = Field(default_factory=list)

    is_demo_data: bool = True
    provenance: CameraProvenance | None = None

    @field_serializer("approved_at", "last_seen_utc")
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


class CityOut(BaseModel):
    """One city in the registry, with the departments operating in it."""

    city: str
    city_normalized: str
    camera_count: int
    online: int = 0
    departments: list[str] = Field(default_factory=list)
    districts: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)


class DepartmentOut(BaseModel):
    department: str
    department_code: str
    source_system: str | None = None
    camera_count: int
    online: int = 0
    cities: list[str] = Field(default_factory=list)
    video_capable_cameras: int = 0


class PagedCameras(BaseModel):
    """The list response shape used by the city/department browse endpoints."""

    items: list["Camera"] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    total: int = 0


class Event(BaseModel):
    """A normalised generic event.

    Module 1 carries motion, line-crossing, tamper and device-health event
    types only. No ANPR, plate, vehicle, face or identity fields — Phase 2
    modules extend `payload` and add their own tables.
    """

    event_id: str
    source_system: str
    external_event_id: str
    camera_id: str
    event_type: str
    severity: str | None = None
    timestamp_utc: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("timestamp_utc")
    def _ser_ts(self, value: datetime) -> str | None:
        return iso_z(value)


class EventOut(Event):
    camera_name: str | None = None
    owning_department: str | None = None
    district: str | None = None
    ingested_at: datetime | None = None

    @field_serializer("ingested_at")
    def _ser_ingested(self, value: datetime | None) -> str | None:
        return iso_z(value)


class CorrelationPair(BaseModel):
    """Two events at nearby cameras within a small time window."""

    a: EventOut
    b: EventOut
    distance_m: float
    delta_seconds: float
    cross_department: bool


class CorrelationResponse(BaseModel):
    window_seconds: int
    radius_m: int
    considered: int
    pairs: list[CorrelationPair]


class CameraMetadata(BaseModel):
    """Adapter output: one commissioned camera's canonical metadata, flattened.

    This is the internal hand-off between an adapter and the sync service. It
    maps one-to-one onto the `cameras` table. Like everything else in Module 1
    it carries no stream URL, no NVR address and no credential.
    """

    model_config = ConfigDict(use_enum_values=True)

    camera_id: str
    external_camera_id: str
    source_system: str
    installation_request_id: str | None = None

    name: str
    vendor: str | None = None
    model: str | None = None
    camera_type: CameraType = CameraType.FIXED
    installation_purpose: InstallationPurpose | None = None
    camera_serial_masked: str | None = None

    owning_department: str
    department_code: str = ""
    owning_unit: str | None = None
    police_station_or_zone: str | None = None
    maintenance_agency: str | None = None
    installation_vendor: str | None = None

    #: City and zone are the fields the department dialects disagree on most.
    #: Both fall back to the values recorded when the source was registered.
    city: str = ""
    zone: str | None = None
    district: str
    road_or_junction: str | None = None
    landmark: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    view_direction: str = "unknown"
    coverage_description: str | None = None
    entry_exit_zone_description: str | None = None

    source_type: SourceType | None = None
    vms_name: str | None = None
    vms_vendor: str | None = None
    resolution: str | None = None
    fps: int | None = None
    codec: str | None = None
    retention_days: int | None = None
    timezone_name: str = "Asia/Kolkata"

    installation_date: date | None = None
    commissioning_date: date | None = None
    installation_status: InstallationStatus = InstallationStatus.COMMISSIONED

    request_status: RequestStatus = RequestStatus.REGISTERED
    approved_by_role: str | None = None
    approved_at: datetime | None = None

    #: Whether the OWNING DEPARTMENT's VMS serves footage for this camera, and
    #: therefore whether Sentinel may broker it. A precondition, not a grant.
    local_video_access: bool = True
    permitted_local_roles: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=lambda: ["metadata", "health"])

    health_status: CameraStatus = CameraStatus.UNKNOWN
    last_frame_utc: datetime | None = None
    reconnect_count: int | None = None
    source_synced_at: datetime | None = None

    attachments: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Video sessions
# ---------------------------------------------------------------------------

class VideoSessionCreate(BaseModel):
    """Request an authorized viewing session.

    `reason` is mandatory: a viewing action that nobody can explain afterwards
    is exactly what the audit trail exists to prevent.
    """

    model_config = ConfigDict(use_enum_values=True)

    camera_id: str
    mode: VideoMode = VideoMode.LIVE
    #: Re-entered at the moment of viewing, not just at sign-in. A bearer token
    #: left open on an unattended workstation is enough to browse the registry;
    #: it must not also be enough to open somebody's camera. The value is
    #: verified and discarded - it is never stored, never audited and never
    #: echoed back, and `repr=False` keeps it out of tracebacks and logs.
    password: str = Field(repr=False, min_length=1)
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    reason: str = Field(min_length=5, max_length=500)
    case_id: str | None = Field(default=None, max_length=64)

    @field_validator("case_id")
    @classmethod
    def _clean_case(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class VideoSessionOut(BaseModel):
    """What the browser is allowed to see.

    No source URL, no vendor ticket, no credential — the only address here is
    Sentinel's own opaque stream route.
    """

    model_config = ConfigDict(use_enum_values=True)

    session_id: str
    camera_id: str
    camera_name: str | None = None
    department: str
    city: str | None = None
    mode: VideoMode
    stream_url: str = Field(description="Protected Sentinel route - the only URL a client may use")
    #: How the bytes behind `stream_url` are packaged, so the player can pick a
    #: strategy without guessing from the URL. `hls` needs a JS player in most
    #: browsers; `http-mp4` plays natively in a <video> element.
    stream_protocol: str = "http-mp4"
    status: str = "active"
    expires_at_utc: datetime
    expires_in_seconds: int = 0
    watermark: str = ""
    case_id: str | None = None
    reason: str | None = None
    audit_id: str | None = None
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    access_count: int = 0
    is_demo_data: bool = True
    #: Which part of the owning department's recording answers the requested
    #: window, in seconds from the start of the returned media. The player
    #: turns this into a media fragment so a 09:00-09:05 request and a
    #: 14:30-14:40 request visibly differ. Absent for live.
    segment_start_seconds: float | None = None
    segment_end_seconds: float | None = None

    @field_serializer("expires_at_utc", "start_time_utc", "end_time_utc")
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


# ---------------------------------------------------------------------------
# Video access requests
# ---------------------------------------------------------------------------

class VideoAccessRequestCreate(BaseModel):
    """Ask the owning unit for access to one of its cameras."""

    camera_id: str
    #: Mandatory. A viewing request nobody can explain later is exactly what
    #: the audit trail exists to prevent.
    reason: str = Field(min_length=5, max_length=500)
    case_id: str | None = Field(default=None, max_length=64)
    modes: list[VideoMode] = Field(default_factory=lambda: [VideoMode.LIVE, VideoMode.PLAYBACK])


class VideoAccessDecision(BaseModel):
    """The owning unit's answer."""

    note: str | None = Field(default=None, max_length=500)
    #: Narrow the grant on the way through, e.g. playback only.
    modes: list[VideoMode] | None = None
    #: Days the grant lasts. Omitted uses VIDEO_GRANT_DEFAULT_DAYS.
    days: int | None = Field(default=None, ge=1, le=365)


class VideoAccessRequestOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    grant_id: str
    camera_id: str
    camera_name: str | None = None
    owning_department: str
    requested_by: str
    requester_department: str
    requester_role: str
    reason: str
    case_id: str | None = None
    status: str
    allowed_modes: list[str] = Field(default_factory=list)
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    requested_at: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = False

    @field_serializer("decided_at", "requested_at", "expires_at")
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


# ---------------------------------------------------------------------------
# Detections
# ---------------------------------------------------------------------------

class BoundingBox(BaseModel):
    """xyxy in pixels, validated against the frame if dimensions are supplied."""

    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def from_xyxy(cls, values: list[float]) -> "BoundingBox":
        if len(values) != 4:
            raise ValueError("bbox_xyxy must have exactly four values")
        return cls(x1=values[0], y1=values[1], x2=values[2], y2=values[3])

    def as_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]


#: Generic classes only. No plate text, no face, no vehicle identity.
DETECTION_CLASSES = [
    "person",
    "car",
    "motorcycle",
    "bus",
    "truck",
    "auto-rickshaw",
    "bicycle",
]


class FrameQuality(str, Enum):
    NORMAL = "normal"
    LOW_LIGHT = "low_light"
    OVEREXPOSED = "overexposed"
    BLURRED = "blurred"


class DetectionIn(BaseModel):
    """One detection as an edge worker submits it."""

    detection_id: str = Field(min_length=3, max_length=128)
    camera_id: str
    timestamp_utc: datetime
    class_name: str = Field(max_length=64)
    class_id: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    bbox_xyxy: list[float] = Field(min_length=4, max_length=4)
    model_name: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=80)
    source_mode: str = "mock"
    inference_latency_ms: float | None = Field(default=None, ge=0)
    frame_quality: FrameQuality | None = None
    evidence_reference: str | None = Field(default=None, max_length=255)
    is_demo_data: bool = True
    provenance: dict[str, Any] = Field(default_factory=dict)

    #: Set only when the edge worker ran with ANPR enabled. The edge discards
    #: anything that does not parse as a registration number, so a value here
    #: has already passed format and state-code validation.
    plate_text: str | None = Field(default=None, max_length=16)
    plate_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    plate_bbox_xyxy: list[float] | None = None
    plate_reader: str | None = Field(default=None, max_length=80)

    @field_validator("bbox_xyxy")
    @classmethod
    def _check_bbox(cls, value: list[float]) -> list[float]:
        x1, y1, x2, y2 = value
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox_xyxy must satisfy x2 > x1 and y2 > y1")
        if any(coordinate < 0 for coordinate in value):
            raise ValueError("bbox_xyxy coordinates must be non-negative")
        return value

    @field_validator("class_name")
    @classmethod
    def _known_class(cls, value: str) -> str:
        token = value.strip().lower()
        if token not in DETECTION_CLASSES:
            raise ValueError(
                f"class_name must be one of {', '.join(DETECTION_CLASSES)} in this phase"
            )
        return token


class DetectionBatch(BaseModel):
    detections: list[DetectionIn] = Field(min_length=1, max_length=500)


class DetectionOut(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    detection_id: str
    camera_id: str
    camera_name: str | None = None
    owning_department: str | None = None
    city: str | None = None
    timestamp_utc: datetime
    class_name: str
    class_id: int
    confidence: float
    bbox_xyxy: list[float] = Field(default_factory=list)
    model_name: str
    model_version: str
    source_mode: str
    inference_latency_ms: float | None = None
    frame_quality: str | None = None
    evidence_reference: str | None = None
    is_demo_data: bool = True
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None

    #: Withheld unless the reader holds `plate:read`. `plate_withheld` says so
    #: explicitly rather than leaving the UI to infer meaning from a null.
    plate_text: str | None = None
    plate_confidence: float | None = None
    plate_bbox_xyxy: list[float] | None = None
    plate_reader: str | None = None
    plate_withheld: bool = False

    @field_serializer("timestamp_utc", "created_at")
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


class DetectionIngestResult(BaseModel):
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    #: Per-detection errors, so an edge worker can fix and resend just those.
    errors: list[dict[str, Any]] = Field(default_factory=list)
    model_name: str | None = None
    model_version: str | None = None


class DetectorHealth(BaseModel):
    enabled: bool
    detector: str
    model_name: str
    model_version: str
    device: str
    weights_available: bool
    classes: list[str] = Field(default_factory=list)
    confidence_threshold: float
    frame_sample_interval: int
    detail: str | None = None
    #: Stated plainly on the health endpoint so no consumer treats scores as truth.
    accuracy_disclaimer: str = (
        "Detections are probabilistic. Confidence is a model score, not a guarantee, "
        "and degrades in low light, glare, occlusion and motion blur."
    )


# ---------------------------------------------------------------------------
# Vehicle reference registry
# ---------------------------------------------------------------------------

class VehicleOut(BaseModel):
    """One vehicle reference record.

    Attributes only. There is no owner field, and there must never be one -
    see services/vehicle_service.py for the import guard that enforces it.
    """

    registration_number: str
    registration_date: date | None = None
    registration_valid_upto: date | None = None
    vehicle_category_code: str | None = None
    vehicle_class: str | None = None
    make: str | None = None
    model: str | None = None
    body_type: str | None = None
    fuel_type: str | None = None
    colour: str | None = None
    registration_status: str | None = None
    #: RTO / place of registration as published - a place, not an address.
    registered_at: str | None = None
    status_as_on: date | None = None
    source: str = "reference_import"
    is_demo_data: bool = True
    imported_at: datetime | None = None

    @field_serializer("imported_at")
    def _ser_imported(self, value: datetime | None) -> str | None:
        return iso_z(value)


class VehicleSearchResponse(BaseModel):
    items: list[VehicleOut] = Field(default_factory=list)
    total: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)
    #: Repeated on every response so the boundary travels with the data.
    scope_note: str = ""


class VehicleFacets(BaseModel):
    makes: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    fuel_types: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sources and metadata synchronisation
# ---------------------------------------------------------------------------

class SourceOut(BaseModel):
    source_system: str
    display_name: str
    department: str
    adapter: str
    adapter_version: str
    status: str
    camera_count: int
    pending_requests: int = 0
    endpoint: str = Field(description="Redacted upstream endpoint - host and port only")
    last_sync_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    latency_ms: float | None = None

    @field_serializer("last_sync_at", "last_success_at")
    def _ser_times(self, value: datetime | None) -> str | None:
        return iso_z(value)


class SyncSourceResult(BaseModel):
    """Per-source outcome. Errors here never fail another source's run."""

    approved_records_seen: int = 0
    synchronized: int = 0
    skipped_unregistered: int = 0
    withdrawn: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    status: str = "unknown"


class SyncResponse(BaseModel):
    success: bool
    #: Stated in the payload so no consumer can mistake this for video federation.
    metadata_only: bool = True
    sources: dict[str, SyncSourceResult]
    total_cameras: int = 0
    synced_at: datetime

    @field_serializer("synced_at")
    def _ser_synced(self, value: datetime) -> str | None:
        return iso_z(value)


# ---------------------------------------------------------------------------
# Audit and identity
# ---------------------------------------------------------------------------

class AuditOut(BaseModel):
    audit_id: str
    timestamp_utc: datetime
    username: str
    role: str
    action: str
    outcome: str
    resource_type: str | None = None
    resource_id: str | None = None
    department: str | None = None
    source_system: str | None = None
    case_or_reason: str | None = None
    client_ip: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("timestamp_utc")
    def _ser_ts(self, value: datetime) -> str | None:
        return iso_z(value)


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str
    department: str
    unit: str | None = None
    permissions: list[str] = Field(default_factory=list)
    visibility_level: str = "standard"
    #: Surfaced to the UI so it can state the boundary rather than infer it.
    sentinel_video_access: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ---------------------------------------------------------------------------
# Platform health and overview
# ---------------------------------------------------------------------------

class DependencyHealth(BaseModel):
    name: str
    status: str
    detail: str | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    module: str = "Module 1 - metadata-only CCTV registry federation"
    access_model: str = "METADATA_ONLY"
    video_access: str = "NOT_AVAILABLE"
    time_utc: datetime
    dependencies: list[DependencyHealth] = Field(default_factory=list)

    @field_serializer("time_utc")
    def _ser_time(self, value: datetime) -> str | None:
        return iso_z(value)


class DistrictCoverage(BaseModel):
    """One district's coverage stats."""

    district: str
    cameras: int
    online: int
    degraded: int
    offline: int
    unavailable: int
    by_department: dict[str, int] = Field(default_factory=dict)
    is_thin: bool = Field(description="Fewer cameras than the configured minimum")


class AgeingCamera(BaseModel):
    camera_id: str
    name: str
    owning_department: str
    district: str
    installation_date: date | None = None
    age_years: float | None = None
    health_status: CameraStatus


class GapAnalysisResponse(BaseModel):
    """Coverage gaps and ageing infrastructure across the federated registry."""

    generated_at: datetime
    thresholds: dict[str, int] = Field(
        description="min_cameras_per_district and ageing_years used for this report"
    )
    totals: dict[str, int]
    districts_covered: list[DistrictCoverage]
    thin_districts: list[DistrictCoverage] = Field(
        description="Districts with fewer than the configured minimum"
    )
    ageing_cameras: list[AgeingCamera]
    departments_without_coverage: list[str] = Field(default_factory=list)

    @field_serializer("generated_at")
    def _ser_generated(self, value: datetime) -> str | None:
        return iso_z(value)


class OverviewResponse(BaseModel):
    """Numbers behind the operations overview page."""

    total_cameras: int = 0
    approved_cameras: int = 0
    suspended_cameras: int = 0
    decommissioned_cameras: int = 0
    pending_installation_requests: int = 0
    draft_installation_requests: int = 0
    requiring_metadata_review: int = 0
    online: int = 0
    offline: int = 0
    degraded: int = 0
    unavailable: int = 0
    unknown: int = 0
    departments_connected: int = 0
    departments_total: int = 0
    last_metadata_sync_at: datetime | None = None
    video_access: str = "NOT_AVAILABLE"

    @field_serializer("last_metadata_sync_at")
    def _ser_sync(self, value: datetime | None) -> str | None:
        return iso_z(value)
