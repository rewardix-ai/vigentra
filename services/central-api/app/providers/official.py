"""Official Vigentra Gujarat resource provider.

**Status: not configured, and deliberately inert.**

This class is the documented shape an official integration would take. It is
NOT connected to any live portal, and it will not attempt to be:

  * there is no default endpoint baked in — `SENTINEL_RESOURCE_API_URL` has no
    fallback value, so nothing is contacted unless an operator supplies one;
  * every method refuses with `SOURCE_ACCESS_NOT_CONFIGURED` until both the URL
    and a token are present;
  * the field mapping below is driven entirely by whatever the official API
    documentation turns out to specify, and is written against a generic
    documented contract rather than anything inferred from a live site.

Nothing here scrapes, probes, reverse-engineers or fingerprints a portal. If
official access is not granted, run in `mock` or `federated` mode and say so —
`docs/resource-integration.md` covers the prerequisites for switching over.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from ..config import Settings, normalize_city
from ..schemas import (
    CameraProvenance,
    CameraStatus,
    ExternalCameraRecord,
    InstallationStatus,
    RequestStatus,
)
from .base import CameraResourceProvider, ProviderNotConfigured, ProviderUnavailable

logger = logging.getLogger("vigentra.provider.official")


class OfficialVigentraProvider(CameraResourceProvider):
    """Reads an authorized official camera export/API.

    Expects a documented JSON contract of the shape::

        { "cameras": [ { "id": ..., "name": ..., "city": ..., ... } ] }

    The exact field names are configurable through `FIELD_MAP` below because
    the official schema is defined by the authority publishing it, not by us.
    """

    name = "official_vigentra_provider"
    version = "0.1.0"
    is_demo = False

    #: official field -> our field. Adjust to the published contract.
    FIELD_MAP: dict[str, str] = {
        "id": "external_camera_id",
        "name": "camera_name",
        "city": "city",
        "district": "district",
        "zone": "zone",
        "department": "department",
        "latitude": "latitude",
        "longitude": "longitude",
        "status": "health_status",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    # -- configuration gate -------------------------------------------------

    def _require_configuration(self) -> tuple[str, str]:
        """Refuse rather than guess.

        Returning a clear, machine-readable configuration error is the honest
        behaviour when official access has not been granted.
        """
        url = (self.settings.sentinel_resource_api_url or "").strip()
        token = (self.settings.sentinel_resource_api_token or "").strip()
        if not url or not token:
            raise ProviderNotConfigured(
                "The official source adapter is not configured. Supply "
                "SENTINEL_RESOURCE_API_URL and SENTINEL_RESOURCE_API_TOKEN from "
                "your authorized integration agreement, or run in approved "
                "mock/federated mode.",
                provider=self.name,
                detail={
                    "url_configured": bool(url),
                    "token_configured": bool(token),
                    "modes_available": ["mock", "federated"],
                },
            )
        return url, token

    def _http(self) -> httpx.AsyncClient:
        url, token = self._require_configuration()
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=url,
                timeout=httpx.Timeout(float(self.settings.sentinel_resource_timeout_seconds)),
                # The token is held in memory on the client and never logged.
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "User-Agent": f"Vigentra/{self.settings.service_version} (authorized integration)",
                },
                follow_redirects=False,
            )
        return self._client

    def describe(self) -> dict[str, Any]:
        configured = self.settings.official_provider_configured
        return {
            **super().describe(),
            "configured": configured,
            # Host only, never the full URL and never the token.
            "endpoint": (
                self.settings.sentinel_resource_api_url.split("//")[-1].split("/")[0]
                if configured
                else None
            ),
            "status": "ready" if configured else "SOURCE_ACCESS_NOT_CONFIGURED",
        }

    # -- contract -----------------------------------------------------------

    async def list_cameras(self) -> list[ExternalCameraRecord]:
        client = self._http()
        try:
            response = await client.get("/cameras")
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            # Deliberately does not echo the URL or any header back.
            raise ProviderUnavailable(
                "The official camera export could not be reached.",
                provider=self.name,
                detail=str(exc)[:200],
            ) from exc

        rows = payload.get("cameras") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ProviderUnavailable(
                "The official camera export did not return a camera list.",
                provider=self.name,
            )
        return [self._to_record(row) for row in rows]

    def _to_record(self, row: dict[str, Any]) -> ExternalCameraRecord:
        mapped = {
            ours: row.get(theirs)
            for theirs, ours in self.FIELD_MAP.items()
            if row.get(theirs) is not None
        }
        city = str(mapped.get("city") or "")
        external_id = str(mapped.get("external_camera_id") or "")
        department = str(mapped.get("department") or "Unknown Department")

        return ExternalCameraRecord(
            external_camera_id=external_id,
            source_system="official_vigentra",
            department=department,
            department_code=department.split()[0].upper()[:32] if department else "UNKNOWN",
            city=city,
            district=str(mapped.get("district") or city),
            zone=mapped.get("zone"),
            camera_name=str(mapped.get("camera_name") or external_id),
            latitude=mapped.get("latitude"),
            longitude=mapped.get("longitude"),
            health_status=CameraStatus.UNKNOWN,
            installation_status=InstallationStatus.COMMISSIONED,
            request_status=RequestStatus.REGISTERED,
            # Video stays off until the official contract explicitly states that
            # brokered viewing is permitted, and for which cameras.
            video_access_enabled=False,
            capabilities=["metadata", "health"],
            is_demo_data=False,
            provenance=CameraProvenance(
                source_system="official_vigentra",
                external_camera_id=external_id,
                provider=self.name,
                provider_version=self.version,
                imported_at_utc=datetime.now(tz=None),
            ),
        )

    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        self._require_configuration()
        raise ProviderNotConfigured(
            "Per-camera health for the official source requires a documented "
            "health endpoint, which has not been configured.",
            provider=self.name,
        )

    async def create_video_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Refuses until an authorized video contract exists.

        Official video brokering needs an explicit agreement covering which
        cameras may be relayed, under what retention, to which roles. Until
        that is configured this returns the configuration error rather than
        attempting any request.
        """
        raise ProviderNotConfigured(
            "The official source video adapter is not configured. Use approved "
            "mock/demo mode.",
            provider=self.name,
            detail={"external_camera_id": external_camera_id, "mode": mode},
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
