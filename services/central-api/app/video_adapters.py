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
import re
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
    "official_sentinel": OfficialSentinelVideoAdapter,
}


def build_video_adapter(
    config: SourceSettings,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> BaseVideoAdapter:
    """Pick the video adapter for one department, honouring resource mode.

    Video follows the department's METADATA adapter rather than a table keyed
    by department name. Where a department's records come from is where its
    footage comes from, and a deployment that points Traffic Police at its own
    VMS gets that VMS's video - not the shared gateway, because a hard-coded
    pair said so.
    """
    if (settings.sentinel_resource_mode or "").strip().lower() == "mock":
        return MockVideoAdapter(config, settings, client)
    by_metadata_adapter = METADATA_ADAPTER_VIDEO.get(config.adapter)
    if by_metadata_adapter is not None:
        return by_metadata_adapter(config, settings, client)
    adapter_cls = VIDEO_ADAPTERS.get(config.source_system, MockVideoAdapter)
    return adapter_cls(config, settings, client)


#: A grid camera id in the scheme introduced with the cctv.corp8.cloud move:
#: a short alphanumeric token such as `cam04`. Deliberately permissive about
#: the prefix - the grid has renamed its cameras once already.
_GRID_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,31}")


class SentinelGridVideoAdapter(BaseVideoAdapter):
    """Live HLS from the Sentinel sandbox grid.

    Live only, and that is a property of the source rather than a policy: the
    grid is a live feed with no archive, no seeking and no byte-range fetching.
    A playback request is refused here as well as by the camera's capability
    list, so "there is no recording" never depends on a single check.

    RTSP is what the guide recommends for inference, but it is not a browser
    protocol and port 8554 is blocked on many networks, so the browser path is
    always the HLS endpoint. The edge worker takes RTSP directly and falls back
    to HLS - see services/edge-worker/app/grid.py.

    The manifest URL returned here is INTERNAL. The browser only ever sees
    /api/v1/streams/{session_id}; the broker rewrites the playlist so no
    upstream host reaches the page.
    """

    name = "sentinel_grid_video_adapter"

    #: The gateway 302s to an http:// URL unless this query flag is present,
    #: which would downgrade the scheme and break the player. Setting it on
    #: every request keeps the whole chain on https.
    COOKIE_CHECK = {"cookieCheck": "1"}

    async def create_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode != "live":
            raise VideoAdapterError(
                "The Sentinel grid carries no recorded archive. Only live "
                "viewing is available for these cameras.",
                source_system=self.source_system,
                detail={"requested_mode": mode, "available_modes": ["live"]},
            )

        # GRID-cam04 -> cam04, and GRID-007 -> 7 for the numbering the grid
        # used before it renumbered. Both are accepted because the registry
        # holds ids federated under either scheme, and a camera onboarded last
        # week must not stop streaming because the upstream renamed itself.
        raw = str(external_camera_id).removeprefix("GRID-").removeprefix("grid-")
        raw = raw.strip()
        if raw.isdigit():
            raw = raw.lstrip("0") or "0"
        elif not _GRID_ID.fullmatch(raw):
            raise VideoAdapterError(
                f"'{external_camera_id}' is not a Sentinel grid camera id",
                source_system=self.source_system,
            )

        base = self.config.base_url.rstrip("/")
        # Path shape changed with the move: /live/stream/<n>/index.m3u8 became
        # /<id>/index.m3u8 on the CDN host.
        manifest = (f"{base}/{raw}/index.m3u8" if not raw.isdigit()
                    else f"{base}/live/stream/{raw}/index.m3u8?cookieCheck=1")

        # Probe the manifest before handing it over. The grid's HLS packager
        # fails independently of the rest of the gateway - the catalogue keeps
        # answering 200 and reporting the camera live while every playlist
        # returns 502 - so a session opened blind produces a player stuck on a
        # black frame with nothing to say about why.
        if await self._manifest_ok(manifest):
            return {
                "source_session_reference": manifest,
                "protocol": "hls",
                # The grid issues no ticket of its own, so the session's lifetime
                # is Sentinel's own TTL. Returning None lets the broker apply it.
                "expires_at": None,
                "is_demo_data": False,
            }

        # Fall back to the endpoint the guide itself names "the browser playback
        # fallback": /stream/<id> answers range requests for a media player.
        #
        # This is NOT the thing the guide warns against. That warning is about
        # planning around *obtaining a copy* - pulling the path with curl and
        # building a pipeline against a local file that looks complete but is
        # not. Serving it to a <video> element is its stated purpose, and the
        # broker still proxies it, still range-limits it, still watermarks the
        # session and still expires it. Inference never uses this path.
        progressive = f"{base}/stream/{raw}"
        logger.warning(
            "grid camera %s: HLS manifest unavailable, using the browser "
            "playback fallback for this session",
            external_camera_id,
        )
        return {
            "source_session_reference": progressive,
            "protocol": "http-mp4",
            "expires_at": None,
            "is_demo_data": False,
            "degraded": "hls_unavailable",
        }

    async def _manifest_ok(self, manifest: str) -> bool:
        """Is the HLS packager actually serving this camera right now?

        Deliberately cheap and deliberately forgiving: one short GET, and any
        transport error counts as "not ok" rather than raising. A slow probe
        here would delay every session open, and an exception would turn a
        recoverable degradation into a failed request.
        """
        try:
            response = await self._client.get(
                manifest, timeout=6.0, follow_redirects=True
            )
        except httpx.HTTPError as exc:
            logger.info("grid manifest probe failed: %s", exc)
            return False
        return response.status_code == 200 and response.text.lstrip().startswith("#EXTM3U")


#: Video transport implied by a department's metadata adapter. A department
#: whose records are read from the shared gateway streams from the shared
#: gateway; one running its own VMS streams from that VMS.
METADATA_ADAPTER_VIDEO: dict[str, type[BaseVideoAdapter]] = {
    "grid_adapter": SentinelGridVideoAdapter,
    "traffic_adapter": TrafficVmsVideoAdapter,
    "municipal_adapter": MunicipalVmsVideoAdapter,
}
