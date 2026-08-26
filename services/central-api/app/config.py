"""Central API configuration, roles, permissions and resource-provider mode.

Every credential and upstream URL arrives through the environment. Nothing here
is ever serialised to the browser: the dashboard talks to this service, and this
service talks to the department systems.

Phase 2 note: authorized video viewing is now supported, but it is *off* for
every role unless that role holds `video:live` / `video:playback` AND the
account's department/city scope matches the camera AND the camera's own policy
permits it. See `services/video_permissions.py` for the full check.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TRAFFIC_SOURCE = "traffic_vms"
MUNICIPAL_SOURCE = "municipal_vms"
#: The Gujarat Police Sentinel sandbox camera grid. A real, live, third-party
#: source - not a mock department - federated through the same adapter
#: contract as everything else. See docs/sentinel-grid.md.
GRID_SOURCE = "sentinel_grid"

TRAFFIC_DEPARTMENT = "Traffic Police"
MUNICIPAL_DEPARTMENT = "Municipal Corporation"
GRID_DEPARTMENT = "Sentinel Grid"

#: A user scoped to ALL_DEPARTMENTS / ALL_CITIES sees the whole federation.
ALL_DEPARTMENTS = "*"
ALL_CITIES = "*"


def normalize_city(value: str | None) -> str:
    """`  Ahmedabad ` -> `ahmedabad`. Used for case-insensitive filtering."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()


# ---------------------------------------------------------------------------
# Roles and permissions
# ---------------------------------------------------------------------------

class Role:
    STATE_ADMIN = "state_admin"
    STATE_REGISTRY_VIEWER = "state_registry_viewer"
    CITY_ADMIN = "city_admin"
    DEPARTMENT_ADMIN = "department_admin"
    TRAFFIC_OPERATOR = "traffic_operator"
    MUNICIPAL_OPERATOR = "municipal_operator"
    #: Watches the Sentinel sandbox grid. Live only - the grid keeps no
    #: archive, so no role can be given playback on it.
    GRID_OPERATOR = "grid_operator"
    HEALTH_MONITOR = "health_monitor"
    AUDITOR = "auditor"
    AI_OPERATOR = "ai_operator"
    VEHICLE_REGISTRY_VIEWER = "vehicle_registry_viewer"
    # Retained from Module 1 - the onboarding workflow still needs them.
    INSTALLATION_OPERATOR = "installation_operator"
    SYSTEM_ADMIN = "system_admin"


ALL_ROLES = [
    Role.STATE_ADMIN,
    Role.STATE_REGISTRY_VIEWER,
    Role.CITY_ADMIN,
    Role.DEPARTMENT_ADMIN,
    Role.TRAFFIC_OPERATOR,
    Role.MUNICIPAL_OPERATOR,
    Role.GRID_OPERATOR,
    Role.HEALTH_MONITOR,
    Role.AUDITOR,
    Role.AI_OPERATOR,
    Role.VEHICLE_REGISTRY_VIEWER,
    Role.INSTALLATION_OPERATOR,
    Role.SYSTEM_ADMIN,
]


class Permission:
    """Capability strings checked by the routers."""

    # onboarding
    INSTALLATION_CREATE = "installation:create"
    INSTALLATION_READ = "installation:read"
    INSTALLATION_UPDATE = "installation:update"
    INSTALLATION_SUBMIT = "installation:submit"
    INSTALLATION_SUSPEND = "installation:suspend"
    INSTALLATION_DECOMMISSION = "installation:decommission"
    INSTALLATION_SYNC = "installation:sync"

    # registry
    REGISTRY_READ = "registry:read"
    POLICY_READ = "policy:read"
    HEALTH_READ = "health:read"
    AUDIT_READ = "audit:read"

    # video - new in this phase. Holding these is necessary but NOT sufficient;
    # department/city scope and camera policy are checked separately.
    VIDEO_LIVE = "video:live"
    VIDEO_PLAYBACK = "video:playback"
    #: Ask another unit for access to one of its cameras.
    VIDEO_REQUEST_ACCESS = "video:request"
    #: Decide on requests made against your OWN unit's cameras.
    VIDEO_GRANT_ACCESS = "video:grant"

    # analytics
    DETECTION_READ = "detection:read"
    DETECTION_INGEST = "detection:ingest"
    #: Registration numbers are personal data, so reading them is its own
    #: permission rather than a side effect of reading detections.
    PLATE_READ = "plate:read"

    # vehicle reference registry - a separate permission on purpose, so that
    # holding it is a deliberate grant rather than a side effect of any role.
    VEHICLE_REGISTRY_READ = "vehicle:read"


