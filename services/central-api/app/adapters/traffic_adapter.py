"""Adapter for the Traffic VMS department system (Gujarat Traffic Police).

Vendor dialect handled here and nowhere else:
    envelope     {"status": "OK", "records": [...]} / {"record": {...}}
    form fields  flat snake_case - cam_code / cam_name / lat / lng / feed_type
    lifecycle    DRAFT / SUBMITTED / REGISTERED / SYNCED / SUSPENDED / ...
    time         "dd-mm-YYYY HH:MM:SS" wall-clock in Asia/Kolkata
    auth         X-API-Key header

Translation runs in both directions: canonical form -> vendor form when an
installer submits, vendor record -> canonical when Sentinel reads.
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

#: canonical field -> this vendor's field
FORM_FIELD_MAP: dict[str, str] = {
    "camera_name": "cam_name",
    "external_camera_id": "cam_code",
    "camera_serial_number": "serial_no",
    "camera_vendor": "make",
    "camera_model": "model_no",
    "camera_type": "cam_kind",
    "installation_purpose": "purpose",
    "owning_department": "dept",
    "owning_unit": "unit",
    "district": "district",
    "police_station_or_zone": "ps_zone",
    "local_admin_contact": "admin_contact",
    "maintenance_agency": "maint_agency",
    "installation_vendor": "install_vendor",
    "latitude": "lat",
    "longitude": "lng",
    "address_or_landmark": "address",
    "road_or_junction": "road",
    "view_direction": "direction",
    "coverage_description": "coverage",
    "entry_exit_zone_description": "entry_exit",
    "source_type": "feed_type",
    "vms_name": "vms",
    "vms_vendor": "vms_make",
    "resolution": "res",
    "fps": "fps",
    "codec": "codec",
    "supports_live": "live_ok",
    "supports_playback": "playback_ok",
    "retention_days": "retention",
    "timezone": "tz",
    "permitted_local_roles": "roles_allowed",
}

#: Dates travel as dd-mm-YYYY in this dialect.
DATE_FIELD_MAP = {"installation_date": "installed_on", "commissioning_date": "commissioned_on"}


class TrafficAdapter(SurveillanceAdapter):
    adapter_name = "traffic_adapter"
    adapter_version = "0.2.0"

    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.config.credential}

    # -- envelope helpers -------------------------------------------------

    def _records(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise UpstreamProtocolError(
                "Traffic VMS response is missing the 'records' list",
                source_system=self.source_system,
                detail=payload if isinstance(payload, dict) else str(payload)[:200],
            )
        return payload["records"]

    def _record(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict):
            raise UpstreamProtocolError(
                "Traffic VMS response is missing the 'record' object",
                source_system=self.source_system,
                detail=str(payload)[:200],
            )
        return payload["record"]

    # -- canonical -> vendor ----------------------------------------------

    def _to_vendor_form(self, values: dict[str, Any]) -> dict[str, Any]:
        """Translate a canonical form (or partial form) into this dialect."""
        vendor: dict[str, Any] = {}
        for canonical_field, vendor_field in FORM_FIELD_MAP.items():
            if canonical_field in values:
                vendor[vendor_field] = values[canonical_field]
        for canonical_field, vendor_field in DATE_FIELD_MAP.items():
            if canonical_field in values:
                vendor[vendor_field] = norm.date_to_ist_string(values[canonical_field])
        return vendor

    @staticmethod
    def _to_vendor_docs(attachments: list[Any]) -> list[dict[str, Any]]:
        out = []
        for item in attachments or []:
            data = item if isinstance(item, dict) else item.model_dump()
            out.append(
                {
                    "kind": data.get("document_type"),
                    "ref": data.get("reference"),
                    "filename": data.get("filename"),
                }
            )
        return out

    # -- vendor -> canonical ----------------------------------------------

    def _to_canonical_form(self, vendor_form: dict[str, Any]) -> dict[str, Any]:
        canonical: dict[str, Any] = {}
        for canonical_field, vendor_field in FORM_FIELD_MAP.items():
            if vendor_field in vendor_form:
                canonical[canonical_field] = vendor_form[vendor_field]
        for canonical_field, vendor_field in DATE_FIELD_MAP.items():
            parsed = norm.ist_date(vendor_form.get(vendor_field))
            canonical[canonical_field] = parsed.isoformat() if parsed else None

        canonical["camera_type"] = norm.normalize_camera_type(vendor_form.get("cam_kind")).value
        canonical["installation_purpose"] = norm.normalize_purpose(vendor_form.get("purpose")).value
        canonical["source_type"] = norm.normalize_source_type(vendor_form.get("feed_type")).value
        canonical["view_direction"] = norm.normalize_direction(vendor_form.get("direction"))
        canonical["permitted_local_roles"] = norm.normalize_local_roles(
            vendor_form.get("roles_allowed")
        )
        # The department masks these before they leave its system; Sentinel
        # never holds the full values.
        canonical["camera_serial_number"] = vendor_form.get("serial_no")
        canonical["local_admin_contact_masked"] = vendor_form.get("admin_contact_masked")
        canonical.pop("local_admin_contact", None)
        return canonical

    def _to_request(self, record: dict[str, Any]) -> InstallationRequestOut:
        form = record.get("form", {}) or {}
        status = norm.normalize_request_status(record.get("state"))
        return InstallationRequestOut(
            request_id=str(record["request_ref"]),
            source_system=self.source_system,
            owning_department=form.get("dept") or self.config.department,
            owning_unit=form.get("unit"),
            status=status,
            camera_name=form.get("cam_name"),
            external_camera_id=form.get("cam_code"),
            district=form.get("district") or self.config.default_district,
            created_by=record.get("created_by"),
            created_at=norm.ist_wallclock_to_utc(record.get("created_on")),
            submitted_by=record.get("submitted_by"),
            submitted_at=norm.ist_wallclock_to_utc(record.get("submitted_on")),
            approved_by=record.get("approved_by"),
            approved_by_role=record.get("approved_by_role"),
            approved_at=norm.ist_wallclock_to_utc(record.get("approved_on")),
            rejected_by=record.get("rejected_by"),
            rejected_at=norm.ist_wallclock_to_utc(record.get("rejected_on")),
            rejection_reason=record.get("rejection_reason"),
            withdrawal_reason=record.get("suspension_reason") or record.get("decommission_reason"),
            synchronized_at=norm.ist_wallclock_to_utc(record.get("synced_on")),
            updated_at=norm.ist_wallclock_to_utc(record.get("updated_on")),
            validation_errors=list(record.get("validation_errors") or []),
            form=self._to_canonical_form(form),
            attachments=[
                {
                    "document_type": doc.get("kind"),
                    "reference": doc.get("ref"),
                    "filename": doc.get("filename"),
                    "custodian": self.config.department,
                }
                for doc in record.get("docs", []) or []
            ],
        )

    def _to_camera(self, record: dict[str, Any]) -> CameraMetadata:
        status = norm.normalize_request_status(record.get("state"))
        installation_status = {
            RequestStatus.SUSPENDED: InstallationStatus.SUSPENDED,
            RequestStatus.DECOMMISSIONED: InstallationStatus.DECOMMISSIONED,
        }.get(status, InstallationStatus.COMMISSIONED)

        district = record.get("district") or self.config.default_district
        external_id = str(record["cam_code"])

        return CameraMetadata(
            camera_id=norm.make_camera_id(self.config.department_code, district, external_id),
            external_camera_id=external_id,
            source_system=self.source_system,
            installation_request_id=record.get("request_ref"),
            name=str(record.get("cam_name") or external_id),
            vendor=record.get("make"),
            model=record.get("model_no"),
            camera_type=norm.normalize_camera_type(record.get("cam_kind")),
            installation_purpose=norm.normalize_purpose(record.get("purpose")),
            camera_serial_masked=record.get("serial_no"),
            # This department does report its own name; registration is the fallback.
            owning_department=record.get("dept") or self.config.department,
            department_code=self.config.department_code,
            owning_unit=record.get("unit"),
            police_station_or_zone=record.get("ps_zone"),
            # The Traffic dialect has no city field; the value recorded when the
            # source was registered is the fallback.
            city=record.get("city") or self.config.default_city,
            zone=record.get("zone") or record.get("unit"),
            maintenance_agency=record.get("maint_agency"),
            installation_vendor=record.get("install_vendor"),
            district=district,
            road_or_junction=record.get("road"),
            landmark=record.get("address"),
            latitude=record.get("lat"),
            longitude=record.get("lng"),
            view_direction=norm.normalize_direction(record.get("direction")),
            coverage_description=record.get("coverage"),
            entry_exit_zone_description=record.get("entry_exit"),
            source_type=norm.normalize_source_type(record.get("feed_type")),
            vms_name=record.get("vms"),
            vms_vendor=record.get("vms_make"),
            resolution=record.get("res"),
            fps=record.get("fps"),
            codec=record.get("codec"),
            retention_days=record.get("retention"),
            timezone_name=record.get("tz") or "Asia/Kolkata",
            installation_date=norm.ist_date(record.get("installed_on")),
            commissioning_date=norm.ist_date(record.get("commissioned_on")),
            installation_status=installation_status,
            request_status=status,
            approved_by_role=record.get("approved_by_role"),
            approved_at=norm.ist_wallclock_to_utc(record.get("approved_on")),
            # Local viewing capability, recorded for the policy summary only.
            local_video_access=bool(record.get("live_ok") or record.get("playback_ok")),
            permitted_local_roles=norm.normalize_local_roles(record.get("roles_allowed")),
            capabilities=norm.build_capabilities(
                live=bool(record.get("live_ok")),
                playback=bool(record.get("playback_ok")),
                commissioned=installation_status is InstallationStatus.COMMISSIONED,
            ),
            health_status=norm.normalize_health(record.get("status")),
            last_frame_utc=norm.ist_wallclock_to_utc(record.get("last_frame_at")),
            reconnect_count=record.get("reconnects"),
            source_synced_at=norm.ist_wallclock_to_utc(record.get("synced_on")),
            provenance=self.provenance,
        )

    # ----------------------------------------------------------------------
    # Installation register
    # ----------------------------------------------------------------------

    async def list_installation_requests(self) -> list[InstallationRequestOut]:
        payload = await self._request("GET", "/traffic/installation-requests")
        return [self._to_request(record) for record in self._records(payload)]

    async def get_installation_request(self, request_id: str) -> InstallationRequestOut:
        payload = await self._request("GET", f"/traffic/installation-requests/{request_id}")
        return self._to_request(self._record(payload))

    async def create_installation_request(
        self, form: InstallationForm, *, created_by: str
    ) -> InstallationRequestOut:
        values = form.model_dump(mode="python")
        attachments = values.pop("attachments", [])
        body = {
            "created_by": created_by,
            "form": self._to_vendor_form(values),
            "docs": self._to_vendor_docs(attachments),
        }
        payload = await self._request("POST", "/traffic/installation-requests", json_body=body)
        return self._to_request(self._record(payload))

    async def update_installation_request(
        self, request_id: str, patch: InstallationFormPatch, *, updated_by: str
    ) -> InstallationRequestOut:
        values = patch.model_dump(mode="python", exclude_unset=True)
        attachments = values.pop("attachments", None)
        body: dict[str, Any] = {"form": self._to_vendor_form(values)}
        if attachments is not None:
            body["docs"] = self._to_vendor_docs(attachments)
        payload = await self._request(
            "PATCH", f"/traffic/installation-requests/{request_id}", json_body=body
        )
        return self._to_request(self._record(payload))

    async def submit_installation_request(
        self, request_id: str, *, submitted_by: str
    ) -> InstallationRequestOut:
        payload = await self._request(
            "POST",
            f"/traffic/installation-requests/{request_id}/submit",
            json_body={"submitted_by": submitted_by},
        )
        return self._to_request(self._record(payload))

    async def suspend_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        payload = await self._request(
            "POST",
            f"/traffic/installation-requests/{request_id}/suspend",
            json_body={"actor": actor, "reason": reason},
        )
        return self._to_request(self._record(payload))

    async def decommission_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        payload = await self._request(
            "POST",
            f"/traffic/installation-requests/{request_id}/decommission",
            json_body={"actor": actor, "reason": reason},
        )
        return self._to_request(self._record(payload))

    async def mark_synchronized(self, request_id: str) -> InstallationRequestOut:
        payload = await self._request(
            "POST", f"/traffic/installation-requests/{request_id}/mark-synchronized"
        )
        return self._to_request(self._record(payload))

    # ----------------------------------------------------------------------
    # Camera metadata
    # ----------------------------------------------------------------------

    async def list_approved_cameras(self) -> list[CameraMetadata]:
        payload = await self._request("GET", "/traffic/approved-cameras")
        return [self._to_camera(record) for record in self._records(payload)]

    def _to_event(self, record: dict[str, Any], external_camera_id: str) -> Event:
        external_id = str(record["evt_id"])
        vendor_type = record.get("kind")
        # Deterministic canonical event ID so re-syncing upserts rather than
        # duplicating.
        digest = hashlib.sha1(f"{self.source_system}:{external_id}".encode()).hexdigest()[:16]
        district = self.config.default_district
        canonical_camera_id = norm.make_camera_id(
            self.config.department_code, district, external_camera_id
        )
        canonical_type = {
            "motion_detected": "motion_detected",
            "line_crossing": "line_crossing",
            "camera_tamper": "camera_tamper",
            "video_loss_recovered": "video_loss_recovered",
            "recording_gap": "recording_gap",
            "storage_warning": "storage_warning",
            "device_offline": "device_offline",
        }.get(str(vendor_type or "").lower(), "other")
        return Event(
            event_id=f"sentinel_evt_{digest}",
            source_system=self.source_system,
            external_event_id=external_id,
            camera_id=canonical_camera_id,
            event_type=canonical_type,
            severity=record.get("severity"),
            timestamp_utc=norm.ist_wallclock_to_utc(record["ts"]),  # type: ignore[arg-type]
            payload={
                "event_label": canonical_type,
                "source_event_type": vendor_type,
                "attributes": record.get("meta", {}),
            },
            provenance=self.provenance,
        )

    async def fetch_events(
        self,
        external_camera_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Event]:
        params: dict[str, Any] = {}
        if start:
            params["start"] = norm.utc_to_ist_wallclock(start)
        if end:
            params["end"] = norm.utc_to_ist_wallclock(end)
        payload = await self._request(
            "GET",
            f"/traffic/cameras/{external_camera_id}/events",
            params=params or None,
        )
        rows = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise UpstreamProtocolError(
                "Traffic VMS response is missing the 'events' list",
                source_system=self.source_system,
                detail=str(payload)[:200],
            )
        return [self._to_event(row, external_camera_id) for row in rows]

    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        payload = await self._request("GET", f"/traffic/cameras/{external_camera_id}/health")
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        health = payload.get("health") if isinstance(payload, dict) else None
        if not isinstance(health, dict):
            raise UpstreamProtocolError(
                "Traffic VMS response is missing the 'health' object",
                source_system=self.source_system,
                detail=str(payload)[:200],
            )

        return {
            "status": norm.normalize_health(health.get("state")),
            "last_heartbeat_utc": datetime.now(timezone.utc),
            "last_frame_utc": norm.ist_wallclock_to_utc(health.get("last_frame_at")),
            "latency_ms": latency_ms,
            "reconnect_count": health.get("reconnects"),
            "detail": {
                "vendor_status": health.get("state"),
                "firmware": health.get("firmware"),
                "uptime_pct": health.get("uptime_pct"),
                "adapter": self.adapter_name,
            },
        }
