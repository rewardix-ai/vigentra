"""E-challan: from a confirmed incident and the plate an operator read on its evidence, to an SMS.

Three pieces, each replaceable:

- OFFENCES: the incident kinds that are offences, with section and fine. Amounts are the central
  Motor Vehicles Act schedule (2019 amendment); a state may notify its own rates, so the department
  must confirm them before use (`verify` is shown with every challan).
- Owner lookup. Production: the state e-challan / VAHAN service, which holds the legal basis for
  linking a registration to its owner (MV Act s.136A electronic enforcement; CMVR rule 167A: the
  notice goes to the owner within 15 days, with the evidence). Not configured here, so the DEMO
  source answers: a fictional owner per plate, created on first lookup and kept in
  `demo_vehicle_owners`, every mobile starting with 0 so it can never be a real number.
- SMS. Production: a DLT-registered gateway (Indian SMS needs a registered sender and template).
  None is configured, so messages are SIMULATED: written with the challan, never sent.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Challan, DemoVehicleOwner

OFFENCES: dict[str, dict] = {
    "NO_HELMET": {"label": "riding a two-wheeler without a helmet", "section": "MV Act s.129 r/w s.194D",
                  "fine": 1000, "note": "and licence disqualification for 3 months"},
    "WRONG_WAY": {"label": "driving against the direction of traffic", "section": "MV Act s.184",
                  "fine": 1000, "note": ""},
}
VERIFY = "Fine per the central MV Act schedule; confirm the state-notified rate before issuing."

IST = timezone(timedelta(hours=5, minutes=30))
OWNER_SOURCE = "demo"
SMS_PROVIDER = "simulated"

_FIRST = ["Ravi", "Kiran", "Meena", "Hitesh", "Pooja", "Nilesh", "Asha", "Jignesh", "Komal", "Paresh",
          "Dipti", "Mahesh", "Rekha", "Sanjay", "Bhavna", "Tushar"]
_LAST = ["Patel", "Shah", "Desai", "Mehta", "Joshi", "Parmar", "Solanki", "Chauhan", "Rathod", "Trivedi"]


def mask(mobile: str) -> str:
    digits = "".join(ch for ch in mobile if ch.isdigit())
    return f"+91 XXXXX X{digits[-4:]}" if len(digits) >= 4 else "XXXX"


async def owner_of(db: AsyncSession, plate: str) -> DemoVehicleOwner:
    """The (demo) registered owner of a plate: the same fictional owner every time it is asked."""
    row = await db.get(DemoVehicleOwner, plate)
    if row is None:
        h = int(hashlib.sha1(plate.encode()).hexdigest(), 16)
        row = DemoVehicleOwner(plate_normalised=plate, owner_name=f"{_FIRST[h % 16]} {_LAST[(h >> 8) % 10]}",
                               mobile=f"+91 0{(h >> 16) % 10**9:09d}", is_demo=True)
        db.add(row)
        await db.flush()
    return row


def sms_text(challan_no: str, plate: str, offence: dict, place: str, when: datetime, department: str) -> str:
    local = when.astimezone(IST).strftime("%d-%m-%Y %H:%M IST")
    note = f" {offence['note']}" if offence["note"] else ""
    return (f"{department} e-Challan {challan_no}: vehicle {plate} recorded {offence['label']} at {place} "
            f"on {local}. Fine Rs {offence['fine']}{note} ({offence['section']}). The camera evidence is "
            f"attached to the challan; pay or contest on the e-Challan portal within 60 days.")


async def next_challan_no(db: AsyncSession, when: datetime) -> str:
    day = when.strftime("%Y%m%d")
    n = (await db.execute(select(func.count()).select_from(Challan).where(Challan.challan_no.like(f"VG-{day}-%")))).scalar_one()
    return f"VG-{day}-{n + 1:05d}"


def send_sms(mobile: str, text: str) -> str:
    """SENT / SIMULATED / FAILED. No gateway is configured: nothing leaves this system."""
    return "SIMULATED"
