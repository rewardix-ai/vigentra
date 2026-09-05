"""Name the district an Indian registration's RTO code belongs to.

A registration like ``GJ01RY6237`` carries its origin: ``GJ`` is Gujarat, ``01``
is the Ahmedabad RTO. Surfacing that as "Ahmedabad" gives an operator context a
raw string does not - where a vehicle is registered, at a glance.

The map is deliberately honest about what it does and does not know. Gujarat's
RTO codes GJ01-GJ27 are named from a verified table; GJ28-GJ38 parse and read
normally but are accepted structurally and NOT named, because asserting a
district nobody confirmed would be exactly the fabrication the whole plate stack
is built to avoid. An unverified code returns a null district, never a guess.

This is a lookup on data the reader already produced. It runs on the central
side at display time rather than at the edge, so the edge stays lean and the
map can be corrected against the RTO gazette without redeploying workers.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger("vigentra.plate_geography")

#: GJ01RY6237 / MH12AB1234 -> state letters then the 1-2 digit RTO code.
_RTO = re.compile(r"^([A-Z]{2})\s*([0-9]{1,2})")

#: Baked into the image at /app/reference by the Dockerfile, like the other
#: reference data; a local run points it at the repo copy via the env var.
_MAP_PATH = os.getenv("PLATE_DISTRICT_MAP", "/app/reference/gj_rto_districts.json")


@dataclass(frozen=True)
class PlateOrigin:
    state_code: str
    state_name: str | None
    rto: str
    district: str | None
    #: False when the RTO parses but its district is not in the verified table.
    #: The caller shows the code without a name rather than inventing one.
    district_verified: bool


@lru_cache(maxsize=1)
def _gujarat() -> dict:
    """The verified GJ RTO -> district table, or an empty map if it is absent.

    Absence is not fatal: plates still read and display, they simply carry no
    district. A registry that shows a plate without its district is fine; one
    that will not load because a lookup file moved is not.
    """
    try:
        with open(_MAP_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("district map %s unavailable: %s", _MAP_PATH, exc)
        return {"districts": {}}


#: State code -> full name, for the states this estate actually sees. Not a
#: complete list of Indian states; extend as deployments spread.
_STATE_NAMES = {
    "GJ": "Gujarat", "MH": "Maharashtra", "RJ": "Rajasthan", "DL": "Delhi",
    "MP": "Madhya Pradesh", "UP": "Uttar Pradesh", "KA": "Karnataka",
}


def origin_of(plate_text: str | None) -> PlateOrigin | None:
    """Where this registration is registered, as far as can be asserted.

    Returns None when the string does not begin with a recognisable state +
    RTO. Never raises on a malformed plate - a bad read is an ordinary event.
    """
    if not plate_text:
        return None
    match = _RTO.match(plate_text.strip().upper())
    if not match:
        return None
    state, rto_digits = match.group(1), match.group(2)
    rto = rto_digits.zfill(2)

    district = None
    verified = False
    if state == "GJ":
        entry = _gujarat().get("districts", {}).get(rto)
        if entry and entry.get("verified"):
            district = entry.get("district")
            verified = True

    return PlateOrigin(
        state_code=state,
        state_name=_STATE_NAMES.get(state),
        rto=f"{state}{rto}",
        district=district,
        district_verified=verified,
    )
