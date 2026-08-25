"""Source-specific video adapters.

One per department dialect. Each asks its own system for a short-lived,
authorized feed handle and returns it in a canonical shape:

    {
      "source_session_reference": "<internal URL or handle>",
      "protocol": "http-mp4" | "hls" | "webrtc" | "mock-file",
      "expires_at": datetime | None,
    }

`source_session_reference` is INTERNAL. It goes into the `video_sessions` row
and is read only by the stream proxy inside this service. It is never placed in
a response model, never logged in full, and never rendered into the page.

In this build the demo departments hand back ticketed HTTP MP4 URLs. A
production deployment would swap the reference for an HLS manifest produced by
a local media gateway (FFmpeg/MediaMTX) — the broker above does not change,
because it only ever handles an opaque handle.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import Settings, SourceSettings
from .services import normalization as norm

logger = logging.getLogger("sentinel.video_adapter")


class VideoAdapterError(Exception):
    """A department system refused or could not serve a feed handle."""

    code = "video_source_error"
    http_status = 502

    def __init__(self, message: str, *, source_system: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.source_system = source_system
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "source_system": self.source_system,
            "detail": self.detail,
        }


class VideoSourceUnavailable(VideoAdapterError):
    code = "VIDEO_SOURCE_UNAVAILABLE"
    http_status = 503


class VideoNotConfigured(VideoAdapterError):
    code = "SOURCE_ACCESS_NOT_CONFIGURED"
    http_status = 503


class BaseVideoAdapter(ABC):
    """Contract for asking one department for an authorized feed."""

    name = "base_video_adapter"
    source_system = "unknown"

    def __init__(
        self,
        config: SourceSettings,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.settings = settings
        self.source_system = config.source_system
        # Reuse the department's existing connection pool when one is handed in
        # (which is also what lets the test-suite route these calls in-process).
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(settings.upstream_timeout_seconds),
            follow_redirects=True,
        )

    def _auth_headers(self) -> dict[str, str]:
        return {}

    @abstractmethod
    async def create_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a canonical feed handle. Never returns a credential."""

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.get(
                path, params=params, headers=self._auth_headers()
            )
        except httpx.HTTPError as exc:
            raise VideoSourceUnavailable(
                f"{self.source_system} is unreachable for video",
                source_system=self.source_system,
                detail=str(exc)[:200],
            ) from exc

        if response.status_code == 404:
            raise VideoAdapterError(
                f"{self.source_system} does not know this camera",
                source_system=self.source_system,
            )
        if response.status_code in (401, 403):
            # Sentinel's own credential was rejected: a middleware fault, not
            # the operator's. Surfaced as 502 upstream, never as a 403 that
            # would wrongly suggest the operator lacks permission.
            raise VideoAdapterError(
                f"Sentinel's credentials for {self.source_system} were rejected",
                source_system=self.source_system,
            )
        if response.status_code == 503:
            raise VideoSourceUnavailable(
                f"{self.source_system} reports this camera as unavailable",
                source_system=self.source_system,
            )
        if response.status_code >= 400:
            raise VideoAdapterError(
                f"{self.source_system} returned HTTP {response.status_code}",
                source_system=self.source_system,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise VideoAdapterError(
                f"{self.source_system} returned a non-JSON video response",
                source_system=self.source_system,
            ) from exc


class TrafficVmsVideoAdapter(BaseVideoAdapter):
    """Traffic Police: X-API-Key auth, ticketed MP4, IST wall-clock expiry."""

    name = "traffic_video_adapter"

    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.config.credential}

    async def create_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode == "playback":
            params: dict[str, Any] = {}
            if start_time:
                params["start"] = norm.utc_to_ist_wallclock(start_time)
            if end_time:
                params["end"] = norm.utc_to_ist_wallclock(end_time)
            payload = await self._get(
                f"/traffic/cameras/{external_camera_id}/playback", params or None
            )
        else:
            payload = await self._get(f"/traffic/cameras/{external_camera_id}/live")
        url = payload.get("playback_url") if isinstance(payload, dict) else None
        if not url:
            raise VideoAdapterError(
                "Traffic VMS did not return a playback handle",
                source_system=self.source_system,
            )
        return {
            "source_session_reference": url,
            "protocol": "http-mp4",
            # The department's ticket TTL; the broker clamps its own session to
            # whichever expiry is sooner.
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "custody": payload.get("custody"),
            "is_demo_data": True,
            # Which part of the archive answers this window. Canonicalised
            # here so the broker never sees a vendor's field names.
            "segment_start_seconds": payload.get("segment_start_seconds"),
            "segment_end_seconds": payload.get("segment_end_seconds"),
        }


class MunicipalVmsVideoAdapter(BaseVideoAdapter):
    """Municipal Corporation: Bearer auth, tokenised MP4, epoch-ms expiry."""

    name = "municipal_video_adapter"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.credential}"}

    async def create_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode == "playback":
            params: dict[str, Any] = {}
            if start_time:
                params["from"] = norm.utc_to_epoch_ms(start_time)
            if end_time:
                params["to"] = norm.utc_to_epoch_ms(end_time)
            payload = await self._get(
                f"/vms/recording/{external_camera_id}", params or None
            )
        else:
            payload = await self._get(f"/vms/live/{external_camera_id}")
        data = payload.get("playback") if isinstance(payload, dict) else None
        url = data.get("url") if isinstance(data, dict) else None
        if not url:
            raise VideoAdapterError(
                "Municipal VMS did not return a playback handle",
                source_system=self.source_system,
            )
        return {
            "source_session_reference": url,
            "protocol": "http-mp4",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "custody": data.get("custody"),
            "is_demo_data": True,
            "segment_start_seconds": data.get("segmentStartSeconds"),
            "segment_end_seconds": data.get("segmentEndSeconds"),
        }


class OfficialSentinelVideoAdapter(BaseVideoAdapter):
    """Official source video. Inert until an authorized contract is configured.

    No default endpoint, no probing, no fallback. See `providers/official.py`
    for the same stance on metadata.
    """

    name = "official_video_adapter"

    async def create_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise VideoNotConfigured(
            "The official source video adapter is not configured. Use approved "
            "mock/demo mode.",
            source_system=self.source_system,
            detail={"external_camera_id": external_camera_id, "mode": mode},
        )


class MockVideoAdapter(BaseVideoAdapter):
    """Serves a bundled demo clip. Still short-lived and still opaque."""

    name = "mock_video_adapter"

    async def create_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clip = (
            "traffic_01.mp4"
            if self.config.department_code == "TRAFFIC"
            else "municipal_01.mp4"
        )
        return {
            "source_session_reference": f"mock://clip/{clip}",
            "protocol": "mock-file",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "is_demo_data": True,
        }


VIDEO_ADAPTERS: dict[str, type[BaseVideoAdapter]] = {
    "traffic_vms": TrafficVmsVideoAdapter,
    "municipal_vms": MunicipalVmsVideoAdapter,
    "official_sentinel": OfficialSentinelVideoAdapter,
}


def build_video_adapter(
    config: SourceSettings,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> BaseVideoAdapter:
    """Pick the video adapter for one department, honouring resource mode."""
    if (settings.sentinel_resource_mode or "").strip().lower() == "mock":
        return MockVideoAdapter(config, settings, client)
    adapter_cls = VIDEO_ADAPTERS.get(config.source_system, MockVideoAdapter)
    return adapter_cls(config, settings, client)
