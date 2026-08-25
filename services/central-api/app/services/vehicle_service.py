"""Vehicle reference registry - import and lookup.

**Scope, and why it is drawn here.**

This is a lookup table of vehicle *attributes*: plate → make, model, colour,
class, fuel, registration status. It is not joined to cameras, detections or
video sessions anywhere in this codebase, and `tests/test_vehicles.py` asserts
that stays true.

The separation is the point. Vehicle attributes on their own are ordinary
reference data — the same category as a make/model catalogue. The risk lives in
the JOIN: linking "camera X saw this plate at 14:32" to a registration is what
turns a camera registry into a person-tracking system. That join needs ANPR,
which this phase does not implement, and it would need its own legal basis
besides. So the table exists; the join does not.

**The import guard.** `assert_no_owner_fields` rejects any record carrying an
owner-identifying column. This is not defensive paranoia about the current
file — that one is clean — it is a guard against a future export quietly
including owner data and nobody noticing until it is already in the database.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import Vehicle

logger = logging.getLogger("sentinel.vehicles")


class OwnerDataRejected(ValueError):
    """The import carried an owner-identifying field and was refused.

    Deliberately fatal rather than "strip it and carry on": silently discarding
    personal data hides the fact that someone exported it in the first place,
    which is exactly the thing worth knowing about.
    """


@dataclass
class ImportResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    source: str = ""
    schema_version: str = ""
    is_demo_data: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "source": self.source,
            "schema_version": self.schema_version,
            "is_demo_data": self.is_demo_data,
            "total": self.imported + self.updated,
        }


def normalize_plate(value: str | None) -> str:
    """`mh 05 ef 3195` -> `MH05EF3195`.

    Registration numbers are written with wildly inconsistent spacing and
    hyphenation, so both stored keys and lookups go through this.
    """
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def assert_no_owner_fields(record: dict[str, Any], forbidden: set[str]) -> None:
    """Refuse any record carrying an owner-identifying field."""
    present = sorted(key for key in record if key.strip().lower() in forbidden)
    if present:
        raise OwnerDataRejected(
            "Vehicle import refused: the source carries owner-identifying "
            f"field(s) {present}. This registry stores vehicle attributes only. "
            "Remove these columns from the export and re-run."
        )


async def import_from_file(
    db: AsyncSession,
    settings: Settings,
    path: str | Path | None = None,
    *,
    force_demo_flag: bool | None = None,
) -> ImportResult:
    """Load the reference file into the `vehicles` table.

    Idempotent on the normalised registration number, so re-running updates
    rather than duplicating.
    """
    source_path = Path(path or settings.vehicle_registry_path)
    result = ImportResult()

    if not source_path.is_file():
        result.errors.append(f"Vehicle registry file not found: {source_path}")
        return result

    payload = json.loads(source_path.read_text(encoding="utf-8"))
    vehicles = payload.get("vehicles") if isinstance(payload, dict) else payload
    if not isinstance(vehicles, list):
        result.errors.append("Vehicle registry file has no 'vehicles' array.")
        return result

    result.source = str(payload.get("source", "reference_import"))
    result.schema_version = str(payload.get("schema_version", "1.0"))
    # The file carries its own flag, but the operator running the import gets
    # the final say - a mislabelled export should not silently define how the
    # data is treated for the rest of its life.
    result.is_demo_data = (
        force_demo_flag
        if force_demo_flag is not None
        else bool(payload.get("is_demo_data", True))
    )

    forbidden = {
        token.strip().lower()
        for token in settings.vehicle_registry_forbidden_fields.split(",")
        if token.strip()
    }

    now = datetime.now(timezone.utc)
    for raw in vehicles:
        if not isinstance(raw, dict):
            result.skipped += 1
            continue

        # Fatal by design - see OwnerDataRejected.
        assert_no_owner_fields(raw, forbidden)

        plate = normalize_plate(raw.get("registration_number"))
        if not plate:
            result.skipped += 1
            result.errors.append("Record without a registration_number was skipped.")
            continue

        row = (
            await db.execute(select(Vehicle).where(Vehicle.registration_number == plate))
        ).scalar_one_or_none()
        is_new = row is None
        if row is None:
            row = Vehicle(registration_number=plate)
            db.add(row)

        row.registration_date = _parse_date(raw.get("registration_date"))
        row.registration_valid_upto = _parse_date(raw.get("registration_valid_upto"))
        row.vehicle_category_code = raw.get("vehicle_category_code")
        row.vehicle_class = raw.get("vehicle_class")
        row.make = raw.get("make")
        row.model = raw.get("model")
        row.body_type = raw.get("body_type")
        row.fuel_type = raw.get("fuel_type")
        row.colour = raw.get("colour")
        row.registration_status = raw.get("registration_status")
        row.registered_at = raw.get("registered_at")
        row.status_as_on = _parse_date(raw.get("status_as_on"))
        row.source = result.source
        row.schema_version = result.schema_version
        row.is_demo_data = result.is_demo_data
        row.imported_at = now

        if is_new:
            result.imported += 1
        else:
            result.updated += 1

    await db.commit()
    logger.info(
        "vehicle registry import: %s new, %s updated, source=%s demo=%s",
        result.imported, result.updated, result.source, result.is_demo_data,
    )
    return result


async def lookup(db: AsyncSession, registration_number: str) -> Vehicle | None:
    """Exact lookup by registration number, spacing-insensitive."""
    plate = normalize_plate(registration_number)
    if not plate:
        return None
    return (
        await db.execute(select(Vehicle).where(Vehicle.registration_number == plate))
    ).scalar_one_or_none()


async def search(
    db: AsyncSession,
    *,
    query: str | None = None,
    make: str | None = None,
    vehicle_class: str | None = None,
    registration_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Vehicle], int]:
    """Filtered listing. Returns (page, total_matching)."""
    stmt = select(Vehicle)

    if query:
        plate = normalize_plate(query)
        like = f"%{query.strip().upper()}%"
        stmt = stmt.where(
            or_(
                Vehicle.registration_number.contains(plate),
                func.upper(Vehicle.make).like(like),
                func.upper(Vehicle.model).like(like),
            )
        )
    if make:
        stmt = stmt.where(func.upper(Vehicle.make).like(f"%{make.strip().upper()}%"))
    if vehicle_class:
        stmt = stmt.where(Vehicle.vehicle_class == vehicle_class)
    if registration_status:
        stmt = stmt.where(Vehicle.registration_status == registration_status)

    total = len(((await db.execute(stmt)).scalars().all()))
    rows = (
        await db.execute(
            stmt.order_by(Vehicle.registration_number).offset(offset).limit(limit)
        )
    ).scalars().all()
    return list(rows), total


async def facets(db: AsyncSession) -> dict[str, list[str]]:
    """Distinct values for the filter dropdowns."""
    rows = (await db.execute(select(Vehicle))).scalars().all()

    def distinct(attribute: str) -> list[str]:
        return sorted({getattr(row, attribute) for row in rows if getattr(row, attribute)})

    return {
        "makes": distinct("make"),
        "classes": distinct("vehicle_class"),
        "statuses": distinct("registration_status"),
        "fuel_types": distinct("fuel_type"),
    }
