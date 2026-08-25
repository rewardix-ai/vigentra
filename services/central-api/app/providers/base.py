"""Camera resource provider contract.

A *provider* answers "where does the authorized camera inventory come from?".
It sits above the department adapters: an adapter speaks one department's API
dialect, a provider decides which source of truth the platform is reading from
at all — a local fixture, the two federated department systems, or a documented
official export.

Providers never return credentials, raw RTSP URLs, NVR addresses or permanent
public links. `ExternalCameraRecord` has nowhere to put them.

Configuration (see `.env.example`):

    SENTINEL_RESOURCE_MODE=mock | federated | official
    SENTINEL_RESOURCE_API_URL=
    SENTINEL_RESOURCE_API_TOKEN=
    SENTINEL_RESOURCE_TIMEOUT_SECONDS=20

Tokens are read from configuration, held only in memory, and never logged or
serialised. `__repr__` on every provider is redacted for exactly this reason.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..schemas import ExternalCameraRecord


class ProviderError(Exception):
    """Base class for provider failures surfaced to the API layer."""

    code = "provider_error"
    http_status = 502

    def __init__(self, message: str, *, provider: str, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "provider": self.provider,
            "detail": self.detail,
        }


class ProviderNotConfigured(ProviderError):
    """The provider needs authorized configuration that has not been supplied.

    This is the honest answer when someone selects `official` mode without an
    official endpoint and token. The platform refuses rather than guessing at
    an undocumented URL.
    """

    code = "SOURCE_ACCESS_NOT_CONFIGURED"
    http_status = 503


class ProviderUnavailable(ProviderError):
    """The configured provider could not be reached."""

    code = "provider_unavailable"
    http_status = 503


class CameraResourceProvider(ABC):
    """Where authorized camera records come from."""

    name: str = "base_provider"
    version: str = "0.1.0"
    #: True when the records are synthetic and must be labelled as such.
    is_demo: bool = True

    @abstractmethod
    async def list_cameras(self) -> list[ExternalCameraRecord]:
        """Return every camera this provider is authorized to expose."""

    @abstractmethod
    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        """Return canonical health facts for one camera."""

    @abstractmethod
    async def create_video_session(
        self,
        external_camera_id: str,
        mode: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask the source for an authorized, short-lived feed handle.

        Returns a dict with at least:
            source_session_reference  internal URL/handle, never sent to a client
            protocol                  http-mp4 | hls | webrtc
            expires_at                datetime | None (the source's own expiry)

        Implementations must not return credentials, and must not return a
        handle that remains valid indefinitely.
        """

    async def aclose(self) -> None:  # pragma: no cover - most providers hold nothing
        return None

    def describe(self) -> dict[str, Any]:
        """Safe-to-log, safe-to-return description. Never includes a token."""
        return {
            "provider": self.name,
            "provider_version": self.version,
            "is_demo": self.is_demo,
        }

    def __repr__(self) -> str:  # redacted on purpose
        return f"<{type(self).__name__} name={self.name!r} version={self.version!r}>"
