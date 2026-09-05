"""Audit logging.

Every onboarding step, approval decision, metadata synchronisation, record view
and refused video-access attempt is written here. Audit rows are committed
immediately and independently of the request outcome: a denied attempt is
exactly the record you most want to survive, so it must not roll back with the
failed request that produced it.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog

logger = logging.getLogger("vigentra.audit")


class AuditAction(str, Enum):
    # identity
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"

    # installation onboarding
    INSTALLATION_FORM_CREATED = "installation_form_created"
    INSTALLATION_FORM_UPDATED = "installation_form_updated"
    INSTALLATION_FORM_SUBMITTED = "installation_form_submitted"
    INSTALLATION_REQUEST_APPROVED = "installation_request_approved"
    INSTALLATION_REQUEST_REJECTED = "installation_request_rejected"
    INSTALLATION_REQUEST_SUSPENDED = "installation_request_suspended"
    INSTALLATION_REQUEST_DECOMMISSIONED = "installation_request_decommissioned"
    INSTALLATION_REGISTER_VIEWED = "installation_register_viewed"
    INSTALLATION_REQUEST_VIEWED = "installation_request_viewed"

    # registry
    CAMERA_METADATA_SYNCHRONIZED = "camera_metadata_synchronized"
    CAMERA_REGISTRY_VIEWED = "camera_registry_viewed"
    CAMERA_DETAILS_VIEWED = "camera_details_viewed"
    ACCESS_POLICY_VIEWED = "access_policy_viewed"
    CAMERA_HEALTH_VIEWED = "camera_health_viewed"

    # oversight
    AUDIT_VIEWED = "audit_viewed"

    # video
    VIDEO_SESSION_OPENED = "video_session_opened"
    VIDEO_SESSION_REVOKED = "video_session_revoked"
    VIDEO_STREAM_ACCESSED = "video_stream_accessed"
    #: Every refusal, whatever the cause - role, scope, camera policy, expiry.
    VIDEO_ACCESS_DENIED = "video_access_denied"
    VIDEO_ACCESS_REQUESTED = "video_access_requested"
    VIDEO_ACCESS_GRANTED = "video_access_granted"
    VIDEO_ACCESS_REFUSED = "video_access_refused"
    VIDEO_ACCESS_REVOKED = "video_access_revoked"

    # analytics
    DETECTIONS_INGESTED = "detections_ingested"
    DETECTIONS_VIEWED = "detections_viewed"
    #: Registration numbers were disclosed to a reader. Deliberately distinct
    #: from detections_viewed: seeing that a vehicle passed is not the same act
    #: as learning which vehicle it was.
    PLATE_DATA_VIEWED = "plate_data_viewed"
    INCIDENT_REVIEWED = "incident_reviewed"
    INCIDENTS_INGESTED = "incidents_ingested"

    # plate identity - watchlist, alerts, movement
    WATCHLIST_ENTRY_ADDED = "watchlist_entry_added"
    WATCHLIST_ENTRY_DEACTIVATED = "watchlist_entry_deactivated"
    WATCHLIST_VIEWED = "watchlist_viewed"
    #: The matcher fired. Written by the ingest path, not by a human action,
    #: so the trail shows what the system decided as well as what people did.
    WATCHLIST_ALERT_RAISED = "watchlist_alert_raised"
    ALERTS_VIEWED = "alerts_viewed"
    ALERT_ACKNOWLEDGED = "alert_acknowledged"
    ALERT_DISMISSED = "alert_dismissed"
    #: Someone asked where a registration number has been. The single most
    #: revealing query this platform answers, and audited as its own act.
    VEHICLE_MOVEMENT_VIEWED = "vehicle_movement_viewed"
    PLATE_SEARCHED = "plate_searched"

    # vehicle reference registry
    VEHICLE_REGISTRY_IMPORTED = "vehicle_registry_imported"
    VEHICLE_REGISTRY_SEARCHED = "vehicle_registry_searched"
    VEHICLE_RECORD_VIEWED = "vehicle_record_viewed"


class ResourceType(str, Enum):
    INSTALLATION_REQUEST = "installation_request"
    CAMERA = "camera"
    ACCESS_POLICY = "access_policy"
    SOURCE_SYSTEM = "source_system"
    AUDIT_LOG = "audit_log"
    SESSION = "session"
    VIDEO = "video"
    DETECTION = "detection"
    VEHICLE = "vehicle"
    VIDEO_ACCESS_GRANT = "video_access_grant"
    WATCHLIST_ENTRY = "watchlist_entry"
    WATCHLIST_ALERT = "watchlist_alert"
    PLATE_SIGHTING = "plate_sighting"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"
    PARTIAL = "partial"


def new_audit_id() -> str:
    return f"audit_{uuid.uuid4().hex[:20]}"


async def record(
    db: AsyncSession,
    *,
    username: str,
    action: AuditAction | str,
    role: str = "unknown",
    outcome: AuditOutcome | str = AuditOutcome.SUCCESS,
    resource_type: ResourceType | str | None = None,
    resource_id: str | None = None,
    department: str | None = None,
    source_system: str | None = None,
    case_or_reason: str | None = None,
    client_ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Append one audit row and commit it."""

    def _value(item: Any) -> Any:
        return item.value if isinstance(item, Enum) else item

    entry = AuditLog(
        audit_id=new_audit_id(),
        timestamp_utc=datetime.now(timezone.utc),
        username=username,
        role=role,
        action=_value(action),
        outcome=_value(outcome),
        resource_type=_value(resource_type),
        resource_id=resource_id,
        department=department,
        source_system=source_system,
        case_or_reason=(case_or_reason or None),
        client_ip=client_ip,
        details=details or {},
    )
    db.add(entry)
    await db.commit()

    logger.info(
        "audit action=%s outcome=%s user=%s resource=%s/%s dept=%s",
        entry.action, entry.outcome, entry.username,
        entry.resource_type, entry.resource_id, entry.department,
    )
    return entry