#: Metadata-only baseline shared by most read roles.
_READ_ONLY = {
    Permission.REGISTRY_READ,
    Permission.POLICY_READ,
    Permission.HEALTH_READ,
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # Statewide metadata oversight. Video is NOT granted by default - a state
    # admin can see every camera exists without being able to watch any of them.
    # Set SENTINEL_STATE_ADMIN_VIDEO=true to grant it explicitly.
    Role.STATE_ADMIN: {
        *_READ_ONLY,
        Permission.INSTALLATION_READ,
        Permission.INSTALLATION_SYNC,
        Permission.INSTALLATION_SUSPEND,
        Permission.INSTALLATION_DECOMMISSION,
        Permission.AUDIT_READ,
        Permission.DETECTION_READ,
        # Holding these does NOT confer viewing: a central role is in
        # CENTRAL_OVERSIGHT_ROLES, so every camera reads as another unit's and
        # needs that unit's grant first.
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
    },
    Role.STATE_REGISTRY_VIEWER: {*_READ_ONLY},
    # City oversight. Video only when explicitly configured, same as state admin.
    Role.CITY_ADMIN: {
        *_READ_ONLY,
        Permission.INSTALLATION_READ,
        Permission.AUDIT_READ,
        Permission.DETECTION_READ,
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
    },
    Role.DEPARTMENT_ADMIN: {
        Permission.PLATE_READ,
        *_READ_ONLY,
        Permission.INSTALLATION_READ,
        Permission.INSTALLATION_SYNC,
        Permission.AUDIT_READ,
        Permission.DETECTION_READ,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_GRANT_ACCESS,
    },
    # Traffic gets plates: enforcement and incident follow-up is the reason
    # ANPR exists. Municipal does not - civic monitoring counts vehicles, it
    # does not identify their owners.
    Role.TRAFFIC_OPERATOR: {
        Permission.PLATE_READ,
        *_READ_ONLY,
        Permission.DETECTION_READ,
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_GRANT_ACCESS,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
        Permission.INSTALLATION_SUSPEND,
        Permission.INSTALLATION_DECOMMISSION,
    },
    Role.MUNICIPAL_OPERATOR: {
        *_READ_ONLY,
        Permission.DETECTION_READ,
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_GRANT_ACCESS,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
    },
    # The sandbox grid is a live-only feed with no recorded archive, so this
    # role deliberately holds VIDEO_LIVE and not VIDEO_PLAYBACK. The camera's
    # own capability list refuses playback as well - two independent gates,
    # because "there is no recording" should never depend on one of them.
    Role.GRID_OPERATOR: {
        *_READ_ONLY,
        Permission.DETECTION_READ,
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_LIVE,
    },
    # Health monitoring needs to know a camera is up, not what it is looking at.
    Role.HEALTH_MONITOR: {
        Permission.REGISTRY_READ,
        Permission.HEALTH_READ,
    },
    Role.AUDITOR: {
        Permission.PLATE_READ,
        *_READ_ONLY,
        Permission.VEHICLE_REGISTRY_READ,
        Permission.AUDIT_READ,
        Permission.INSTALLATION_READ,
        Permission.DETECTION_READ,
    },
    # Analytics: may run and ingest detections, and may open authorized
    # analytics sessions, but holds no onboarding or audit rights.
    Role.AI_OPERATOR: {
        Permission.REGISTRY_READ,
        Permission.HEALTH_READ,
        Permission.DETECTION_READ,
        Permission.DETECTION_INGEST,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
    },
    # Reads the vehicle reference table and nothing else. Deliberately holds
    # no registry, video, detection or audit permission: looking up a plate
    # should not come bundled with the ability to watch a camera.
    Role.VEHICLE_REGISTRY_VIEWER: {
        Permission.VEHICLE_REGISTRY_READ,
    },
    Role.INSTALLATION_OPERATOR: {
        *_READ_ONLY,
        Permission.INSTALLATION_CREATE,
        Permission.INSTALLATION_READ,
        Permission.INSTALLATION_UPDATE,
        Permission.INSTALLATION_SUBMIT,
    },
    # The platform account. It holds video because operating the broker means
    # being able to verify a feed end to end - "video is broken" is not
    # diagnosable from metadata. Being statewide, it can watch any camera
    # without asking a unit, which is a real concession: every session it
    # opens is audited like anyone else's, and that trail is the control.
    Role.SYSTEM_ADMIN: {
        Permission.PLATE_READ,
        *_READ_ONLY,
        Permission.VEHICLE_REGISTRY_READ,
        Permission.INSTALLATION_CREATE,
        Permission.INSTALLATION_READ,
        Permission.INSTALLATION_UPDATE,
        Permission.INSTALLATION_SUBMIT,
        Permission.INSTALLATION_SYNC,
        Permission.INSTALLATION_SUSPEND,
        Permission.INSTALLATION_DECOMMISSION,
        Permission.AUDIT_READ,
        Permission.DETECTION_READ,
        Permission.VIDEO_LIVE,
        Permission.VIDEO_PLAYBACK,
        Permission.VIDEO_REQUEST_ACCESS,
        Permission.VIDEO_GRANT_ACCESS,
    },
}


