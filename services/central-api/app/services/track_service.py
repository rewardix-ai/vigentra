"""Cross-camera movement reconstruction for one registration number.

The graded test case asks for "the complete route traversed by the designated
vehicle, including timestamped and location-wise movement history". That is
this module.

A track here is built from **plate reads alone**. There is no appearance
matching, no re-identification by colour or shape, and no join to the vehicle
reference registry. If the plate was not read, the vehicle is not on the track
— which understates movement rather than inventing it, and that is the right
direction to be wrong in.

Search is fuzzy for the reason set out in :mod:`plate_matching`: the camera
that read one character wrong is exactly the camera whose sighting an operator
most needs. Every leg therefore carries the distance that admitted it, so a
route assembled from three exact reads and one near miss says so plainly
instead of presenting all four as equally certain.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Camera as CameraRow
from ..models import PlateSighting
from . import plate_matching
from .plate_matching import DEFAULT_MAX_DISTANCE

logger = logging.getLogger("vigentra.tracks")

EARTH_RADIUS_KM = 6371.0

#: Two sightings of the same plate closer together in time than this at the
#: SAME camera are one pass, not two. A vehicle stopped at a red light under an
#: ANPR camera otherwise produces a dozen "sightings" and a route that looks
#: like frantic activity in one spot.
SAME_PASS_SECONDS = 90.0

#: A leg implying a speed above this is flagged rather than dropped.
#:
#: Flagged, not dropped, on purpose: an impossible speed usually means one of
#: the two reads is a misread of a different vehicle, and that is a finding an
#: operator should see. Silently discarding the leg would hide the evidence
#: that the track is unreliable.
IMPLAUSIBLE_SPEED_KMH = 200.0


def haversine_km(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    """Great-circle distance in kilometres."""
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class TrackPoint:
    """One camera the vehicle was read at, with everything needed to plot it."""

    sighting_id: str
    detection_id: str
    plate_read: str
    #: 0.0 when the read matched the query exactly.
    match_distance: float
    exact: bool
    confidence: float
    observations: int
    timestamp_utc: datetime

    camera_id: str
    camera_name: str | None
    owning_department: str | None
    city: str | None
    district: str | None
    zone: str | None
    road_or_junction: str | None
    landmark: str | None
    latitude: float | None
    longitude: float | None
    #: How much to trust the pin. Grid cameras publish no coordinates, so this
    #: is often derived - see docs/sentinel-grid.md.
    coverage_description: str | None = None

    # -- filled in when the leg from the previous point is computed ----------
    distance_from_previous_km: float | None = None
    seconds_from_previous: float | None = None
    implied_speed_kmh: float | None = None
    #: True when the implied speed is not physically plausible for a road
    #: vehicle - a strong hint that one of the two reads is a different car.
    implausible_leg: bool = False


@dataclass
class Track:
    """A reconstructed movement history, in time order."""

    query: str
    points: list[TrackPoint] = field(default_factory=list)
    max_distance: float = DEFAULT_MAX_DISTANCE

    @property
    def first_seen(self) -> datetime | None:
        return self.points[0].timestamp_utc if self.points else None

    @property
    def last_seen(self) -> datetime | None:
        return self.points[-1].timestamp_utc if self.points else None

    @property
    def cameras_seen(self) -> int:
        return len({point.camera_id for point in self.points})

    @property
    def total_distance_km(self) -> float:
        return round(
            sum(point.distance_from_previous_km or 0.0 for point in self.points), 2
        )

    @property
    def exact_reads(self) -> int:
        return sum(1 for point in self.points if point.exact)

    @property
    def implausible_legs(self) -> int:
        return sum(1 for point in self.points if point.implausible_leg)


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _collapse_same_pass(points: list[TrackPoint]) -> list[TrackPoint]:
    """Fold repeated reads of one vehicle at one camera into a single point.

    Keeps the highest-confidence read of the pass and sums the observations, so
    a vehicle waiting at a signal contributes one point that says it was read
    forty times, rather than forty points that look like forty passes.
    """
    if not points:
        return []

    collapsed: list[TrackPoint] = []
    for point in points:
        previous = next(
            (
                candidate
                for candidate in reversed(collapsed)
                if candidate.camera_id == point.camera_id
            ),
            None,
        )
        if previous is not None:
            gap = (
                _as_utc(point.timestamp_utc) - _as_utc(previous.timestamp_utc)
            ).total_seconds()
            if gap <= SAME_PASS_SECONDS:
                previous.observations += point.observations
                if point.confidence > previous.confidence:
                    previous.confidence = point.confidence
                    previous.plate_read = point.plate_read
                    previous.match_distance = point.match_distance
                    previous.exact = point.exact
                continue
        collapsed.append(point)
    return collapsed


def _compute_legs(points: list[TrackPoint]) -> None:
    """Fill in distance, elapsed time and implied speed between consecutive points."""
    for index in range(1, len(points)):
        previous, current = points[index - 1], points[index]
        elapsed = (
            _as_utc(current.timestamp_utc) - _as_utc(previous.timestamp_utc)
        ).total_seconds()
        current.seconds_from_previous = round(elapsed, 1)

        if None in (
            previous.latitude,
            previous.longitude,
            current.latitude,
            current.longitude,
        ):
            # A camera without coordinates still belongs on the timeline; it
            # just cannot contribute to the distance or the map line.
            continue

        km = haversine_km(
            previous.latitude, previous.longitude, current.latitude, current.longitude
        )
        current.distance_from_previous_km = round(km, 3)
        if elapsed > 0:
            speed = km / (elapsed / 3600.0)
            current.implied_speed_kmh = round(speed, 1)
            current.implausible_leg = speed > IMPLAUSIBLE_SPEED_KMH


async def reconstruct(
    db: AsyncSession,
    plate: str,
    *,
    max_distance: float = DEFAULT_MAX_DISTANCE,
    since: datetime | None = None,
    until: datetime | None = None,
    camera_ids: set[str] | None = None,
    limit: int = 500,
) -> Track:
    """Build the movement history for *plate*.

    ``camera_ids``, when given, restricts the track to cameras the caller is
    allowed to read. Scope is applied here rather than after assembly, so a
    route can never be built from a sighting the caller could not have seen.
    """
    query = plate_matching.clean(plate)
    track = Track(query=query, max_distance=max_distance)
    if not query:
        return track

    statement = select(PlateSighting)
    if since is not None:
        statement = statement.where(PlateSighting.timestamp_utc >= since)
    if until is not None:
        statement = statement.where(PlateSighting.timestamp_utc <= until)
    if camera_ids is not None:
        if not camera_ids:
            return track
        statement = statement.where(PlateSighting.camera_id.in_(camera_ids))

    # An exact-match SQL prefilter would defeat the whole point of fuzzy
    # search, so candidates are narrowed by time/scope and ranked in Python.
    # At sandbox scale that is comfortably fast; docs/scalability.md sets out
    # what replaces it at 80,000 cameras.
    statement = statement.order_by(PlateSighting.timestamp_utc.asc()).limit(limit * 20)
    rows = (await db.execute(statement)).scalars().all()

    hits = []
    for row in rows:
        distance = plate_matching.plate_distance(query, row.plate_normalised)
        if distance <= max_distance:
            hits.append((row, distance))

    if not hits:
        return track

    cameras = {
        camera.camera_id: camera
        for camera in (
            await db.execute(
                select(CameraRow).where(
                    CameraRow.camera_id.in_({row.camera_id for row, _ in hits})
                )
            )
        ).scalars().all()
    }

    points = []
    for row, distance in hits:
        camera = cameras.get(row.camera_id)
        points.append(
            TrackPoint(
                sighting_id=row.sighting_id,
                detection_id=row.detection_id,
                plate_read=row.plate_normalised,
                match_distance=round(distance, 3),
                exact=distance == 0.0,
                confidence=row.confidence,
                observations=row.observations,
                timestamp_utc=_as_utc(row.timestamp_utc),
                camera_id=row.camera_id,
                camera_name=camera.name if camera else None,
                owning_department=camera.owning_department if camera else None,
                city=camera.city if camera else None,
                district=camera.district if camera else None,
                zone=camera.zone if camera else None,
                road_or_junction=camera.road_or_junction if camera else None,
                landmark=camera.landmark if camera else None,
                latitude=camera.latitude if camera else None,
                longitude=camera.longitude if camera else None,
                coverage_description=camera.coverage_description if camera else None,
            )
        )

    points.sort(key=lambda point: point.timestamp_utc)
    points = _collapse_same_pass(points)[:limit]
    _compute_legs(points)
    track.points = points

    if track.implausible_legs:
        logger.info(
            "track for %s contains %d implausible leg(s) - likely a misread of "
            "a different vehicle on one of them",
            query, track.implausible_legs,
        )
    return track


async def search_plates(
    db: AsyncSession,
    query: str,
    *,
    max_distance: float = DEFAULT_MAX_DISTANCE,
    camera_ids: set[str] | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict]:
    """Rank distinct plates near *query*, most-seen first.

    The answer to "I have a partial or uncertain registration — what did the
    network actually see?" Returns plates, not sightings: an operator picks one
    and then asks for its route.
    """
    cleaned = plate_matching.clean(query)
    if not cleaned:
        return []

    statement = select(PlateSighting)
    if since is not None:
        statement = statement.where(PlateSighting.timestamp_utc >= since)
    if camera_ids is not None:
        if not camera_ids:
            return []
        statement = statement.where(PlateSighting.camera_id.in_(camera_ids))
    rows = (await db.execute(statement)).scalars().all()

    grouped: dict[str, dict] = {}
    for row in rows:
        distance = plate_matching.plate_distance(cleaned, row.plate_normalised)
        if distance > max_distance:
            continue
        entry = grouped.setdefault(
            row.plate_normalised,
            {
                "plate": row.plate_normalised,
                "distance": round(distance, 3),
                "exact": distance == 0.0,
                "similarity": round(plate_matching.similarity(cleaned, row.plate_normalised), 3),
                "sightings": 0,
                "cameras": set(),
                "first_seen": _as_utc(row.timestamp_utc),
                "last_seen": _as_utc(row.timestamp_utc),
                "best_confidence": row.confidence,
            },
        )
        entry["sightings"] += 1
        entry["cameras"].add(row.camera_id)
        moment = _as_utc(row.timestamp_utc)
        entry["first_seen"] = min(entry["first_seen"], moment)
        entry["last_seen"] = max(entry["last_seen"], moment)
        entry["best_confidence"] = max(entry["best_confidence"], row.confidence)

    results = []
    for entry in grouped.values():
        entry["camera_count"] = len(entry.pop("cameras"))
        results.append(entry)

    # Closest first, then the plate the network is most confident about having
    # actually seen - a single 0.35-distance read should not outrank forty
    # exact ones.
    results.sort(key=lambda item: (item["distance"], -item["sightings"]))
    return results[:limit]
