"""Database row -> API schema conversion, including role-based redaction.

Kept separate from services/normalization.py, which handles the different
problem of vendor dialect -> canonical schema.
"""
from __future__ import annotations

from typing import Any

from .config import DemoUser, Settings
from .models import AuditLog, Camera as CameraRow, CameraAccessPolicy, CameraHealth
from .models import InstallationRequestAttachment, Source as SourceRow
from .schemas import (
    AccessPolicyOut,
    VideoAccessState,
    AccessPolicySummary,
    ApprovalSummary,
    AuditOut,
    Camera,
    CameraDetail,
    CameraHealthOut,
    CameraHealthSummary,
    CameraLocation,
    InstallationSummary,
    SentinelSyncSummary,
    SourceOut,
    TechnicalSummary,
)
from .services import video_permissions
from .services.normalization import redact_endpoint
from .services.policy_service import (
    describe_metadata_access,
    effective_visibility,
    redacted_fields_for,
)


def _policy_summary(row: CameraRow, policy: CameraAccessPolicy | None) -> AccessPolicySummary:
    return AccessPolicySummary(
        local_video_access=bool(policy.local_video_access_enabled) if policy else False,
        # The owner's decision. A precondition for brokering, never a grant on
        # its own - the caller still has to pass every scope check.
        sentinel_video_access=bool(row.video_access_enabled),
        permitted_local_roles=list(policy.permitted_local_roles_json or []) if policy else [],
        footage_custodian=row.owning_department,
        metadata_visibility_level=policy.metadata_visibility_level if policy else "standard",
        policy_version=policy.policy_version if policy else 1,
    )


def camera_to_schema(
    row: CameraRow,
    policy: CameraAccessPolicy | None = None,
    *,
    user: DemoUser | None = None,
    settings: Settings | None = None,
    granted_modes: list[str] | None = None,
) -> Camera:
    """Row -> canonical camera.

    `video_access` is computed per request, not stored: the same camera reads
    `denied` for one account and `live_and_playback` for another. Passing no
    user yields `denied`, which is the safe default for any caller that has not
    established who is asking.
    """
    if user is not None and settings is not None:
        decision = video_permissions.evaluate(user, row, settings, grant=granted_modes)
        video_state, video_reason = decision.state, decision.reason
    else:
        video_state, video_reason = VideoAccessState.DENIED, "No authenticated context."

    return Camera(
        camera_id=row.camera_id,
        external_camera_id=row.external_camera_id,
        source_system=row.source_system,
        owning_department=row.owning_department,
        owning_unit=row.owning_unit,
        name=row.name,
        vendor=row.vendor,
        model=row.model,
        camera_type=row.camera_type,
        installation_purpose=row.installation_purpose,
        department_code=row.department_code or "",
        capabilities=list(row.capabilities or []),
        video_access=video_state,
        video_access_reason=video_reason,
        location=CameraLocation(
            city=row.city or "",
            zone=row.zone,
            district=row.district,
            road_or_junction=row.road_or_junction,
            landmark=row.landmark,
            latitude=row.latitude,
            longitude=row.longitude,
            view_direction=row.view_direction,
        ),
        source_type=row.source_type,
        vms_name=row.vms_name,
        technical_summary=TechnicalSummary(
            resolution=row.resolution,
            fps=row.fps,
            codec=row.codec,
            timezone=row.timezone_name,
            source_type=row.source_type,
            retention_days=row.retention_days,
        ),
        installation=InstallationSummary(
            installation_date=row.installation_date,
            commissioning_date=row.commissioning_date,
            installation_status=row.installation_status,
            installation_request_id=row.installation_request_id,
        ),
        approval=ApprovalSummary(
            status=row.approval_status,
            approved_by_role=row.approved_by_role,
            approved_at=row.approved_at,
        ),
        access_policy_summary=_policy_summary(row, policy),
        health=CameraHealthSummary(
            status=row.health_status,
            last_heartbeat_utc=row.last_heartbeat_utc,
            last_frame_utc=row.last_frame_utc,
            last_metadata_sync_utc=row.last_metadata_sync_utc,
        ),
        sentinel_sync=SentinelSyncSummary(
            status=row.sync_status,
            synced_at_utc=row.last_metadata_sync_utc,
            source_request_id=row.installation_request_id,
        ),
    )


