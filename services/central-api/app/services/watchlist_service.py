"""Sightings, watchlist matching and alert generation.

This is the layer that makes the platform react rather than merely observe.
Detection ingest calls :func:`record_sightings` with the plate reads in a
batch; that writes a sighting per read and raises an alert for every active
watchlist entry the read is close enough to.

Three decisions worth stating, because each of them is the kind that quietly
ruins an alerting system:

**Matching happens on ingest, not on read.** An alert is a thing that must have
existed at a moment in time — "the system knew at 14:32" — and a query-time
matcher cannot say that. It also means a watchlist entry added *after* a
vehicle passed does not retroactively invent an alert, which is the correct
behaviour and the surprising one.

**The active watchlist is cached, briefly.** A batch of 200 detections would
otherwise re-read the whole list 200 times. The cache is short (see
``_CACHE_TTL_SECONDS``) and cleared on every write, so a plate added by an
operator is live within one batch rather than within a TTL.

**A near match is stored, not hidden.** The matcher accepts up to
``DEFAULT_MAX_DISTANCE`` — roughly two plausible OCR errors — and records the
distance that produced each hit. Suppressing near matches to keep the console
tidy is how a stolen vehicle passes a camera and nobody hears about it.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PlateSighting, WatchlistAlert, WatchlistEntry
from . import plate_matching
from .plate_matching import DEFAULT_MAX_DISTANCE

logger = logging.getLogger("vigentra.watchlist")

WATCH_CATEGORIES = ("stolen", "wanted", "blacklist", "missing", "suspect")

#: How long the active watchlist may be served from memory. Deliberately short:
#: this is a correctness/latency trade, and the correctness side is an operator
#: adding a stolen vehicle and expecting the very next camera to catch it.
_CACHE_TTL_SECONDS = 10.0

_cache: tuple[float, list[dict]] | None = None


def invalidate_cache() -> None:
    """Drop the cached watchlist. Called on every write to it."""
    global _cache
    _cache = None


def new_sighting_id() -> str:
    return f"sight_{uuid.uuid4().hex[:20]}"


def new_alert_id() -> str:
    return f"alert_{uuid.uuid4().hex[:20]}"


def new_entry_id() -> str:
    return f"watch_{uuid.uuid4().hex[:16]}"


async def active_watchlist(db: AsyncSession, *, use_cache: bool = True) -> list[dict]:
    """Every watchlist entry currently in force.

    Expiry is applied here rather than by a purge job, for the same reason
    plate retention is enforced on read: a job that fails to run must not
    quietly extend how long a vehicle stays flagged.
    """
    global _cache
    now = time.monotonic()
    if use_cache and _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]

    rows = (
        await db.execute(select(WatchlistEntry).where(WatchlistEntry.active.is_(True)))
    ).scalars().all()

    moment = datetime.now(timezone.utc)
    entries = []
    for row in rows:
        expires = row.expires_at
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= moment:
                continue
        entries.append(
            {
                "entry_id": row.entry_id,
                "plate": row.plate,
                "category": row.category,
                "reason": row.reason,
                "case_reference": row.case_reference,
                "owning_department": row.owning_department,
            }
        )

    _cache = (now, entries)
    return entries


@dataclass
class SightingResult:
    """What one ingest batch produced on the identity side."""

    sightings_recorded: int = 0
    sightings_duplicate: int = 0
    sightings_rejected: int = 0
    alerts_raised: list[WatchlistAlert] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.alerts_raised is None:
            self.alerts_raised = []

    def as_dict(self) -> dict:
        return {
            "sightings_recorded": self.sightings_recorded,
            "sightings_duplicate": self.sightings_duplicate,
            "sightings_rejected": self.sightings_rejected,
            "alerts_raised": len(self.alerts_raised),
        }


@dataclass(frozen=True)
class PlateRead:
    """One plate read from an ingest batch, decoupled from the HTTP schema."""

    detection_id: str
    camera_id: str
    plate_text: str
    confidence: float
    timestamp_utc: datetime
    observations: int = 1
    reader: str | None = None
    frame_quality: str | None = None
    evidence_reference: str | None = None
    is_demo_data: bool = True
    provenance: dict | None = None


async def record_sightings(
    db: AsyncSession,
    reads: list[PlateRead],
    *,
    max_distance: float = DEFAULT_MAX_DISTANCE,
) -> SightingResult:
    """Store each read as a sighting and raise alerts for watchlist hits.

    Does not commit — the caller owns the transaction, so a sighting and the
    detection it came from land together or not at all.

    A read that does not clean to a plausible Indian registration is counted as
    rejected and dropped. Half-read text is worse than no text: it looks like
    evidence, and it is not.
    """
    result = SightingResult()
    if not reads:
        return result

    watchlist = await active_watchlist(db)
    watch_plates = [entry["plate"] for entry in watchlist]
    by_plate = {entry["plate"]: entry for entry in watchlist}

    # One lookup for the batch. Detection IDs are deterministic at the edge, so
    # a replayed batch must not double-count a sighting or re-raise its alert.
    detection_ids = {read.detection_id for read in reads}
    existing = {
        row.detection_id
        for row in (
            await db.execute(
                select(PlateSighting).where(PlateSighting.detection_id.in_(detection_ids))
            )
        ).scalars().all()
    }

    for read in reads:
        if read.detection_id in existing:
            result.sightings_duplicate += 1
            continue

        normalised = plate_matching.clean(read.plate_text)
        if not plate_matching.is_plausible(normalised):
            result.sightings_rejected += 1
            logger.debug(
                "sighting rejected, not a plausible registration: %r", read.plate_text
            )
            continue

        sighting = PlateSighting(
            sighting_id=new_sighting_id(),
            detection_id=read.detection_id,
            plate_text=read.plate_text[:24],
            plate_normalised=normalised,
            state_code=plate_matching.state_of(normalised),
            camera_id=read.camera_id,
            timestamp_utc=read.timestamp_utc,
            confidence=read.confidence,
            observations=max(1, read.observations),
            reader=read.reader,
            frame_quality=read.frame_quality,
            evidence_reference=read.evidence_reference,
            is_demo_data=read.is_demo_data,
            provenance=dict(read.provenance or {}),
        )
        db.add(sighting)
        existing.add(read.detection_id)
        result.sightings_recorded += 1

        for match in plate_matching.best_matches(
            normalised, watch_plates, max_distance=max_distance
        ):
            entry = by_plate[match.watch_plate]
            alert = WatchlistAlert(
                alert_id=new_alert_id(),
                watch_plate=match.watch_plate,
                seen_plate=match.seen_plate,
                category=entry["category"],
                distance=match.distance,
                exact=match.exact,
                sighting_id=sighting.sighting_id,
                camera_id=read.camera_id,
                timestamp_utc=read.timestamp_utc,
                is_demo_data=read.is_demo_data,
            )
            db.add(alert)
            result.alerts_raised.append(alert)
            logger.warning(
                "WATCHLIST HIT %s (%s) seen as %s at %s, distance %.2f%s",
                match.watch_plate,
                entry["category"],
                match.seen_plate,
                read.camera_id,
                match.distance,
                "" if match.exact else " (near match - needs review)",
            )

    return result
