"""Federated provider - the two department systems, via their adapters.

This is the default demo mode. The provider does not open its own connections:
it delegates to the live adapter registry so there is exactly one place that
knows each department's dialect, auth scheme and error envelope.

`TrafficVmsProvider` and `MunicipalVmsProvider` are thin single-department
wrappers over the same machinery, useful when a deployment federates only one
department or when testing one source in isolation.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..adapters.base import AdapterError, SurveillanceAdapter
from ..config import Settings
from ..schemas import CameraProvenance, ExternalCameraRecord
from .base import CameraResourceProvider, ProviderError, ProviderUnavailable

logger = logging.getLogger("sentinel.provider.federated")


class FederatedProvider(CameraResourceProvider):
    """Reads approved camera metadata from every registered department system."""

    name = "federated_department_provider"
    version = "0.2.0"
    is_demo = True

    #: Restrict to these source systems; empty means every registered one.
    only_sources: tuple[str, ...] = ()

    def __init__(self, settings: Settings, adapters: dict[str, SurveillanceAdapter]) -> None:
        self.settings = settings
        self.adapters = adapters

    def _selected(self) -> dict[str, SurveillanceAdapter]:
        if not self.only_sources:
            return self.adapters
        return {
            name: adapter
            for name, adapter in self.adapters.items()
            if name in self.only_sources
        }

    def _config_for(self, source_system: str):
        return next(
            (s for s in self.settings.sources if s.source_system == source_system), None
        )

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "departments": [
                {"source_system": name, "adapter": adapter.adapter_name}
                for name, adapter in self._selected().items()
            ],
        }

    # -- contract -----------------------------------------------------------

    async def list_cameras(self) -> list[ExternalCameraRecord]:
        """Pull every department concurrently.

        One department being unreachable is logged and skipped rather than
        failing the whole inventory - the same isolation guarantee the sync
        service gives.
        """
        selected = self._selected()
        if not selected:
            raise ProviderUnavailable(
                "No department adapters are registered.", provider=self.name
            )

        async def _one(source_system: str, adapter: SurveillanceAdapter):
            try:
                return source_system, await adapter.list_approved_cameras(), None
            except AdapterError as exc:
                logger.warning("federated provider: %s failed: %s", source_system, exc)
                return source_system, [], str(exc)

        results = await asyncio.gather(
            *(_one(name, adapter) for name, adapter in selected.items())
        )

        records: list[ExternalCameraRecord] = []
        failures: list[str] = []
        for source_system, cameras, error in results:
            if error:
                failures.append(f"{source_system}: {error}")
                continue
            config = self._config_for(source_system)
            for camera in cameras:
                records.append(self._to_record(camera, source_system, config))

        if not records and failures:
            raise ProviderUnavailable(
                "No department system could be reached.",
                provider=self.name,
                detail=failures,
            )
        return records

    def _to_record(self, camera, source_system: str, config) -> ExternalCameraRecord:
        """Adapter output -> provider record.

        City and zone are the two fields the department dialects do not agree
        on: the Traffic system reports a police-station string, the Municipal
        one a "West Zone / Vasna Ward" pair, and neither reports a city at all.
        Both fall back to the values recorded when the source was registered.
        """
        city = getattr(camera, "city", None) or (config.default_city if config else "")
        zone = getattr(camera, "zone", None) or camera.police_station_or_zone

        capabilities = ["metadata", "health"]
        if camera.local_video_access and camera.installation_status == "COMMISSIONED":
            capabilities += ["live", "playback"]

        return ExternalCameraRecord(
            external_camera_id=camera.external_camera_id,
            source_system=source_system,
            department=camera.owning_department,
            department_code=(config.department_code if config else ""),
            city=city,
            district=camera.district,
            zone=zone,
            camera_name=camera.name,
            camera_type=camera.camera_type,
            vendor=camera.vendor,
            model=camera.model,
            installation_purpose=camera.installation_purpose,
            camera_serial_masked=camera.camera_serial_masked,
            owning_unit=camera.owning_unit,
            maintenance_agency=camera.maintenance_agency,
            installation_vendor=camera.installation_vendor,
            latitude=camera.latitude,
            longitude=camera.longitude,
            address_or_landmark=camera.landmark,
            road=camera.road_or_junction,
            view_direction=camera.view_direction,
            coverage_description=camera.coverage_description,
            entry_exit_zone_description=camera.entry_exit_zone_description,
            source_type=camera.source_type,
            vms_name=camera.vms_name,
            vms_vendor=camera.vms_vendor,
            resolution=camera.resolution,
            fps=camera.fps,
            codec=camera.codec,
            retention_days=camera.retention_days,
            timezone_name=camera.timezone_name,
            capabilities=capabilities,
            installation_status=camera.installation_status,
            request_status=camera.request_status,
            installation_request_id=camera.installation_request_id,
            installation_date=camera.installation_date,
            commissioning_date=camera.commissioning_date,
            approved_by_role=camera.approved_by_role,
            approved_at=camera.approved_at,
            health_status=camera.health_status,
            last_seen_utc=camera.last_frame_utc,
            reconnect_count=camera.reconnect_count,
            # The owning department's own capability flag is the gate.
            video_access_enabled=bool(camera.local_video_access)
            and camera.installation_status == "COMMISSIONED",
            permitted_local_roles=list(camera.permitted_local_roles),
            is_demo_data=True,
            provenance=CameraProvenance(
                source_system=source_system,
                external_camera_id=camera.external_camera_id,
                provider=self.name,
                provider_version=self.version,
                imported_at_utc=datetime.now(timezone.utc),
            ),
        )

    def _adapter_for(self, source_system: str) -> SurveillanceAdapter:
        adapter = self._selected().get(source_system)
        if adapter is None:
            raise ProviderError(
                f"No adapter registered for '{source_system}'", provider=self.name
            )
        return adapter

    async def get_camera_health(
        self, external_camera_id: str, source_system: str | None = None
    ) -> dict[str, Any]:
        """Probe one camera. Without a source hint, every department is tried."""
        candidates = (
            [self._adapter_for(source_system)]
            if source_system
            else list(self._selected().values())
        )
        last_error: Exception | None = None
        for adapter in candidates:
            try:
                return await adapter.get_camera_health(external_camera_id)
            except AdapterError as exc:
                last_error = exc
                continue
        raise ProviderUnavailable(
            f"No department system could report health for '{external_camera_id}'",
            provider=self.name,
            detail=str(last_error) if last_error else None,
        )

    async def create_video_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Delegate to the owning department's video adapter.

        The broker resolves the source system before calling this, and passes
        it through `user_context["source_system"]`.
        """
        from ..video_adapters import build_video_adapter

        source_system = (user_context or {}).get("source_system")
        if not source_system:
            raise ProviderError(
                "create_video_session requires user_context['source_system']",
                provider=self.name,
            )
        config = self._config_for(source_system)
        if config is None:
            raise ProviderError(
                f"No source configuration for '{source_system}'", provider=self.name
            )

        video_adapter = build_video_adapter(config, self.settings)
        try:
            return await video_adapter.create_session(
                external_camera_id, mode, start_time, end_time, user_context
            )
        finally:
            await video_adapter.aclose()


class TrafficVmsProvider(FederatedProvider):
    """Traffic Police department system only."""

    name = "traffic_vms_provider"
    only_sources = ("traffic_vms",)


class MunicipalVmsProvider(FederatedProvider):
    """Municipal Corporation department system only."""

    name = "municipal_vms_provider"
    only_sources = ("municipal_vms",)
