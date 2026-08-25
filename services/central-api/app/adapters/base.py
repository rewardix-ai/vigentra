"""The adapter contract every federated department system must satisfy.

An adapter is the ONLY component allowed to know a department's field names,
auth scheme, lifecycle vocabulary, timestamp format or error envelope. It
converts all of that into canonical models and canonical exceptions, so the
sync service, the routers and the dashboard stay vendor-neutral.

Module 1 boundary, enforced by the shape of this contract: there is no
`create_video_session`, no stream method and no media method. An adapter has no
way to hand Sentinel a playable feed, because Sentinel has nowhere to put one.

See docs/adapter-contract.md and docs/access-model.md.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import SourceSettings
from ..schemas import (
    CameraMetadata,
    Event,
    InstallationForm,
    InstallationFormPatch,
    InstallationRequestOut,
)


# --------------------------------------------------------------------------
# Canonical adapter errors
# --------------------------------------------------------------------------

class AdapterError(Exception):
    """Base class for every failure an adapter may surface.

    `http_status` is what the central API returns to its own clients. An
    upstream 401 becomes a 502 for us: the department system rejected
    *Sentinel's* credentials, which is a middleware fault, not an operator
    authorisation problem. Confusing the two sends people hunting the wrong bug.
    """

    code = "adapter_error"
    http_status = 502
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        source_system: str,
        detail: Any = None,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source_system = source_system
        self.detail = detail
        self.upstream_status = upstream_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "source_system": self.source_system,
            "upstream_status": self.upstream_status,
            "detail": self.detail,
            "retryable": self.retryable,
        }

    def __str__(self) -> str:
        suffix = f" (upstream HTTP {self.upstream_status})" if self.upstream_status else ""
        return f"[{self.code}] {self.message}{suffix}"


class SourceUnavailableError(AdapterError):
    """The department system could not be reached at all."""

    code = "source_unavailable"
    http_status = 503
    retryable = True


class SourceTimeoutError(SourceUnavailableError):
    code = "source_timeout"


class SourceAuthError(AdapterError):
    """Sentinel's own credentials for the department system were rejected."""

    code = "source_auth_failed"
    http_status = 502


class SourceRateLimitedError(AdapterError):
    code = "source_rate_limited"
    http_status = 429
    retryable = True


class ResourceNotFoundError(AdapterError):
    """The department system does not know this camera or request."""

    code = "resource_not_found"
    http_status = 404


class SourceValidationError(AdapterError):
    """The department system refused the payload (bad form, wrong lifecycle)."""

    code = "source_validation_failed"
    http_status = 422


class SourceConflictError(AdapterError):
    """The record is not in a state that permits this transition."""

    code = "source_state_conflict"
    http_status = 409


class UpstreamProtocolError(AdapterError):
    """The department system answered, but not in a shape this adapter knows."""

    code = "upstream_protocol_error"
    http_status = 502


# --------------------------------------------------------------------------
# Base adapter
# --------------------------------------------------------------------------

