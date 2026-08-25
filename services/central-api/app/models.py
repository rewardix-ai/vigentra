"""SQLAlchemy models for the central metadata registry.

Ten tables, matching shared/canonical-schemas.md:

    users                            demo accounts, seeded from configuration
    roles                            role catalogue and its permission set
    sources                          one row per federated department system
    installation_requests            central MIRROR of each department's register
    installation_request_attachments document references (never the documents)
    camera_access_policies           local vs Sentinel access, per camera
    cameras                          the canonical metadata registry
    camera_health                    time-series health samples
    metadata_sync_logs               one row per source per synchronisation run
    audit_logs                       who did what, to which record

There is no `video_sessions` table. Module 1 has no video path, so it has
nowhere to record one.

JSON columns use JSONB on PostgreSQL and fall back to plain JSON on SQLite so
the test-suite can run without a database container.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

JSONColumn = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

class Role(Base, TimestampMixin):
    """Role catalogue. Seeded from configuration on startup."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    permissions: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    metadata_visibility_level: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    #: Recorded per role to make the boundary explicit in the data, not just in code.
    grants_video_access: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class User(Base, TimestampMixin):
    """A demo account. Passwords are NOT stored here - see config.DemoUser."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(120), nullable=False, default="*")
    unit: Mapped[str | None] = mapped_column(String(160), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------

class Source(Base, TimestampMixin):
    """A federated department system and the outcome of its last sync."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_system_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.2.0")
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Internal upstream URL. Only ever exposed redacted to host:port.
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    camera_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# Installation register (central mirror of each department's own register)
# ---------------------------------------------------------------------------

