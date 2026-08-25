"""Local mock camera resource provider.

Zero network. Used by the test-suite and by offline demos, and it is the
honest default whenever official access has not been granted. Every record it
produces is flagged `is_demo_data=True` and every camera name is obviously
synthetic.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..config import Settings
from ..schemas import (
    CameraProvenance,
    CameraStatus,
    CameraType,
    ExternalCameraRecord,
    InstallationPurpose,
    InstallationStatus,
    RequestStatus,
    SourceType,
)
from .base import CameraResourceProvider, ProviderError

#: (external_id, name, dept, code, city, zone, district, road, lat, lng,
#:  direction, type, health, video_enabled)
_FIXTURES: list[tuple] = [
    ("MOCK-TRF-0001", "CG Road East", "Traffic Police", "TRAFFIC", "Ahmedabad", "Zone 1",
     "Ahmedabad", "CG Road", 23.0225, 72.5714, "eastbound", CameraType.FIXED,
     CameraStatus.ONLINE, True),
    ("MOCK-TRF-0002", "Ashram Road North", "Traffic Police", "TRAFFIC", "Ahmedabad", "Zone 1",
     "Ahmedabad", "Ashram Road", 23.0339, 72.5664, "northbound", CameraType.PTZ,
     CameraStatus.ONLINE, True),
    ("MOCK-TRF-0003", "Sarkhej Circle", "Traffic Police", "TRAFFIC", "Ahmedabad", "Zone 2",
     "Ahmedabad", "Sarkhej Circle", 22.9955, 72.5012, "westbound", CameraType.DOME,
     CameraStatus.DEGRADED, True),
    ("MOCK-TRF-0101", "Ring Road Junction", "Traffic Police", "TRAFFIC", "Surat", "Zone 1",
     "Surat", "Ring Road", 21.1702, 72.8311, "southbound", CameraType.FIXED,
     CameraStatus.ONLINE, True),
    ("MOCK-SMC-0101", "Municipal Junction 1", "Municipal Corporation", "MUNICIPAL", "Ahmedabad",
     "West Zone", "Ahmedabad", "Vasna Junction", 23.0502, 72.5311, "northbound",
     CameraType.DOME, CameraStatus.ONLINE, True),
    ("MOCK-SMC-0102", "Riverfront Walkway", "Municipal Corporation", "MUNICIPAL", "Ahmedabad",
     "Central Zone", "Ahmedabad", "Khanpur Riverfront", 23.0489, 72.5799, "westbound",
     CameraType.BULLET, CameraStatus.ONLINE, True),
    # Deliberately video-disabled: proves the owner's policy is a hard gate
    # that no role can override.
    ("MOCK-SMC-0103", "Kankaria Lake Gate 3", "Municipal Corporation", "MUNICIPAL", "Ahmedabad",
     "South Zone", "Ahmedabad", "Kankaria Gate 3", 22.9195, 72.6003, "southbound",
     CameraType.PTZ, CameraStatus.ONLINE, False),
    # Deliberately withdrawn: proves a suspended camera cannot be viewed.
    ("MOCK-SMC-0104", "Old Bus Depot Yard", "Municipal Corporation", "MUNICIPAL", "Ahmedabad",
     "East Zone", "Ahmedabad", "Rakhial Depot Road", 23.0301, 72.6215, "eastbound",
     CameraType.FIXED, CameraStatus.UNAVAILABLE, False),
]


class MockCameraResourceProvider(CameraResourceProvider):
    """Deterministic in-memory inventory. No I/O of any kind."""

    name = "mock_camera_provider"
    version = "0.1.0"
    is_demo = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self._sessions: dict[str, dict[str, Any]] = {}

    def _provenance(self, external_id: str, source_system: str) -> CameraProvenance:
        return CameraProvenance(
            source_system=source_system,
            external_camera_id=external_id,
            provider=self.name,
            provider_version=self.version,
            imported_at_utc=datetime.now(timezone.utc),
        )

    async def list_cameras(self) -> list[ExternalCameraRecord]:
        records: list[ExternalCameraRecord] = []
        for (
            external_id, name, department, code, city, zone, district, road,
            lat, lng, direction, camera_type, health, video_enabled,
        ) in _FIXTURES:
            source_system = "traffic_vms" if code == "TRAFFIC" else "municipal_vms"
            withdrawn = health is CameraStatus.UNAVAILABLE
            capabilities = ["metadata", "health"]
            if video_enabled and not withdrawn:
                capabilities += ["live", "playback"]

            records.append(
                ExternalCameraRecord(
                    external_camera_id=external_id,
                    source_system=source_system,
                    department=department,
                    department_code=code,
                    city=city,
                    district=district,
                    zone=zone,
                    camera_name=name,
                    camera_type=camera_type,
                    vendor="Demo Vendor",
                    model="Demo IP Camera X1",
                    installation_purpose=(
                        InstallationPurpose.TRAFFIC_MONITORING
                        if code == "TRAFFIC"
                        else InstallationPurpose.PUBLIC_SAFETY
                    ),
                    owning_unit=f"{city} {zone}",
                    latitude=lat,
                    longitude=lng,
                    address_or_landmark=f"{road} (demonstration coordinates)",
                    road=road,
                    view_direction=direction,
                    source_type=SourceType.VMS_API,
                    vms_name="Mock VMS",
                    resolution="1920x1080",
                    fps=25,
                    codec="H.264",
                    retention_days=30,
                    capabilities=capabilities,
                    installation_status=(
                        InstallationStatus.SUSPENDED if withdrawn
                        else InstallationStatus.COMMISSIONED
                    ),
                    request_status=(
                        RequestStatus.SUSPENDED if withdrawn else RequestStatus.SYNCHRONIZED
                    ),
                    installation_date=date(2026, 8, 1),
                    commissioning_date=None if withdrawn else date(2026, 8, 5),
                    approved_by_role="video_access_approver",
                    approved_at=datetime(2026, 8, 6, 9, 30, tzinfo=timezone.utc),
                    health_status=health,
                    last_seen_utc=datetime.now(timezone.utc),
                    video_access_enabled=video_enabled and not withdrawn,
                    permitted_local_roles=["department_operator", "district_supervisor"],
                    is_demo_data=True,
                    provenance=self._provenance(external_id, source_system),
                )
            )
        return records

    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        match = next(
            (row for row in _FIXTURES if row[0].upper() == external_camera_id.upper()), None
        )
        if match is None:
            raise ProviderError(
                f"Mock provider has no camera '{external_camera_id}'", provider=self.name
            )
        return {
            "status": match[12],
            "last_heartbeat_utc": datetime.now(timezone.utc),
            "last_frame_utc": datetime.now(timezone.utc),
            "latency_ms": 3.0,
            "reconnect_count": 0,
            "detail": {"provider": self.name, "is_demo_data": True},
        }

    async def create_video_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a handle to a bundled demo clip.

        Still short-lived and still opaque — the mock behaves like a real source
        so the broker's expiry and revocation logic is exercised identically in
        tests and in the offline demo.
        """
        match = next(
            (row for row in _FIXTURES if row[0].upper() == external_camera_id.upper()), None
        )
        if match is None:
            raise ProviderError(
                f"Mock provider has no camera '{external_camera_id}'", provider=self.name
            )
        if not match[13]:
            raise ProviderError(
                f"Mock camera '{external_camera_id}' has video disabled by its owner",
                provider=self.name,
            )

        clip = "traffic_01.mp4" if match[3] == "TRAFFIC" else "municipal_01.mp4"
        return {
            "source_session_reference": f"mock://clip/{clip}",
            "protocol": "mock-file",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "is_demo_data": True,
        }
