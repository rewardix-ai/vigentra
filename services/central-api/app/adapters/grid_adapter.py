"""Adapter for the Sentinel sandbox camera grid.

Unlike the two demo department systems, this one is a REAL upstream: the live
grid documented at https://sentinel.gujarat.gov.in/resource and served from
`live.corp8.cloud`. Everything the integrator's guide asks of a client is
enforced here or in the video adapter beside it. See docs/sentinel-grid.md.

Two things make this adapter different from the department adapters:

*Read-only.* The grid publishes a catalogue and streams; it has no installation
register and its own rules say "consume only - do not push streams to any path,
and do not call the gateway's control API". So every write in the adapter
contract refuses locally rather than attempting a request that should not be
made. `list_installation_requests` returns [] because the register genuinely
does not exist, not because a call failed.

*Live-only.* The grid has no archive, no seeking and no byte-range fetching, so
cameras advertise the `live` capability and never `playback`. A playback
request is refused by capability, independently of any role check.

The catalogue is the contract, the URL pattern is not: camera ids and the set
of cameras change, so `/api/ingest` is re-read (behind a short TTL, because
"pace your load" applies to the control plane too) rather than hard-coded.
Per-camera codec, resolution and frame rate come from the catalogue as well -
the grid is deliberately not uniform.

Coordinates, view direction and camera class are NOT published by the grid.
They come from `reference/grid_cameras.json`, compiled by surveying the feeds,
and each record carries the confidence of its own coordinate so an estimate is
never silently presented as a survey.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import SourceSettings
from ..schemas import (
    CameraMetadata,
    CameraStatus,
    Event,
    InstallationForm,
    InstallationFormPatch,
    InstallationPurpose,
    InstallationRequestOut,
    InstallationStatus,
    RequestStatus,
    SourceType,
)
from ..services import normalization as norm
from .base import (
    AdapterError,
    SourceAuthError,
    ResourceNotFoundError,
    SourceConflictError,
    SourceUnavailableError,
    SurveillanceAdapter,
    UpstreamProtocolError,
)

logger = logging.getLogger("vigentra.adapter.grid")

#: Where the surveyed reference lives inside the image. Overridable so the
#: test-suite and a local run can point at the repo copy.
REFERENCE_PATH = os.getenv(
    "SENTINEL_GRID_REFERENCE", "/app/reference/grid_cameras.json"
)

_CAMERA_TYPES = {
    "ptz": "ptz",
    "dome": "dome",
    "bullet": "bullet",
    "fixed": "fixed",
    # The surveyed data records ANPR capability as a camera class; the canonical
    # schema has no such type, so it lands as fixed and the capability is
    # carried in the purpose and coverage text instead.
    "anpr-capable": "fixed",
}

_PURPOSES = {
    "traffic monitoring": InstallationPurpose.TRAFFIC_MONITORING,
    "junction monitoring": InstallationPurpose.TRAFFIC_MONITORING,
    "highway monitoring": InstallationPurpose.TRAFFIC_MONITORING,
    "public safety": InstallationPurpose.PUBLIC_SAFETY,
}


def _load_reference() -> dict[str, dict[str, Any]]:
    """Surveyed per-camera facts, keyed by the grid's own camera id.

    Missing or malformed reference data is not fatal: the grid still federates,
    the cameras simply arrive without coordinates. A registry that is missing
    map pins is far better than a registry that will not load.
    """
    try:
        with open(REFERENCE_PATH, encoding="utf-8") as handle:
            rows = json.load(handle)
    except FileNotFoundError:
        logger.warning(
            "grid reference file %s not found - cameras will federate without "
            "coordinates or view direction",
            REFERENCE_PATH,
        )
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("grid reference file %s unreadable: %s", REFERENCE_PATH, exc)
        return {}

    # Accept the flat list this adapter expects, and also the wrapped
    # {"cameras": [...]} survey export, because the two live side by side in
    # data/reference and handing over the wrong one should degrade to "no
    # coordinates" rather than crash the whole sync.
    if isinstance(rows, dict):
        rows = rows.get("cameras", [])
    if not isinstance(rows, list):
        logger.warning(
            "grid reference file %s is not a camera list - ignoring", REFERENCE_PATH
        )
        return {}

    reference: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Keyed by the grid's id first. `camera_no` is the survey's own
        # numbering and the two stopped agreeing when the grid renumbered:
        # cam21 is survey number 23, and cam24-cam30 are cameras the survey
        # never saw. Both keys are registered so a reference row still finds
        # its camera whichever numbering the catalogue is using today.
        for key in (row.get("grid_id"), row.get("camera_no"), row.get("id")):
            if key:
                reference.setdefault(str(key), row)
    if not reference:
        logger.warning("grid reference file %s held no usable rows", REFERENCE_PATH)
    return reference


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> str | None:
    """Blank out the reference file's explicit 'not probed' placeholders."""
    text = str(value or "").strip()
    if not text or text.lower() in ("not probed", "unknown", "none"):
        return None
    return text