class InstallationRequest(Base, TimestampMixin):
    """Central mirror of one department installation record.

    The authoritative copy lives in the owning department's system. This mirror
    lets Sentinel list and audit the pipeline, and keeps the last known state
    visible when a department system is unreachable.
    """

    __tablename__ = "installation_requests"
    __table_args__ = (
        UniqueConstraint("source_system", "external_camera_id", "request_id", name="uq_install_source_ref"),
        Index("ix_install_status_dept", "status", "owning_department"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    owning_department: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    owning_unit: Mapped[str | None] = mapped_column(String(160), nullable=True)
    district: Mapped[str | None] = mapped_column(String(120), nullable=True)
    camera_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    external_camera_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    synchronized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    validation_errors: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    #: The canonicalised installation form. Contains no credential and no
    #: stream URL - the adapters strip those before they ever reach here.
    installation_form_json: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    #: When Sentinel last read this record from its owning system.
    mirrored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InstallationRequestAttachment(Base):
    """A REFERENCE to a department-held document. Never the document itself."""

    __tablename__ = "installation_request_attachments"
    __table_args__ = (
        UniqueConstraint("request_id", "reference", name="uq_attachment_request_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("installation_requests.request_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    custodian: Mapped[str] = mapped_column(String(120), nullable=False, default="owning_department")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Camera registry
# ---------------------------------------------------------------------------

class Camera(Base, TimestampMixin):
    """One canonical camera METADATA record.

    Note what has no column here: stream URL, RTSP address, NVR host, VMS
    credential, media token. There is nowhere to put them, by design.
    """

    __tablename__ = "cameras"
    __table_args__ = (
        UniqueConstraint("source_system", "external_camera_id", name="uq_cameras_source_external"),
        Index("ix_cameras_source_status", "source_system", "health_status"),
        Index("ix_cameras_dept_district", "owning_department", "district"),
        # City-wise and department-wise browsing are the two dominant query
        # patterns, so they get dedicated composite indexes rather than relying
        # on the single-column ones.
        Index("ix_cameras_city_dept", "city_normalized", "department_code"),
        Index("ix_cameras_city_status", "city_normalized", "installation_status"),
        Index("ix_cameras_city_zone", "city_normalized", "zone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    external_camera_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    installation_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # identity
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    camera_type: Mapped[str] = mapped_column(String(32), nullable=False, default="fixed")
    installation_purpose: Mapped[str | None] = mapped_column(String(64), nullable=True)
    camera_serial_masked: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # ownership
    owning_department: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    #: Stable machine code for the department (TRAFFIC / MUNICIPAL). Filtering
    #: on this rather than the display name survives a department being renamed.
    department_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", index=True)
    owning_unit: Mapped[str | None] = mapped_column(String(160), nullable=True)
    police_station_or_zone: Mapped[str | None] = mapped_column(String(160), nullable=True)
    maintenance_agency: Mapped[str | None] = mapped_column(String(160), nullable=True)
    installation_vendor: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # location
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="", index=True)
    #: Lower-cased, whitespace-collapsed city for case-insensitive filtering.
    city_normalized: Mapped[str] = mapped_column(String(120), nullable=False, default="", index=True)
    zone: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    district: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    road_or_junction: Mapped[str | None] = mapped_column(String(160), nullable=True)
    landmark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    view_direction: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    coverage_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_exit_zone_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # technical
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vms_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vms_vendor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")

    # installation and approval
    installation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    commissioning_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    installation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="COMMISSIONED")
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    approved_by_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # health snapshot (the time series lives in camera_health)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_heartbeat_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_frame_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Whether the OWNING DEPARTMENT permits Sentinel to broker video for this
    #: camera at all. False here is final - no role can override it.
    video_access_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Canonical capability list, e.g. ["metadata", "health", "live", "playback"].
    capabilities: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    # synchronisation
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    last_metadata_sync_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)


class CameraAccessPolicy(Base, TimestampMixin):
    """Who may see the FOOTAGE locally, and who may see the RECORD centrally."""

    __tablename__ = "camera_access_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("cameras.camera_id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    owning_department: Mapped[str] = mapped_column(String(120), nullable=False)
    local_video_access_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Module 1 invariant. `services/policy_service.py` refuses to write True,
    #: and no API path accepts a value for it at all.
    sentinel_video_access_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permitted_local_roles_json: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    metadata_visibility_level: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CameraHealth(Base):
    """A health sample for one camera at one instant."""

    __tablename__ = "camera_health"
    __table_args__ = (Index("ix_camera_health_camera_checked", "camera_id", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("cameras.camera_id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_heartbeat_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_frame_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    reconnect_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Event(Base):
    """A normalised generic event.

    Module 1 carries device-health and motion-style events only. There are no
    ANPR, vehicle, face or identity fields anywhere in this table — the
    ``payload`` JSON column is where Phase 2 detection modules will land.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source_system", "external_event_id", name="uq_events_source_external"),
        Index("ix_events_camera_time", "camera_id", "timestamp_utc"),
        Index("ix_events_source_type_time", "source_system", "event_type", "timestamp_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class MetadataSyncLog(Base):
    """One row per department system per synchronisation run."""

    __tablename__ = "metadata_sync_logs"
    __table_args__ = (Index("ix_sync_log_source_started", "source_system", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sync_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    triggered_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    approved_records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synchronized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_unregistered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    withdrawn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoSession(Base):
    """A short-lived, audited grant to view one camera.

    `source_session_reference` holds whatever handle the owning department
    handed back (a ticketed URL, an HLS manifest path, a vendor session id).
    It is INTERNAL: it is never serialised into any response model, and the
    browser only ever receives the opaque /api/v1/streams/{session_id} path.

    There is deliberately no column for a credential or a permanent URL.
    """

    __tablename__ = "video_sessions"
    __table_args__ = (
        Index("ix_video_sessions_user_created", "user_id", "created_at_utc"),
        Index("ix_video_sessions_camera_created", "camera_id", "created_at_utc"),
        Index("ix_video_sessions_status_expiry", "status", "expires_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    department: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)

    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="live")
    start_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: active | expired | revoked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)

    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Internal upstream handle. Never returned to a client.
    source_session_reference: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_protocol: Mapped[str] = mapped_column(String(32), nullable=False, default="http-mp4")

    watermark_text: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    audit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VideoAccessGrant(Base, TimestampMixin):
    """A request to the OWNING UNIT for permission to view a camera.

    Metadata federates automatically; footage does not. To watch a camera
    outside your own department you ask the unit that owns it, and someone
    there decides. This table is that conversation.

    Within your own department the existing scope rules still apply and no
    grant is needed - the unit already owns the camera.
    """

    __tablename__ = "video_access_grants"
    __table_args__ = (
        Index("ix_grant_user_camera", "requested_by", "camera_id"),
        Index("ix_grant_status_camera", "status", "camera_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grant_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    owning_department: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requester_department: Mapped[str] = mapped_column(String(120), nullable=False)
    requester_role: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Mandatory. A viewing request nobody can explain later is the thing the
    #: audit trail exists to prevent.
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    #: requested | granted | denied | revoked | expired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    #: Grants are time-boxed. An open-ended grant is a standing permission
    #: nobody revisits.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Which modes the owning unit allowed, e.g. ["live"] or ["live","playback"].
    allowed_modes: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)


class ModelVersion(Base):
    """A detector build that has produced at least one detection."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_name", "model_version", name="uq_model_name_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    task: Mapped[str] = mapped_column(String(40), nullable=False, default="object_detection")
    classes: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)
    device: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    detection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Detection(Base):
    """One generic object detection.

    Generic classes: person, car, motorcycle, bus, truck, auto-rickshaw,
    bicycle. Optionally a registration number, when the owning deployment has
    turned ANPR on at the edge.

    Still absent, deliberately: face embeddings, vehicle identity, and any
    cross-camera association. A plate here is an observation at one camera at
    one instant - it is not joined to the vehicle registry, and nothing in this
    table tracks a vehicle between cameras.

    Detections are probabilistic. `confidence` is the model's own score and is
    never treated as certainty anywhere in this system.
    """

    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_camera_time", "camera_id", "timestamp_utc"),
        Index("ix_detections_class_time", "class_name", "timestamp_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detection_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    class_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_json: Mapped[list] = mapped_column(JSONColumn, nullable=False, default=list)

    # -- ANPR ---------------------------------------------------------------
    # Personal data, and treated as such: read behind `plate:read`, retained on
    # its own shorter clock, and never populated unless the edge worker was
    # explicitly run with ANPR enabled.
    plate_text: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    plate_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    plate_bbox_json: Mapped[list | None] = mapped_column(JSONColumn, nullable=True)
    plate_reader: Mapped[str | None] = mapped_column(String(80), nullable=True)
    plate_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    #: authorized_edge | demo_local | mock
    source_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="mock")
    inference_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_quality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: A pointer to evidence held by the owning department - never the pixels.
    evidence_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    provenance: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class FrameQualityEvent(Base):
    """A frame the quality router flagged as other than normal."""

    __tablename__ = "frame_quality_events"
    __table_args__ = (Index("ix_frame_quality_camera_time", "camera_id", "timestamp_utc"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: normal | low_light | overexposed | blurred
    quality: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mean_luma: Mapped[float | None] = mapped_column(Float, nullable=True)
    laplacian_variance: Mapped[float | None] = mapped_column(Float, nullable=True)
    clipped_highlight_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    enhancement_applied: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: True when the router judged inference unreliable and skipped it.
    inference_skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Vehicle(Base, TimestampMixin):
    """A vehicle registration REFERENCE record.

    Scope, deliberately narrow: this is a lookup table of vehicle attributes,
    the same category of thing as a make/model catalogue. It is NOT joined to
    cameras, detections or video sessions anywhere in this codebase.

    That separation is the whole point. Vehicle attributes on their own are
    low-risk reference data. The risk lives in the JOIN - linking "camera X saw
    this plate at 14:32" to a registration is what turns a camera registry into
    a person-tracking system. Making that join needs ANPR, which this phase does
    not implement, and it would need its own legal basis besides.

    There is no owner column here, and there must never be one. The source file
    ships without owner name, address, phone, chassis or engine number, and
    `vehicle_service.py` refuses to import a record carrying any of them.
    """

    __tablename__ = "vehicles"
    __table_args__ = (
        Index("ix_vehicles_make_model", "make", "model"),
        Index("ix_vehicles_class_status", "vehicle_class", "registration_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: Normalised (upper-case, no spaces) - the natural key for lookup.
    registration_number: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )

    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    registration_valid_upto: Mapped[date | None] = mapped_column(Date, nullable=True)
    vehicle_category_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    vehicle_class: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    make: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    body_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    colour: Mapped[str | None] = mapped_column(String(64), nullable=True)
    registration_status: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    #: RTO / place of registration, as published. A place, not an address.
    registered_at: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status_as_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    source: Mapped[str] = mapped_column(String(80), nullable=False, default="reference_import")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    is_demo_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AuditLog(Base):
    """Append-only record of who did what to which record."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_ts_action", "timestamp_utc", "action"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_or_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
