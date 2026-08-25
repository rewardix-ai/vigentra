"""Camera resource providers.

Selecting a provider decides where the authorized camera inventory comes from:

    mock       local fixtures - no network at all, for tests and offline demos
    federated  the Traffic + Municipal department systems (default demo mode)
    official   a documented official export/API, only when authorized config
               is supplied. Never guesses an endpoint.

See docs/resource-integration.md.
"""
from __future__ import annotations

from ..config import Settings
from .base import (
    CameraResourceProvider,
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
)
from .federated import FederatedProvider, MunicipalVmsProvider, TrafficVmsProvider
from .mock import MockCameraResourceProvider
from .official import OfficialSentinelProvider

PROVIDER_MODES = ("mock", "federated", "official")


def build_provider(settings: Settings, *, adapters: dict | None = None) -> CameraResourceProvider:
    """Instantiate the provider named by SENTINEL_RESOURCE_MODE.

    `adapters` is the live adapter registry; the federated provider delegates
    to it rather than opening its own connections.
    """
    mode = (settings.sentinel_resource_mode or "federated").strip().lower()

    if mode == "mock":
        return MockCameraResourceProvider(settings)
    if mode == "official":
        return OfficialSentinelProvider(settings)
    if mode == "federated":
        return FederatedProvider(settings, adapters or {})

    raise ProviderNotConfigured(
        f"SENTINEL_RESOURCE_MODE must be one of {', '.join(PROVIDER_MODES)}; got '{mode}'",
        provider="unknown",
    )


__all__ = [
    "CameraResourceProvider",
    "FederatedProvider",
    "MockCameraResourceProvider",
    "MunicipalVmsProvider",
    "OfficialSentinelProvider",
    "PROVIDER_MODES",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderUnavailable",
    "TrafficVmsProvider",
    "build_provider",
]