def camera_to_detail(
    row: CameraRow,
    user: DemoUser,
    *,
    settings: Settings | None = None,
    granted_modes: list[str] | None = None,
    policy: CameraAccessPolicy | None = None,
    health: CameraHealth | None = None,
    attachments: list[InstallationRequestAttachment] | None = None,
) -> CameraDetail:
    """Camera detail, with fields this role may not see removed.

    The redacted field names are returned alongside the record so the UI can say
    "withheld for your role" rather than silently showing a blank.
    """
    base = camera_to_schema(
        row, policy, user=user, settings=settings, granted_modes=granted_modes
    ).model_dump()
    withheld = redacted_fields_for(user, row)

    detail = CameraDetail(
        **base,
        camera_serial_masked=row.camera_serial_masked,
        police_station_or_zone=row.police_station_or_zone,
        maintenance_agency=row.maintenance_agency,
        installation_vendor=row.installation_vendor,
        coverage_description=row.coverage_description,
        entry_exit_zone_description=row.entry_exit_zone_description,
        address_or_landmark=row.landmark,
        attachments=[
            {
                "document_type": item.document_type,
                "reference": item.reference,
                "filename": item.filename,
                "custodian": item.custodian,
            }
            for item in (attachments or [])
        ],
        provenance=dict(row.provenance or {}),
        first_synced_at=row.first_synced_at,
        last_synced_at=row.last_metadata_sync_utc,
        visibility_level=effective_visibility(user, row),
        redacted_fields=list(withheld),
    )

    if health is not None:
        detail.health = CameraHealthSummary(
            status=health.status,
            last_heartbeat_utc=health.last_heartbeat_utc,
            last_frame_utc=health.last_frame_utc,
            last_metadata_sync_utc=row.last_metadata_sync_utc,
            latency_ms=health.latency_ms,
            reconnect_count=health.reconnect_count,
        )

    for field in withheld:
        if field == "attachments":
            detail.attachments = []
        else:
            setattr(detail, field, None)
    return detail


def health_to_schema(row: CameraHealth, camera: CameraRow) -> CameraHealthOut:
    return CameraHealthOut(
        camera_id=row.camera_id,
        source_system=row.source_system,
        owning_department=camera.owning_department,
        status=row.status,
        last_heartbeat_utc=row.last_heartbeat_utc,
        last_frame_utc=row.last_frame_utc,
        last_metadata_sync_utc=camera.last_metadata_sync_utc,
        latency_ms=row.latency_ms,
        reconnect_count=row.reconnect_count,
        detail=row.detail or {},
        checked_at=row.checked_at,
    )


def access_policy_to_schema(
    row: CameraRow,
    policy: CameraAccessPolicy | None,
    user: DemoUser,
    settings: Settings | None = None,
    granted_modes: list[str] | None = None,
) -> AccessPolicyOut:
    if settings is not None:
        decision = video_permissions.evaluate(user, row, settings, grant=granted_modes)
        caller_state, caller_reason = decision.state.value, decision.reason
    else:
        caller_state, caller_reason = "denied", "No authenticated context."

    return AccessPolicyOut(
        caller_video_access=caller_state,
        caller_video_access_reason=caller_reason,
        camera_id=row.camera_id,
        camera_name=row.name,
        owning_department=row.owning_department,
        source_system=row.source_system,
        sentinel_metadata_access=describe_metadata_access(user),
        sentinel_video_access=bool(row.video_access_enabled),
        local_video_access_enabled=bool(policy.local_video_access_enabled) if policy else False,
        permitted_local_roles=list(policy.permitted_local_roles_json or []) if policy else [],
        footage_custodian=row.owning_department,
        local_vms_name=row.vms_name,
        policy_version=policy.policy_version if policy else 1,
        approved_by_role=row.approved_by_role,
        approved_at=row.approved_at,
    )


def source_to_schema(row: SourceRow) -> SourceOut:
    return SourceOut(
        source_system=row.source_system_id,
        display_name=row.display_name,
        department=row.department,
        adapter=row.adapter,
        adapter_version=row.adapter_version,
        status=row.status,
        camera_count=row.camera_count,
        pending_requests=row.pending_requests,
        # Host and port only. The credentialed URL never leaves this service.
        endpoint=redact_endpoint(row.base_url),
        last_sync_at=row.last_sync_at,
        last_success_at=row.last_success_at,
        last_error=row.last_error,
        latency_ms=row.latency_ms,
    )


def audit_to_schema(row: AuditLog) -> AuditOut:
    return AuditOut(
        audit_id=row.audit_id,
        timestamp_utc=row.timestamp_utc,
        username=row.username,
        role=row.role,
        action=row.action,
        outcome=row.outcome,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        department=row.department,
        source_system=row.source_system,
        case_or_reason=row.case_or_reason,
        client_ip=row.client_ip,
        details=row.details or {},
    )
