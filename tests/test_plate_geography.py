"""RTO -> district naming: name what is verified, never guess the rest."""
import os

os.environ.setdefault("PLATE_DISTRICT_MAP", "./data/reference/gj_rto_districts.json")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "central-api"))

from app.services.plate_geography import origin_of  # noqa: E402


def test_a_verified_gujarat_rto_names_its_district():
    o = origin_of("GJ01RY6237")
    assert o is not None
    assert o.state_name == "Gujarat"
    assert o.rto == "GJ01"
    assert o.district == "Ahmedabad"
    assert o.district_verified is True


def test_an_unverified_rto_parses_but_names_no_district():
    o = origin_of("GJ30AB1234")
    assert o is not None
    assert o.rto == "GJ30"
    # Parses and reads; simply carries no district rather than a fabricated one.
    assert o.district is None
    assert o.district_verified is False


def test_another_state_shows_the_state_but_no_gujarat_district():
    o = origin_of("MH12AB1234")
    assert o is not None
    assert o.state_name == "Maharashtra"
    assert o.district is None


def test_a_malformed_or_empty_plate_is_not_an_error():
    assert origin_of("garbage") is None
    assert origin_of("") is None
    assert origin_of(None) is None


def test_single_digit_rto_is_zero_padded():
    o = origin_of("GJ1AB1234")
    assert o is not None and o.rto == "GJ01" and o.district == "Ahmedabad"