class Visibility:
    """How much of a camera's metadata a role may see."""

    FULL = "full"
    STANDARD = "standard"
    LIMITED = "limited"


ROLE_VISIBILITY: dict[str, str] = {
    Role.STATE_ADMIN: Visibility.FULL,
    Role.SYSTEM_ADMIN: Visibility.FULL,
    Role.DEPARTMENT_ADMIN: Visibility.FULL,
    Role.CITY_ADMIN: Visibility.FULL,
    Role.INSTALLATION_OPERATOR: Visibility.FULL,
    Role.TRAFFIC_OPERATOR: Visibility.STANDARD,
    Role.MUNICIPAL_OPERATOR: Visibility.STANDARD,
    Role.GRID_OPERATOR: Visibility.STANDARD,
    Role.AUDITOR: Visibility.STANDARD,
    Role.STATE_REGISTRY_VIEWER: Visibility.STANDARD,
    Role.AI_OPERATOR: Visibility.STANDARD,
    Role.VEHICLE_REGISTRY_VIEWER: Visibility.LIMITED,
    Role.HEALTH_MONITOR: Visibility.LIMITED,
}


class DemoUser(BaseModel):
    """A demo account.

    Scope is three-dimensional: department, city and (optionally) zone. A video
    request must satisfy all three, plus the camera's own policy.
    """

    username: str
    password: str
    display_name: str
    role: str
    department: str = ALL_DEPARTMENTS
    city: str = ALL_CITIES
    #: Empty list means "every zone within the permitted city".
    zones: list[str] = Field(default_factory=list)
    unit: str | None = None
    active: bool = True

    @property
    def permissions(self) -> set[str]:
        return ROLE_PERMISSIONS.get(self.role, set())

    @property
    def visibility(self) -> str:
        return ROLE_VISIBILITY.get(self.role, Visibility.LIMITED)

    @property
    def is_statewide(self) -> bool:
        return self.department == ALL_DEPARTMENTS

    @property
    def is_all_cities(self) -> bool:
        return self.city == ALL_CITIES

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def may_access_department(self, department: str | None) -> bool:
        if self.is_statewide:
            return True
        return department == self.department

    def may_access_city(self, city: str | None) -> bool:
        if self.is_all_cities:
            return True
        return normalize_city(city) == normalize_city(self.city)

    def may_access_zone(self, zone: str | None) -> bool:
        """An empty zone list means the account is not zone-restricted."""
        if not self.zones:
            return True
        if not zone:
            # A camera with no recorded zone cannot satisfy a zone restriction.
            return False
        return any(z.strip().lower() == str(zone).strip().lower() for z in self.zones)

    def may_access_source(self, source_system: str | None) -> bool:
        if self.is_statewide:
            return True
        return SOURCE_BY_DEPARTMENT.get(self.department) == source_system


#: Roles that oversee the federation rather than operate a unit's cameras.
#: They never *own* a camera, so footage always needs the owning operator's
#: approval - being statewide is a reason to ask more carefully, not a reason
#: to skip asking. Their statewide scope still gives them the whole REGISTRY;
#: it is only the footage that becomes a conversation.
CENTRAL_OVERSIGHT_ROLES = frozenset({
    Role.STATE_ADMIN,
    Role.CITY_ADMIN,
    Role.STATE_REGISTRY_VIEWER,
    Role.SYSTEM_ADMIN,
})