class SurveillanceAdapter(ABC):
    """Abstract department-system adapter. Metadata only."""

    source_system: str = "unknown"
    adapter_name: str = "base_adapter"
    adapter_version: str = "0.2.0"
    health_path: str = "/health"

    def __init__(
        self,
        config: SourceSettings,
        *,
        timeout: float = 6.0,
        retries: int = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.source_system = config.source_system
        self.timeout = timeout
        self.retries = max(0, retries)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )

    # -- identity ---------------------------------------------------------

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter_name,
            "adapter_version": self.adapter_version,
            "source_system": self.source_system,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metadata_only": True,
        }

    def _auth_headers(self) -> dict[str, str]:
        """Vendor auth scheme. Never logged, never returned to a client."""
        return {}

    # -- transport --------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        authenticated: bool = True,
    ) -> Any:
        """Perform one upstream call and translate every failure mode.

        Handled: connect failure, timeout, 400/422, 401/403, 404, 409, 429, 5xx,
        and a body that is not JSON. Retryable classes are retried.
        """
        headers = self._auth_headers() if authenticated else {}
        attempts = self.retries + 1
        last_error: AdapterError | None = None

        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, path, params=params, json=json_body, headers=headers
                )
            except httpx.TimeoutException as exc:
                last_error = SourceTimeoutError(
                    f"{self.source_system} did not respond within {self.timeout:.1f}s",
                    source_system=self.source_system,
                    detail=str(exc),
                )
            except httpx.HTTPError as exc:
                last_error = SourceUnavailableError(
                    f"{self.source_system} is unreachable",
                    source_system=self.source_system,
                    detail=str(exc),
                )
            else:
                return self._handle_response(response)

            if attempt < attempts - 1:
                await asyncio.sleep(0.25 * (attempt + 1))

        assert last_error is not None
        raise last_error

    def _handle_response(self, response: httpx.Response) -> Any:
        status = response.status_code
        if status < 400:
            try:
                return response.json()
            except ValueError as exc:
                raise UpstreamProtocolError(
                    f"{self.source_system} returned a non-JSON body",
                    source_system=self.source_system,
                    detail=str(exc),
                    upstream_status=status,
                ) from exc

        body = self._safe_body(response)
        if status in (400, 422):
            raise SourceValidationError(
                f"{self.source_system} rejected the submitted record",
                source_system=self.source_system,
                detail=body,
                upstream_status=status,
            )
        if status in (401, 403):
            raise SourceAuthError(
                f"Sentinel's credentials for {self.source_system} were rejected",
                source_system=self.source_system,
                detail=body,
                upstream_status=status,
            )
        if status == 404:
            raise ResourceNotFoundError(
                f"{self.source_system} does not know this resource",
                source_system=self.source_system,
                detail=body,
                upstream_status=status,
            )
        if status == 409:
            raise SourceConflictError(
                f"{self.source_system} refused this lifecycle transition",
                source_system=self.source_system,
                detail=body,
                upstream_status=status,
            )
        if status == 429:
            raise SourceRateLimitedError(
                f"{self.source_system} is rate limiting Sentinel",
                source_system=self.source_system,
                detail=body,
                upstream_status=status,
            )
        if status in (500, 502, 503, 504):
            raise SourceUnavailableError(
                f"{self.source_system} reported an internal failure",
                source_system=self.source_system,
                detail=body,
                upstream_status=status,
            )
        raise UpstreamProtocolError(
            f"{self.source_system} returned an unexpected HTTP {status}",
            source_system=self.source_system,
            detail=body,
            upstream_status=status,
        )

    @staticmethod
    def _safe_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]

    # -- lifecycle --------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def check_source_health(self) -> dict[str, Any]:
        """Probe the department system's liveness endpoint and time it."""
        import time

        started = time.perf_counter()
        try:
            payload = await self._request("GET", self.health_path, authenticated=False)
        except AdapterError as exc:
            return {
                "reachable": False,
                "status": "offline",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": str(exc),
                "error_code": exc.code,
            }
        return {
            "reachable": True,
            "status": "online",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "upstream": payload,
        }

    # ----------------------------------------------------------------------
    # The contract: installation register
    # ----------------------------------------------------------------------

    @abstractmethod
    async def list_installation_requests(self) -> list[InstallationRequestOut]:
        """Every installation record this department holds, at any stage."""

    @abstractmethod
    async def get_installation_request(self, request_id: str) -> InstallationRequestOut:
        """One installation record."""

    @abstractmethod
    async def create_installation_request(
        self, form: InstallationForm, *, created_by: str
    ) -> InstallationRequestOut:
        """Create a DRAFT record in the department's own register."""

    @abstractmethod
    async def update_installation_request(
        self, request_id: str, patch: InstallationFormPatch, *, updated_by: str
    ) -> InstallationRequestOut:
        """Edit a record that has not yet left the drafting stage."""

    @abstractmethod
    async def submit_installation_request(
        self, request_id: str, *, submitted_by: str
    ) -> InstallationRequestOut:
        """Run the department's validation.

        Passing it registers the camera immediately - there is no approval
        queue in the metadata path.
        """

    @abstractmethod
    async def suspend_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        """Take a commissioned camera out of service."""

    @abstractmethod
    async def decommission_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        """Permanently retire a camera asset."""

    @abstractmethod
    async def mark_synchronized(self, request_id: str) -> InstallationRequestOut:
        """Tell the department system Sentinel has taken this record's metadata."""

    # ----------------------------------------------------------------------
    # The contract: camera metadata
    # ----------------------------------------------------------------------

    @abstractmethod
    async def list_approved_cameras(self) -> list[CameraMetadata]:
        """Canonical metadata for every commissioned camera.

        Includes suspended and decommissioned assets, carrying their status, so
        the registry can mark them unavailable rather than losing them. Records
        still being drafted, or failing validation, must NOT be returned.
        """

    @abstractmethod
    async def fetch_events(
        self,
        external_camera_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Event]:
        """Return canonical generic events for one camera in a time window.

        A commissioned camera with no events returns []; a camera the source
        does not know returns ResourceNotFoundError. Metadata only - the
        payload carries no video URL and no media token.
        """

    @abstractmethod
    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        """Canonical health facts for one camera.

        Keys: status, last_heartbeat_utc, last_frame_utc, latency_ms,
        reconnect_count, detail.
        """
