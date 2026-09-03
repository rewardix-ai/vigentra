"""Adapter for the Municipal VMS department system (Municipal Corporation).

Vendor dialect handled here and nowhere else:
    envelope     {"ok": true, "data": ..., "meta": {...}}
    submission   nested camelCase - identity{} / ownership{} / geo{} / tech{}
    lifecycle    draft / awaiting_approval / approved / synchronized / retired
    time         epoch milliseconds, UTC
    auth         Authorization: Bearer <token>

Compare with traffic_adapter.py: same canonical output, entirely different
translation. That asymmetry is the point of the adapter pattern.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import hashlib

from ..schemas import (
    CameraMetadata,
    Event,
    InstallationForm,
    InstallationFormPatch,
    InstallationRequestOut,
    InstallationStatus,
    RequestStatus,
)
from ..services import normalization as norm
from .base import SurveillanceAdapter, UpstreamProtocolError

#: canonical field -> (vendor group, vendor field)
FORM_FIELD_MAP: dict[str, tuple[str, str]] = {
    "camera_name": ("identity", "label"),
    "external_camera_id": ("identity", "deviceId"),
    "camera_serial_number": ("identity", "serial"),
    "camera_vendor": ("identity", "vendor"),
    "camera_model": ("identity", "model"),
    "camera_type": ("identity", "kind"),
    "installation_purpose": ("identity", "purpose"),
    "owning_department": ("ownership", "department"),
    "owning_unit": ("ownership", "unit"),
    "district": ("ownership", "district"),
    "police_station_or_zone": ("ownership", "zone"),
    "local_admin_contact": ("ownership", "adminContact"),
    "maintenance_agency": ("ownership", "maintenanceAgency"),
    "installation_vendor": ("ownership", "installationVendor"),
    "latitude": ("geo", "latitude"),
    "longitude": ("geo", "longitude"),
    "address_or_landmark": ("geo", "landmark"),
    "road_or_junction": ("geo", "junction"),
    "view_direction": ("geo", "facing"),
    "coverage_description": ("geo", "coverage"),
    "entry_exit_zone_description": ("geo", "entryExit"),
    "source_type": ("tech", "transport"),
    "vms_name": ("tech", "vmsName"),
    "vms_vendor": ("tech", "vmsVendor"),
    "resolution": ("tech", "resolution"),
    "fps": ("tech", "framesPerSecond"),
    "codec": ("tech", "codec"),
    "supports_live": ("tech", "liveSupported"),
    "supports_playback": ("tech", "playbackSupported"),
    "retention_days": ("tech", "retentionDays"),
    "timezone": ("tech", "timezone"),
    "permitted_local_roles": ("policy", "localRoles"),
}

#: Dates travel as epoch milliseconds in this dialect.
DATE_FIELD_MAP = {
    "installation_date": ("tech", "installedOnMs"),
    "commissioning_date": ("tech", "commissionedOnMs"),
}


class MunicipalAdapter(SurveillanceAdapter):
    adapter_name = "municipal_adapter"
    adapter_version = "0.2.0"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.credential}"}

    # -- envelope helpers -------------------------------------------------

    def _data(self, payload: Any, *, expect_list: bool) -> Any:
        if not isinstance(payload, dict) or "data" not in payload:
            raise UpstreamProtocolError(
                "Municipal VMS response is missing the 'data' key",
                source_system=self.source_system,
                detail=payload if isinstance(payload, dict) else str(payload)[:200],
            )
        data = payload["data"]
        if expect_list and not isinstance(data, list):
            raise UpstreamProtocolError(
                "Municipal VMS returned 'data' that is not a list",
                source_system=self.source_system,
                detail=str(data)[:200],
            )
        if not expect_list and not isinstance(data, dict):
            raise UpstreamProtocolError(
                "Municipal VMS returned 'data' that is not an object",
                source_system=self.source_system,
                detail=str(data)[:200],
            )
        return data

    # -- canonical -> vendor ----------------------------------------------

    def _to_vendor_submission(self, values: dict[str, Any]) -> dict[str, Any]:
        submission: dict[str, Any] = {}
        for canonical_field, (group, vendor_field) in FORM_FIELD_MAP.items():
            if canonical_field in values:
                submission.setdefault(group, {})[vendor_field] = values[canonical_field]
        for canonical_field, (group, vendor_field) in DATE_FIELD_MAP.items():
            if canonical_field in values:
                submission.setdefault(group, {})[vendor_field] = norm.date_to_epoch_ms(
                    values[canonical_field]
                )
        return submission

    @staticmethod
    def _to_vendor_attachments(attachments: list[Any]) -> list[dict[str, Any]]:
        out = []
        for item in attachments or []:
            data = item if isinstance(item, dict) else item.model_dump()
            out.append(
                {
                    "type": data.get("document_type"),
                    "reference": data.get("reference"),
                    "filename": data.get("filename"),
                }
            )
        return out

    # -- vendor -> canonical ----------------------------------------------

    def _to_canonical_form(self, submission: dict[str, Any]) -> dict[str, Any]:
        canonical: dict[str, Any] = {}
        for canonical_field, (group, vendor_field) in FORM_FIELD_MAP.items():
            group_values = submission.get(group) or {}
            if vendor_field in group_values:
                canonical[canonical_field] = group_values[vendor_field]
        for canonical_field, (group, vendor_field) in DATE_FIELD_MAP.items():
            parsed = norm.epoch_ms_to_date((submission.get(group) or {}).get(vendor_field))
            canonical[canonical_field] = parsed.isoformat() if parsed else None

        identity = submission.get("identity") or {}
        ownership = submission.get("ownership") or {}
        geo = submission.get("geo") or {}
        canonical["camera_type"] = norm.normalize_camera_type(identity.get("kind")).value
        canonical["installation_purpose"] = norm.normalize_purpose(identity.get("purpose")).value
        canonical["source_type"] = norm.normalize_source_type(
            (submission.get("tech") or {}).get("transport")
        ).value
        canonical["view_direction"] = norm.normalize_direction(geo.get("facing"))
        canonical["permitted_local_roles"] = norm.normalize_local_roles(
            (submission.get("policy") or {}).get("localRoles")
        )
        # Masked by the department before it leaves; never held in full here.
        canonical["camera_serial_number"] = identity.get("serial")
        canonical["local_admin_contact_masked"] = ownership.get("adminContactMasked")
        canonical.pop("local_admin_contact", None)
        return canonical

    def _to_request(self, record: dict[str, Any]) -> InstallationRequestOut:
        submission = record.get("submission", {}) or {}
        identity = submission.get("identity") or {}
        ownership = submission.get("ownership") or {}
        approval = record.get("approval") or {}
        rejection = record.get("rejection") or {}
        status = norm.normalize_request_status(record.get("lifecycle"))

        return InstallationRequestOut(
            request_id=str(record["ref"]),
            source_system=self.source_system,
            owning_department=ownership.get("department") or self.config.department,
            owning_unit=ownership.get("unit"),
            status=status,
            camera_name=identity.get("label"),
            external_camera_id=identity.get("deviceId"),
            district=ownership.get("district") or self.config.default_district,
            created_by=record.get("raisedBy"),
            created_at=norm.epoch_ms_to_utc(record.get("createdAtMs")),
            submitted_by=record.get("submittedBy"),
            submitted_at=norm.epoch_ms_to_utc(record.get("submittedAtMs")),
            approved_by=approval.get("by"),
            approved_by_role=approval.get("role"),
            approved_at=norm.epoch_ms_to_utc(approval.get("atMs")),
            rejected_by=rejection.get("by"),
            rejected_at=norm.epoch_ms_to_utc(rejection.get("atMs")),
            rejection_reason=rejection.get("reason"),
            withdrawal_reason=record.get("suspensionReason") or record.get("decommissionReason"),
            synchronized_at=norm.epoch_ms_to_utc(record.get("syncedAtMs")),
            updated_at=norm.epoch_ms_to_utc(record.get("updatedAtMs")),
            validation_errors=list(record.get("validationIssues") or []),
            form=self._to_canonical_form(submission),
            attachments=[
                {
                    "document_type": item.get("type"),
                    "reference": item.get("reference"),
                    "filename": item.get("filename"),
                    "custodian": self.config.department,
                }
                for item in record.get("attachments", []) or []
            ],
        )

    def _to_camera(self, record: dict[str, Any]) -> CameraMetadata:
        ownership = record.get("ownership") or {}
        geo = record.get("geo") or {}
        tech = record.get("tech") or {}
        policy = record.get("policy") or {}
        approval = record.get("approval") or {}

        status = norm.normalize_request_status(record.get("lifecycle"))
        installation_status = {
            RequestStatus.SUSPENDED: InstallationStatus.SUSPENDED,
            RequestStatus.DECOMMISSIONED: InstallationStatus.DECOMMISSIONED,
        }.get(status, InstallationStatus.COMMISSIONED)

        district = ownership.get("district") or self.config.default_district
        external_id = str(record["id"])
        # This system reports a combined "West Zone / Vasna Ward" string rather
        # than a police-station field; keep it as the zone descriptor.
        _, zone = norm.split_zone(ownership.get("zone"), district)

        return CameraMetadata(
            camera_id=norm.make_camera_id(self.config.department_code, district, external_id),
            external_camera_id=external_id,
            source_system=self.source_system,
            installation_request_id=record.get("requestRef"),
            name=str(record.get("label") or external_id),
            vendor=record.get("vendor"),
            model=record.get("model"),
            camera_type=norm.normalize_camera_type(record.get("kind")),
            installation_purpose=norm.normalize_purpose(record.get("purpose")),
            camera_serial_masked=record.get("serial"),
            owning_department=ownership.get("department") or self.config.department,
            department_code=self.config.department_code,
            owning_unit=ownership.get("unit"),
            police_station_or_zone=zone,
            city=ownership.get("city") or self.config.default_city,
            # This dialect reports "West Zone / Vasna Ward"; the leading segment
            # is the operational zone an account can be scoped to.
            zone=(zone or "").split("/")[0].strip() or None,
            maintenance_agency=ownership.get("maintenanceAgency"),
            installation_vendor=ownership.get("installationVendor"),
            district=district,
            road_or_junction=geo.get("junction"),
            landmark=geo.get("landmark"),
            latitude=geo.get("latitude"),
            longitude=geo.get("longitude"),
            view_direction=norm.normalize_direction(geo.get("facing")),
            coverage_description=geo.get("coverage"),
            entry_exit_zone_description=geo.get("entryExit"),
            source_type=norm.normalize_source_type(tech.get("transport")),
            vms_name=tech.get("vmsName"),
            vms_vendor=tech.get("vmsVendor"),
            resolution=tech.get("resolution"),
            fps=tech.get("framesPerSecond"),
            codec=tech.get("codec"),
            retention_days=tech.get("retentionDays"),
            timezone_name=tech.get("timezone") or "Asia/Kolkata",
            installation_date=norm.epoch_ms_to_date(tech.get("installedOnMs")),
            commissioning_date=norm.epoch_ms_to_date(tech.get("commissionedOnMs")),
            installation_status=installation_status,
            request_status=status,
            approved_by_role=approval.get("role"),
            approved_at=norm.epoch_ms_to_utc(approval.get("atMs")),
            local_video_access=bool(tech.get("liveSupported") or tech.get("playbackSupported")),
            permitted_local_roles=norm.normalize_local_roles(policy.get("localRoles")),
            capabilities=norm.build_capabilities(
                live=bool(tech.get("liveSupported")),
                playback=bool(tech.get("playbackSupported")),
                commissioned=installation_status is InstallationStatus.COMMISSIONED,
            ),
            health_status=norm.normalize_health(record.get("availability")),
            last_frame_utc=norm.epoch_ms_to_utc(record.get("lastFrameEpochMs")),
            reconnect_count=record.get("reconnectCount"),
            source_synced_at=norm.epoch_ms_to_utc(record.get("syncedAtMs")),
            provenance=self.provenance,
        )

    # ----------------------------------------------------------------------
    # Installation register
    # ----------------------------------------------------------------------

    async def list_installation_requests(self) -> list[InstallationRequestOut]:
        payload = await self._request("GET", "/vms/installation-requests")
        return [self._to_request(record) for record in self._data(payload, expect_list=True)]

    async def get_installation_request(self, request_id: str) -> InstallationRequestOut:
        payload = await self._request("GET", f"/vms/installation-requests/{request_id}")
        return self._to_request(self._data(payload, expect_list=False))

    async def create_installation_request(
        self, form: InstallationForm, *, created_by: str
    ) -> InstallationRequestOut:
        values = form.model_dump(mode="python")
        attachments = values.pop("attachments", [])
        body = {
            "raisedBy": created_by,
            "submission": self._to_vendor_submission(values),
            "attachments": self._to_vendor_attachments(attachments),
        }
        payload = await self._request("POST", "/vms/installation-requests", json_body=body)
        return self._to_request(self._data(payload, expect_list=False))

    async def update_installation_request(
        self, request_id: str, patch: InstallationFormPatch, *, updated_by: str
    ) -> InstallationRequestOut:
        values = patch.model_dump(mode="python", exclude_unset=True)
        attachments = values.pop("attachments", None)
        body: dict[str, Any] = {"submission": self._to_vendor_submission(values)}
        if attachments is not None:
            body["attachments"] = self._to_vendor_attachments(attachments)
        payload = await self._request(
            "PATCH", f"/vms/installation-requests/{request_id}", json_body=body
        )
        return self._to_request(self._data(payload, expect_list=False))

    async def submit_installation_request(
        self, request_id: str, *, submitted_by: str
    ) -> InstallationRequestOut:
        payload = await self._request(
            "POST",
            f"/vms/installation-requests/{request_id}/submit",
            json_body={"submittedBy": submitted_by},
        )
        return self._to_request(self._data(payload, expect_list=False))

    async def suspend_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        payload = await self._request(
            "POST",
            f"/vms/installation-requests/{request_id}/suspend",
            json_body={"actor": actor, "reason": reason},
        )
        return self._to_request(self._data(payload, expect_list=False))

    async def decommission_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        payload = await self._request(
            "POST",
            f"/vms/installation-requests/{request_id}/decommission",
            json_body={"actor": actor, "reason": reason},
        )
        return self._to_request(self._data(payload, expect_list=False))

    async def mark_synchronized(self, request_id: str) -> InstallationRequestOut:
        payload = await self._request(
            "POST", f"/vms/installation-requests/{request_id}/mark-synchronized"
        )
        return self._to_request(self._data(payload, expect_list=False))

    # ----------------------------------------------------------------------
    # Camera metadata
    # ----------------------------------------------------------------------

    async def list_approved_cameras(self) -> list[CameraMetadata]:
        payload = await self._request("GET", "/vms/approved-cameras")
        return [self._to_camera(record) for record in self._data(payload, expect_list=True)]

    def _to_event(self, record: dict[str, Any]) -> Event:
        external_id = str(record["ref"])
        vendor_type = record.get("type")
        canonical_type = {
            "motion": "motion_detected",
            "tamper_alarm": "camera_tamper",
            "storage_warning": "storage_warning",
            "device_offline": "device_offline",
        }.get(str(vendor_type or "").lower(), "other")
        digest = hashlib.sha1(f"{self.source_system}:{external_id}".encode()).hexdigest()[:16]
        district = self.config.default_district
        canonical_camera_id = norm.make_camera_id(
            self.config.department_code, district, str(record["cam_ref"])
        )
        severity_word = {1: "info", 2: "warning", 3: "critical"}.get(record.get("level"))
        return Event(
            event_id=f"vigentra_evt_{digest}",
            source_system=self.source_system,
            external_event_id=external_id,
            camera_id=canonical_camera_id,
            event_type=canonical_type,
            severity=severity_word,
            timestamp_utc=norm.epoch_ms_to_utc(record["at_ms"]),  # type: ignore[arg-type]
            payload={
                "event_label": canonical_type,
                "source_event_type": vendor_type,
                "source_severity_level": record.get("level"),
                "attributes": record.get("attributes", {}),
            },
            provenance=self.provenance,
        )

    async def fetch_events(
        self,
        external_camera_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Event]:
        params: dict[str, Any] = {"camera_id": external_camera_id}
        if start:
            params["from"] = int(start.timestamp() * 1000) if start.tzinfo else int(
                start.replace(tzinfo=timezone.utc).timestamp() * 1000
            )
        if end:
            params["to"] = int(end.timestamp() * 1000) if end.tzinfo else int(
                end.replace(tzinfo=timezone.utc).timestamp() * 1000
            )
        payload = await self._request("GET", "/vms/events", params=params)
        return [self._to_event(record) for record in self._data(payload, expect_list=True)]

    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        payload = await self._request("GET", f"/vms/cameras/{external_camera_id}/health")
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        data = self._data(payload, expect_list=False)
        return {
            "status": norm.normalize_health(data.get("availability")),
            "last_heartbeat_utc": datetime.now(timezone.utc),
            "last_frame_utc": norm.epoch_ms_to_utc(data.get("lastFrameEpochMs")),
            "latency_ms": latency_ms,
            "reconnect_count": data.get("reconnectCount"),
            "detail": {
                "vendor_status": data.get("availability"),
                "device_model": data.get("deviceModel"),
                "adapter": self.adapter_name,
            },
        }