SOURCE_BY_DEPARTMENT = {
    TRAFFIC_DEPARTMENT: TRAFFIC_SOURCE,
    MUNICIPAL_DEPARTMENT: MUNICIPAL_SOURCE,
    GRID_DEPARTMENT: GRID_SOURCE,
}
DEPARTMENT_BY_SOURCE = {value: key for key, value in SOURCE_BY_DEPARTMENT.items()}

DEPARTMENT_CODES = {
    TRAFFIC_DEPARTMENT: "TRAFFIC",
    MUNICIPAL_DEPARTMENT: "MUNICIPAL",
    GRID_DEPARTMENT: "GRID",
}


DEFAULT_DEMO_USERS: list[dict[str, Any]] = [
    # --- statewide oversight ------------------------------------------------
    {
        "username": "state.admin",
        "password": "State@2026",
        "display_name": "State CCTV Administrator",
        "role": Role.STATE_ADMIN,
    },
    {
        "username": "registry.viewer",
        "password": "Registry@2026",
        "display_name": "State Registry Desk",
        "role": Role.STATE_REGISTRY_VIEWER,
    },
    {
        "username": "health.monitor",
        "password": "Health@2026",
        "display_name": "NOC Health Desk",
        "role": Role.HEALTH_MONITOR,
    },
    {
        "username": "vehicle.registry",
        "password": "Vehicle@2026",
        "display_name": "Vehicle Registry Desk",
        "role": Role.VEHICLE_REGISTRY_VIEWER,
    },
    {
        "username": "auditor",
        "password": "Auditor@2026",
        "display_name": "Internal Audit Cell",
        "role": Role.AUDITOR,
    },
    {
        "username": "system.admin",
        "password": "SysAdmin@2026",
        "display_name": "Sentinel Administrator",
        "role": Role.SYSTEM_ADMIN,
    },
    # --- city oversight -----------------------------------------------------
    {
        "username": "ahmedabad.cityadmin",
        "password": "City@2026",
        "display_name": "Ahmedabad City Administrator",
        "role": Role.CITY_ADMIN,
        "city": "Ahmedabad",
    },
    # --- department operations ---------------------------------------------
    {
        "username": "traffic.operator",
        "password": "Traffic@2026",
        "display_name": "R. Solanki (Traffic Control)",
        "role": Role.TRAFFIC_OPERATOR,
        "department": TRAFFIC_DEPARTMENT,
        "city": "Ahmedabad",
        "zones": ["Ahmedabad Traffic Zone 1", "Ahmedabad Traffic Zone 2"],
        "unit": "Ahmedabad Traffic Zone 1",
    },
    {
        "username": "traffic.zone3",
        "password": "Traffic@2026",
        "display_name": "M. Parmar (Traffic Zone 3)",
        "role": Role.TRAFFIC_OPERATOR,
        "department": TRAFFIC_DEPARTMENT,
        "city": "Ahmedabad",
        # Deliberately scoped to a zone with no cameras, to demonstrate that
        # zone scope is enforced independently of department and city.
        "zones": ["Ahmedabad Traffic Zone 3"],
        "unit": "Ahmedabad Traffic Zone 3",
    },
    {
        "username": "municipal.operator",
        "password": "Municipal@2026",
        "display_name": "A. Desai (Civic Control)",
        "role": Role.MUNICIPAL_OPERATOR,
        "department": MUNICIPAL_DEPARTMENT,
        "city": "Ahmedabad",
        "unit": "West Zone Civic Control Room",
    },
    {
        "username": "dept.admin",
        "password": "DeptAdmin@2026",
        "display_name": "P. Chauhan (Traffic HQ)",
        "role": Role.DEPARTMENT_ADMIN,
        "department": TRAFFIC_DEPARTMENT,
        "city": "Ahmedabad",
        "unit": "Ahmedabad Traffic Zone 1",
    },
    # Municipal's counterpart to dept.admin. Without it the Municipal
    # Corporation had no account of its own that could read its records whole
    # or withdraw its own cameras - only a statewide account could, which is
    # not what "the unit owns its cameras" is supposed to mean.
    {
        "username": "municipal.deptadmin",
        "password": "DeptAdmin@2026",
        "display_name": "S. Mehta (Civic HQ)",
        "role": Role.DEPARTMENT_ADMIN,
        "department": MUNICIPAL_DEPARTMENT,
        "unit": "Civic Headquarters",
    },
    # --- analytics ----------------------------------------------------------
    {
        "username": "ai.operator",
        "password": "AiOps@2026",
        "display_name": "Analytics Workbench",
        "role": Role.AI_OPERATOR,
        "department": TRAFFIC_DEPARTMENT,
        "city": "Ahmedabad",
    },
    # Each department runs its own edge worker; an analytics account is scoped
    # to the unit that owns the cameras it submits for, so detection ingest
    # cannot cross a departmental boundary.
    {
        "username": "municipal.ai",
        "password": "MuniOps@2026",
        "display_name": "Municipal Analytics Workbench",
        "role": Role.AI_OPERATOR,
        "department": MUNICIPAL_DEPARTMENT,
        "city": "Ahmedabad",
    },
    # --- statewide department control rooms --------------------------------
    # The federated cameras are state assets spread across nine districts, so
    # the accounts that watch them are scoped to their department statewide.
    # The city- and zone-limited accounts above are kept deliberately narrow:
    # they are what demonstrates that scope is enforced at all.
    {
        "username": "traffic.state",
        "password": "Traffic@2026",
        "display_name": "State Traffic Control Room",
        "role": Role.TRAFFIC_OPERATOR,
        "department": TRAFFIC_DEPARTMENT,
        "unit": "State Traffic Control Room",
    },
    {
        "username": "municipal.state",
        "password": "Municipal@2026",
        "display_name": "State Civic Control Room",
        "role": Role.MUNICIPAL_OPERATOR,
        "department": MUNICIPAL_DEPARTMENT,
        "unit": "State Civic Control Room",
    },
    {
        "username": "traffic.ai",
        "password": "AiOps@2026",
        "display_name": "Traffic Analytics Workbench (statewide)",
        "role": Role.AI_OPERATOR,
        "department": TRAFFIC_DEPARTMENT,
    },
    # --- onboarding (Module 1 workflow) ------------------------------------
    {
        "username": "traffic.installer",
        "password": "Install@2026",
        "display_name": "Traffic Installation Desk",
        "role": Role.INSTALLATION_OPERATOR,
        "department": TRAFFIC_DEPARTMENT,
        "city": "Ahmedabad",
        "unit": "Ahmedabad Traffic Zone 1",
    },
    {
        "username": "municipal.installer",
        "password": "Install@2026",
        "display_name": "Municipal Installation Desk",
        "role": Role.INSTALLATION_OPERATOR,
        "department": MUNICIPAL_DEPARTMENT,
        "city": "Ahmedabad",
        "unit": "West Zone Civic Control Room",
    },
]


