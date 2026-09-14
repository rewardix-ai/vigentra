"""The Traffic Police's own VMS federates beside the grid for the own-feed demo.

The grid gateway is read-only - no installation register, no recording - so a
camera can only be onboarded through the form into a department system that
keeps one. Enabling the local VMS adds it as a third source, and installation
forms for Traffic Police must go to it rather than to the grid.
"""
from __future__ import annotations


def _settings(**overrides):
    from app.config import Settings

    return Settings(traffic_vms_adapter="grid_adapter", municipal_vms_adapter="grid_adapter", **overrides)


def _adapters(settings) -> dict:
    return {s.source_system: object() for s in settings.sources}


def test_the_local_vms_is_a_third_source_and_takes_installation_forms():
    from app.config import TRAFFIC_DEPARTMENT, TRAFFIC_LOCAL_SOURCE
    from app.services.installation_service import adapter_for_department

    settings = _settings(traffic_local_vms_enabled=True)

    local = [s for s in settings.sources if s.source_system == TRAFFIC_LOCAL_SOURCE]
    assert len(local) == 1
    assert local[0].adapter == "traffic_adapter" and local[0].department == TRAFFIC_DEPARTMENT
    assert adapter_for_department(_adapters(settings), settings, TRAFFIC_DEPARTMENT)[0] == TRAFFIC_LOCAL_SOURCE


def test_without_it_forms_go_to_the_departments_only_source():
    from app.config import TRAFFIC_DEPARTMENT, TRAFFIC_LOCAL_SOURCE, TRAFFIC_SOURCE
    from app.services.installation_service import adapter_for_department

    settings = _settings(traffic_local_vms_enabled=False)

    assert TRAFFIC_LOCAL_SOURCE not in {s.source_system for s in settings.sources}
    assert adapter_for_department(_adapters(settings), settings, TRAFFIC_DEPARTMENT)[0] == TRAFFIC_SOURCE