class _GridGate:
    """One sign-in, shared by everything that uses this client.

    The gateway keeps a single session per account, so concurrent logins fight:
    the winner is whoever logged in last, and the losers get 403. This
    serialises sign-in behind a lock and gives every caller the same answer,
    and it re-authenticates at most once per rejection generation - so two
    adapters that both hit 403 at once cause one new login between them rather
    than two that invalidate each other.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._lock = asyncio.Lock()
        self._authenticated = False
        #: Bumped on every successful sign-in. A caller that failed at
        #: generation N and finds the generation already past N knows someone
        #: else has re-authenticated and it need only retry, not log in again.
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    async def ensure(
        self, *, source_system: str, credential: str, identity: str
    ) -> None:
        if self._authenticated:
            return
        async with self._lock:
            if self._authenticated:
                return
            await self._login(source_system, credential, identity)

    async def reauthenticate(
        self, *, source_system: str, credential: str, identity: str, seen_generation: int
    ) -> None:
        """Sign in again after a rejection, unless someone already did.

        `seen_generation` is what the caller last saw. If the live generation
        has moved past it, a concurrent caller has already refreshed the
        session and this one should simply retry its request.
        """
        async with self._lock:
            if self._generation != seen_generation:
                return
            self._authenticated = False
            await self._login(source_system, credential, identity)

    async def _login(self, source_system: str, credential: str, identity: str) -> None:
        # The form grew a second field. It took a password alone and now takes
        # a registered address alongside it; the address is omitted when none
        # is configured, so a gateway still running the older form is unchanged.
        form = {"password": credential}
        if identity:
            form["email"] = identity

        try:
            response = await self._client.post(
                "/auth/login", data=form, follow_redirects=False
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a source failure
            raise UpstreamProtocolError(
                "Could not reach the grid sign-in endpoint",
                source_system=source_system,
                detail=type(exc).__name__,
            ) from exc

        if response.status_code >= 400:
            raise UpstreamProtocolError(
                "The grid rejected Vigentra's sign-in",
                source_system=source_system,
                detail=str(response.status_code),
            )

        # A cookie is the only proof it worked. A REJECTED sign-in comes back
        # HTTP 200 with the sign-in page again, so status alone marked the
        # adapter authenticated over a session that did not exist.
        if not any(cookie for cookie in self._client.cookies.jar):
            raise UpstreamProtocolError(
                "The grid accepted the sign-in request but issued no session "
                "cookie, which means the credential was refused. Check "
                "SENTINEL_GRID_EMAIL and SENTINEL_GRID_PASSWORD.",
                source_system=source_system,
                detail=f"HTTP {response.status_code}, no Set-Cookie",
            )
        self._authenticated = True
        self._generation += 1


class GridAdapter(SurveillanceAdapter):
    """Read-only adapter over the Sentinel grid's public catalogue."""

    adapter_name = "sentinel_grid_adapter"
    adapter_version = "1.0.0"
    #: The grid has no /health; the catalogue doubles as the liveness probe.
    health_path = "/cameras.json"

    def __init__(self, config: SourceSettings, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._reference = _load_reference()
        self._authenticated = False
        self._cache: list[dict[str, Any]] | None = None
        self._cache_at = 0.0
        self._cache_ttl = 30.0
        #: How long a catalogue may be served after the grid stops answering.
        #: The set of cameras changes over days, not minutes, so a five-minute
        #: old list is still a true statement about the estate - and far more
        #: use than marking thirty cameras offline because one request to a
        #: shared sandbox on the public internet was slow.
        self._stale_ttl = 300.0
        self._stale = False

    # -- access ------------------------------------------------------------

    def _gate(self) -> "_GridGate | None":
        """The one sign-in coordinator for this client.

        The gateway allows a SINGLE active session per account: a second
        sign-in silently invalidates the first. Both department adapters and
        the media client use the one shared grid account, so three independent
        logins left only the last alive and the other two - including the
        client that plays video - answering 403 on every read.

        The coordinator is attached to the httpx client rather than to the
        adapter, so every consumer handed the same client shares one login,
        one cookie jar and one re-auth. Sharing the client is what makes the
        estate reachable; the state has to live where the sharing does.
        """
        client = self._client
        if client is None:
            return None
        gate = getattr(client, "_grid_gate", None)
        if gate is None:
            gate = _GridGate(client)
            client._grid_gate = gate  # type: ignore[attr-defined]
        return gate

    async def _ensure_session(self) -> None:
        """Sign in once for everyone sharing this client."""
        if not self.config.credential:
            return
        gate = self._gate()
        if gate is None:
            return
        await gate.ensure(
            source_system=self.source_system,
            credential=self.config.credential,
            identity=self.config.credential_identity,
        )

    # -- catalogue ---------------------------------------------------------

    async def _catalogue(self) -> list[dict[str, Any]]:
        """The camera list, re-read on a short TTL.

        Re-reading matters because the guide is explicit that the set of
        cameras and their properties change. The TTL matters because each read
        is a real request to a shared sandbox - "pace your load" is not only
        about video.
        """
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_at) < self._cache_ttl:
            return self._cache

        # Two catalogue shapes are accepted: a bare list of {id, name}, and
        # the older {"cameras": [...]} carrying codec, resolution and stream
        # URLs per entry. The guide is explicit that the catalogue is the
        # contract and its URL pattern is not, so pinning to one shape turns
        # the next migration into a reported outage.
        try:
            await self._ensure_session()
            gate = self._gate()
            seen = gate.generation if gate is not None else 0
            try:
                payload = await self._request("GET", "/cameras.json", authenticated=True)
            except SourceAuthError:
                # The session was invalidated - most often because another
                # consumer of this account signed in, which the gateway allows
                # only one of. Re-authenticate once (a no-op if someone already
                # did) and read again.
                if gate is None or not self.config.credential:
                    raise
                await gate.reauthenticate(
                    source_system=self.source_system,
                    credential=self.config.credential,
                    identity=self.config.credential_identity,
                    seen_generation=seen,
                )
                payload = await self._request("GET", "/cameras.json", authenticated=True)
        except AdapterError as exc:
            # Serve the last good catalogue rather than propagating a blip.
            #
            # The health monitor probes every 20s and the wall re-reads on
            # every page load, so a single slow response from a shared sandbox
            # used to blank the registry and post a red banner that stayed up
            # until the next successful poll. Falling back keeps the registry
            # true for as long as the last answer can be trusted, and lets the
            # error through once it cannot.
            if self._cache is not None and (now - self._cache_at) < self._stale_ttl:
                logger.warning(
                    "grid catalogue unavailable (%s); serving the copy read %.0fs ago",
                    exc,
                    now - self._cache_at,
                )
                self._stale = True
                return self._cache
            gate = self._gate()
            if gate is not None:
                gate._authenticated = False  # force a fresh sign-in next pass
            raise
        if isinstance(payload, dict):
            payload = payload.get("cameras", [])
        if not isinstance(payload, list):
            raise UpstreamProtocolError(
                "The grid catalogue was neither a camera list nor {'cameras': [...]}",
                source_system=self.source_system,
                detail=type(payload).__name__,
            )
        cameras = [c for c in payload if isinstance(c, dict) and c.get("id")]
        self._cache = cameras
        self._cache_at = now
        self._stale = False
        return cameras

    def _to_camera(self, record: dict[str, Any]) -> CameraMetadata:
        grid_id = str(record["id"])
        ref = self._reference.get(grid_id, {})
        external_id = f"GRID-{int(grid_id):03d}" if grid_id.isdigit() else f"GRID-{grid_id}"

        district = _clean(ref.get("district")) or self.config.default_district
        # The catalogue's own location string is operator shorthand and is
        # sometimes wrong (camera 6 is labelled Junagadh but its burned-in
        # overlay and its geocoded landmark both say Bhavnagar). The surveyed
        # site name wins where we have one.
        name = _clean(ref.get("site_name")) or str(record.get("location") or record.get("name"))

        # An absent flag means "assume live", not "assume dead": the current
        # catalogue is {id, name} and nothing else, and defaulting to dead
        # publishes every camera with no `live` capability - which reads as
        # "the owner has not enabled video" and empties the live wall over a
        # field that is simply not sent. The capture attempt is the real
        # liveness test and reports its own failure.
        live = (
            str(record.get("status") or "").lower() == "live"
            or bool(record.get("live", True))
        )
        width, height = _as_int(record.get("width")), _as_int(record.get("height"))
        resolution = f"{width}x{height}" if width and height else None
        codec_raw = str(record.get("codec") or "").lower()
        codec = {"h264": "H.264", "hevc": "H.265"}.get(codec_raw) or None

        confidence = _clean(ref.get("geo_confidence"))
        coverage = _clean(ref.get("geo_source"))
        facing_basis = _clean(ref.get("facing_basis"))

        # Custody is recorded per camera, not per source. The grid is one
        # gateway in front of assets that belong to different departments, and
        # the federation's whole access model turns on who owns a camera - so
        # a roadside junction sits with Traffic Police and a panchayat street
        # with the Municipal Corporation, exactly as they would in the field.
        department = _clean(ref.get("owning_department")) or self.config.department
        dept_code = _clean(ref.get("department_code")) or self.config.department_code
        source_system = _clean(ref.get("source_system")) or self.source_system

        return CameraMetadata(
            camera_id=norm.make_camera_id(dept_code, district, external_id),
            external_camera_id=external_id,
            source_system=source_system,
            # No installation register upstream, so nothing to reference. This
            # is also what stops sync_service trying to acknowledge a record.
            installation_request_id=None,
            name=name,
            vendor=None,
            model=None,
            camera_type=norm.normalize_camera_type(
                _CAMERA_TYPES.get(str(ref.get("camera_type") or "").lower(), "fixed")
            ),
            installation_purpose=_PURPOSES.get(
                str(ref.get("installation_purpose") or "").lower(),
                InstallationPurpose.PUBLIC_SAFETY,
            ),
            camera_serial_masked=None,
            owning_department=department,
            department_code=dept_code,
            owning_unit=f"{district} {dept_code.title()} Unit",
            police_station_or_zone=f"{district} Control Room",
            city=district,
            zone=None,
            district=district,
            road_or_junction=_clean(ref.get("road_or_junction")) or name,
            landmark=_clean(record.get("location")),
            latitude=_as_float(ref.get("latitude")),
            longitude=_as_float(ref.get("longitude")),
            view_direction=norm.normalize_direction(ref.get("facing")),
            # Coverage carries the provenance of the coordinate, so an operator
            # reading the camera page sees how much to trust the map pin.
            coverage_description=(
                f"Coordinate confidence: {confidence}. {coverage}" if confidence else coverage
            ),
            entry_exit_zone_description=(
                f"View direction basis: {facing_basis}" if facing_basis else None
            ),
            source_type=SourceType.RTSP,
            vms_name="Sentinel Camera Grid",
            vms_vendor="MediaMTX",
            resolution=resolution,
            fps=_as_int(record.get("fps")),
            codec=codec,
            # The grid keeps no archive at all - not a short one, none.
            retention_days=None,
            timezone_name="Asia/Kolkata",
            installation_date=None,
            commissioning_date=None,
            installation_status=InstallationStatus.COMMISSIONED,
            request_status=RequestStatus.SYNCHRONIZED,
            approved_by_role=None,
            approved_at=None,
            local_video_access=live,
            permitted_local_roles=["grid_operator"],
            # live only, never playback: there is nothing recorded to play back.
            capabilities=norm.build_capabilities(
                live=live, playback=False, commissioned=True
            ),
            health_status=CameraStatus.ONLINE if live else CameraStatus.OFFLINE,
            last_frame_utc=datetime.now(timezone.utc) if live else None,
            reconnect_count=None,
            source_synced_at=datetime.now(timezone.utc),
            provenance={
                **self.provenance,
                # The record is federated by the grid gateway but owned by the
                # department above; keeping both makes the distinction auditable.
                "federated_via": self.source_system,
                "grid_camera_id": grid_id,
                "catalogue_label": record.get("location"),
                "geo_confidence": confidence,
                "surveyed": bool(ref),
            },
        )

    # ----------------------------------------------------------------------
    # Contract: camera metadata
    # ----------------------------------------------------------------------

    async def list_approved_cameras(self) -> list[CameraMetadata]:
        """Only the cameras this source is the federating system for.

        One physical gateway now sits behind two department systems: Traffic
        Police federates its junctions, the Municipal Corporation its civic
        cameras. Each adapter instance is registered under one `source_system`
        and must return only the cameras carrying it, or both would claim all
        thirty and the second sync would rewrite the first's custody.

        A camera with no `source_system` in the reference falls back to this
        adapter's own, which keeps a single-source deployment working
        unchanged.
        """
        cameras = [self._to_camera(record) for record in await self._catalogue()]
        return [camera for camera in cameras if camera.source_system == self.source_system]

    async def check_source_health(self) -> dict[str, Any]:
        """Liveness via the catalogue this adapter already keeps.

        The inherited probe makes its own unauthenticated request to
        `health_path` every polling interval. Against this gateway that is
        wrong twice over: the sandbox is behind a sign-in, so an
        unauthenticated read is answered with the login page rather than the
        catalogue, and a probe every 20s on top of the reads the registry
        already makes is exactly the load the integrator's guide asks clients
        not to generate.

        Going through `_catalogue()` reuses the session, honours the TTL, and
        counts a catalogue served from cache as reachable - which it is: the
        cameras are there and the last read succeeded.
        """
        started = time.perf_counter()
        try:
            cameras = await self._catalogue()
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
            "upstream": {
                "cameras": len(cameras),
                "catalogue_age_seconds": round(time.monotonic() - self._cache_at, 1),
                "stale": self._stale,
            },
        }

    async def warm(self) -> None:
        """Read the catalogue once at startup, failure tolerated.

        The first connection to this gateway pays TLS setup and a sign-in and
        can take half a minute; every one after it is sub-second. Paying that
        in the background at boot means the first operator to open the registry
        does not pay it instead, and a cold start no longer looks like an
        outage.
        """
        try:
            await self._catalogue()
        except AdapterError as exc:
            logger.warning("grid catalogue could not be pre-warmed: %s", exc)

    async def get_camera_health(self, external_camera_id: str) -> dict[str, Any]:
        """Health straight off the catalogue's own live flag.

        The catalogue is not always right - a camera can report `live` while
        its playlist returns HTTP 500 - so the detail block says where the
        claim came from rather than presenting it as measured.
        """
        wanted = external_camera_id.upper()
        for record in await self._catalogue():
            grid_id = str(record["id"])
            candidate = f"GRID-{int(grid_id):03d}" if grid_id.isdigit() else f"GRID-{grid_id}"
            if candidate.upper() == wanted:
                # Accept either spelling the catalogue has used, so the
                # adapter does not depend on which endpoint answered.
                live = (
                    str(record.get("status") or "").lower() == "live"
                    or bool(record.get("live", True))
                )
                now = datetime.now(timezone.utc)
                return {
                    "status": CameraStatus.ONLINE if live else CameraStatus.OFFLINE,
                    "last_heartbeat_utc": now,
                    "last_frame_utc": now if live else None,
                    "latency_ms": None,
                    "reconnect_count": None,
                    "detail": {
                        "source": "grid catalogue",
                        "claimed_live": live,
                        "note": (
                            "Catalogue status is the grid's own claim and is not "
                            "a probe of the stream itself."
                        ),
                    },
                }
        raise ResourceNotFoundError(
            f"The grid catalogue has no camera '{external_camera_id}'",
            source_system=self.source_system,
        )

    async def fetch_events(
        self,
        external_camera_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Event]:
        """The grid publishes no event feed. Empty, not an error."""
        return []

    # ----------------------------------------------------------------------
    # Contract: installation register - none of it exists upstream
    # ----------------------------------------------------------------------

    def _read_only(self, action: str) -> AdapterError:
        return SourceConflictError(
            f"The Sentinel grid is a read-only source; it cannot {action}. "
            "The grid is consumed live and its gateway control API is not called.",
            source_system=self.source_system,
        )

    async def list_installation_requests(self) -> list[InstallationRequestOut]:
        # Not a failure: this source has no installation register to read.
        return []

    async def get_installation_request(self, request_id: str) -> InstallationRequestOut:
        raise ResourceNotFoundError(
            "The Sentinel grid keeps no installation register",
            source_system=self.source_system,
        )

    async def create_installation_request(
        self, form: InstallationForm, *, created_by: str
    ) -> InstallationRequestOut:
        raise self._read_only("accept an installation form")

    async def update_installation_request(
        self, request_id: str, patch: InstallationFormPatch, *, updated_by: str
    ) -> InstallationRequestOut:
        raise self._read_only("edit an installation record")

    async def submit_installation_request(
        self, request_id: str, *, submitted_by: str
    ) -> InstallationRequestOut:
        raise self._read_only("submit an installation record")

    async def suspend_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        raise self._read_only("suspend a camera")

    async def decommission_installation_request(
        self, request_id: str, *, actor: str, reason: str | None = None
    ) -> InstallationRequestOut:
        raise self._read_only("decommission a camera")

    async def mark_synchronized(self, request_id: str) -> InstallationRequestOut:
        raise self._read_only("acknowledge synchronisation")