class SourceSettings(BaseModel):
    """Registration record for one federated department system."""

    source_system: str
    display_name: str
    adapter: str
    base_url: str
    credential: str
    department: str
    default_district: str
    department_code: str
    default_city: str = "Ahmedabad"
    #: A source Sentinel may only read. The grid is consume-only by its own
    #: rules ("do not push streams to any path, and do not call the gateway's
    #: control API"), so its adapter refuses every write rather than
    #: attempting one and being rejected upstream.
    read_only: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- platform -------------------------------------------------------
    environment_label: str = "DEMO / PHASE 2"
    service_name: str = "sentinel-central-api"
    service_version: str = "0.3.0"
    display_timezone: str = "Asia/Kolkata"

    # --- storage --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel"

    # --- identity -------------------------------------------------------
    jwt_secret: str = "sentinel-phase2-demo-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 720
    demo_users_json: str = ""

    # --- federation -----------------------------------------------------
    #: The two demo department systems carry synthetic cameras. Turn them off
    #: to run the platform against real sources only - the registry then shows
    #: nothing invented. They stay in the tree because the cross-unit grant
    #: flow (request -> grant -> revoke) needs a second department to
    #: demonstrate at all, and the test-suite exercises them.
    #: Off by default: the platform should not invent cameras. Turn these on
    #: only to demonstrate the cross-unit grant flow, which needs a second
    #: department to exist at all.
    traffic_vms_enabled: bool = False
    municipal_vms_enabled: bool = False

    traffic_vms_base_url: str = "http://traffic-vms:8001"
    traffic_vms_api_key: str = "traffic-demo-key"
    traffic_vms_department: str = TRAFFIC_DEPARTMENT
    traffic_vms_district: str = "Ahmedabad"
    traffic_vms_city: str = "Ahmedabad"

    municipal_vms_base_url: str = "http://municipal-vms:8002"
    municipal_vms_token: str = "municipal-demo-token"
    municipal_vms_department: str = MUNICIPAL_DEPARTMENT
    municipal_vms_district: str = "Ahmedabad"
    municipal_vms_city: str = "Ahmedabad"

    # --- Sentinel sandbox camera grid ------------------------------------
    #: The live grid described at https://sentinel.gujarat.gov.in/resource.
    #: Unlike the two demo departments this is a real upstream, so every rule
    #: from that guide is enforced in code - see docs/sentinel-grid.md.
    sentinel_grid_enabled: bool = True
    sentinel_grid_base_url: str = "https://live.corp8.cloud"
    sentinel_grid_department: str = GRID_DEPARTMENT
    sentinel_grid_district: str = "Gujarat"
    sentinel_grid_city: str = "Gujarat"
    #: The catalogue is the contract, so it is re-read rather than cached
    #: forever - but "pace your load" means not re-reading it per request.
    sentinel_grid_catalogue_ttl_seconds: int = 30
    #: rtsp | hls. Which transport the EDGE WORKER prefers. Browser preview is
    #: always HLS; RTSP is not a browser protocol.
    sentinel_grid_capture_protocol: str = "rtsp"
    #: UDP is accepted upstream but fails across NAT and most corporate
    #: firewalls, and partial delivery produces corrupt frames that look like
    #: model bugs. Never change this without a very specific reason.
    sentinel_grid_rtsp_transport: str = "tcp"
    #: Seconds to wait for RTSP before falling back to HLS. The guide sanctions
    #: HLS explicitly when port 8554 is blocked on the client's network.
    sentinel_grid_rtsp_probe_seconds: float = 8.0
    sentinel_grid_hls_fallback: bool = True

    upstream_timeout_seconds: float = 6.0
    upstream_retries: int = 1

    # --- camera resource provider ---------------------------------------
    #: mock | federated | official
    #:   mock      - local fixtures, no network beyond the demo containers
    #:   federated - the Traffic + Municipal department systems (default demo)
    #:   official  - a documented official export/API; requires the two settings
    #:               below and refuses to guess an endpoint otherwise.
    sentinel_resource_mode: str = "federated"
    sentinel_resource_api_url: str = ""
    sentinel_resource_api_token: str = ""
    sentinel_resource_timeout_seconds: int = 20

    # --- video ----------------------------------------------------------
    video_enabled: bool = True
    video_live_session_seconds: int = 300
    video_playback_session_seconds: int = 900
    #: Longest historical window a single playback session may request.
    video_playback_max_window_minutes: int = 60
    #: How long a granted cross-unit access lasts before it must be asked for
    #: again. Standing permissions nobody revisits are how scope creeps.
    video_grant_default_days: int = 7
    #: How long a registration number is kept. Shorter than the detection it
    #: rides on, because the plate is the identifying part.
    anpr_plate_retention_days: int = 30
    #: Statewide/city admins get metadata by default; flip these to grant video.
    sentinel_state_admin_video: bool = False
    sentinel_city_admin_video: bool = False

    # --- YOLO / analytics -----------------------------------------------
    yolo_enable: bool = False
    yolo_model_name: str = "yolo11n.pt"
    yolo_confidence_threshold: float = 0.45
    yolo_device: str = "auto"
    yolo_frame_sample_interval: int = 5
    detection_retention_hours: int = 168

    # --- vehicle reference registry --------------------------------------
    vehicle_registry_enabled: bool = True
    vehicle_registry_path: str = "/app/reference/vehicle_registry.json"
    #: Import refuses any record carrying an owner-identifying field.
    vehicle_registry_forbidden_fields: str = (
        "owner_name,owner,name,father_name,address,permanent_address,phone,mobile,"
        "email,aadhaar,chassis_number,engine_number,insurance_policy_number"
    )

    # --- behaviour ------------------------------------------------------
    health_poll_interval_seconds: int = 20
    health_monitor_enabled: bool = True
    health_retention_hours: int = 6
    auto_sync_on_startup: bool = True
    cors_origins: str = "*"

    @property
    def demo_users(self) -> list[DemoUser]:
        raw: Any = DEFAULT_DEMO_USERS
        if self.demo_users_json.strip():
            raw = json.loads(self.demo_users_json)
        return [DemoUser(**entry) for entry in raw]

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sources(self) -> list[SourceSettings]:
        """The federated department registry.

        Adding a third department system is one entry here plus one adapter
        class - nothing in the routers or the dashboard changes.
        """
        entries: list[SourceSettings] = []
        if self.traffic_vms_enabled:
            entries.append(
                SourceSettings(
                    source_system=TRAFFIC_SOURCE,
                    display_name="Traffic VMS",
                    adapter="traffic_adapter",
                    base_url=self.traffic_vms_base_url,
                    credential=self.traffic_vms_api_key,
                    department=self.traffic_vms_department,
                    default_district=self.traffic_vms_district,
                    department_code="TRAFFIC",
                    default_city=self.traffic_vms_city,
                )
            )
        if self.municipal_vms_enabled:
            entries.append(
                SourceSettings(
                    source_system=MUNICIPAL_SOURCE,
                    display_name="Municipal VMS",
                    adapter="municipal_adapter",
                    base_url=self.municipal_vms_base_url,
                    credential=self.municipal_vms_token,
                    department=self.municipal_vms_department,
                    default_district=self.municipal_vms_district,
                    department_code="MUNICIPAL",
                    default_city=self.municipal_vms_city,
                )
            )
        if self.sentinel_grid_enabled:
            entries.append(
                SourceSettings(
                    source_system=GRID_SOURCE,
                    display_name="Sentinel Camera Grid",
                    adapter="grid_adapter",
                    base_url=self.sentinel_grid_base_url,
                    # The grid catalogue is unauthenticated. There is no
                    # credential to hold, and inventing one would be theatre.
                    credential="",
                    department=self.sentinel_grid_department,
                    default_district=self.sentinel_grid_district,
                    department_code="GRID",
                    default_city=self.sentinel_grid_city,
                    read_only=True,
                )
            )
        return entries

    def source_for_department(self, department: str) -> SourceSettings | None:
        for source in self.sources:
            if source.department == department:
                return source
        return None

    def source_for_system(self, source_system: str) -> SourceSettings | None:
        """Which configured source a camera actually arrived through.

        `source_system` is the authoritative link, not the department: one
        gateway can federate assets owned by several departments (the Sentinel
        grid carries both Traffic Police and Municipal Corporation cameras), so
        resolving by department would pick the wrong adapter and ask the wrong
        system for the feed.
        """
        for source in self.sources:
            if source.source_system == source_system:
                return source
        return None

    @property
    def official_provider_configured(self) -> bool:
        """True only when someone has supplied authorized official access.

        Deliberately conservative: no default URL, no guessed endpoint. If this
        is False the official provider refuses with SOURCE_ACCESS_NOT_CONFIGURED
        rather than attempting any request.
        """
        return bool(self.sentinel_resource_api_url.strip() and self.sentinel_resource_api_token.strip())

    def role_video_opt_in(self, role: str) -> bool:
        """Roles that hold video only because a deployment switched it on.

        State and city admins are metadata-first by design: broad oversight
        accounts should not silently become viewing accounts. They carry no
        video permission in ROLE_PERMISSIONS, so this flag is the only thing
        that can give them one.
        """
        if role == Role.STATE_ADMIN:
            return self.sentinel_state_admin_video
        if role == Role.CITY_ADMIN:
            return self.sentinel_city_admin_video
        return False

    def role_grants_video(self, role: str) -> bool:
        """Whether a role may hold video at all, before scope checks."""
        if self.role_video_opt_in(role):
            return True
        # Oversight roles are no longer refused here. They now carry the video
        # permissions but sit in CENTRAL_OVERSIGHT_ROLES, so video_permissions
        # routes them through the owning unit's approval on every camera. The
        # grant is the control, which is stronger than a deployment flag.
        permissions = ROLE_PERMISSIONS.get(role, set())
        return Permission.VIDEO_LIVE in permissions or Permission.VIDEO_PLAYBACK in permissions


@lru_cache
def get_settings() -> Settings:
    return Settings()
