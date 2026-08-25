"""Adapter registry.

Registering a new department system is a two-line change: add the class here
and add a SourceSettings entry in config.Settings.sources. No router, service or
dashboard code needs to know the new department exists.
"""
from __future__ import annotations

import httpx

from ..config import Settings
from .base import (
    AdapterError,
    ResourceNotFoundError,
    SourceAuthError,
    SourceConflictError,
    SourceRateLimitedError,
    SourceTimeoutError,
    SourceUnavailableError,
    SourceValidationError,
    SurveillanceAdapter,
    UpstreamProtocolError,
)
from .municipal_adapter import MunicipalAdapter
from .traffic_adapter import TrafficAdapter

ADAPTER_REGISTRY: dict[str, type[SurveillanceAdapter]] = {
    "traffic_adapter": TrafficAdapter,
    "municipal_adapter": MunicipalAdapter,
}


def build_adapters(
    settings: Settings,
    *,
    clients: dict[str, httpx.AsyncClient] | None = None,
) -> dict[str, SurveillanceAdapter]:
    """Instantiate one adapter per registered source system.

    `clients` lets the test-suite inject an httpx transport that talks to the
    mock services in-process, or one that always fails, without patching.
    """
    adapters: dict[str, SurveillanceAdapter] = {}
    for source in settings.sources:
        adapter_cls = ADAPTER_REGISTRY.get(source.adapter)
        if adapter_cls is None:
            raise KeyError(f"No adapter registered under '{source.adapter}'")
        adapters[source.source_system] = adapter_cls(
            source,
            timeout=settings.upstream_timeout_seconds,
            retries=settings.upstream_retries,
            client=(clients or {}).get(source.source_system),
        )
    return adapters


async def close_adapters(adapters: dict[str, SurveillanceAdapter]) -> None:
    for adapter in adapters.values():
        await adapter.aclose()


__all__ = [
    "ADAPTER_REGISTRY",
    "AdapterError",
    "MunicipalAdapter",
    "ResourceNotFoundError",
    "SourceAuthError",
    "SourceConflictError",
    "SourceRateLimitedError",
    "SourceTimeoutError",
    "SourceUnavailableError",
    "SourceValidationError",
    "SurveillanceAdapter",
    "TrafficAdapter",
    "UpstreamProtocolError",
    "build_adapters",
    "close_adapters",
]
