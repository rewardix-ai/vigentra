"""A registration never fills the vehicle carrying it.

The numbers here come from looking at all 323 proposed boxes in data/det/grid of the ANPR
research repo (reports/review/grid/by_eye.csv there): of the 279 that are plates the widest is 0.57 of the vehicle
box; 37 of the 44 that are not - a night hoarding's phone number read as TS57SQ8300, a light bar,
a delivery bag - are above 0.60.
"""
from __future__ import annotations

from anpr.detect.plate import geometry_prior

VEHICLE = (0.0, 0.0, 200.0, 200.0)


def prior(x1: float, x2: float, y1: float = 150.0, y2: float = 172.0, kind: str = "car") -> float:
    return geometry_prior((x1, y1, x2, y2), VEHICLE, kind)[0]


def test_a_plate_sized_box_is_untouched():
    assert prior(60, 110) == 1.0                       # 0.25 of the vehicle: the median real plate


def test_the_widest_real_plate_seen_is_untouched():
    assert prior(10, 124) == 1.0                       # 0.57, the widest of 279 human-confirmed plates


def test_a_box_that_spans_the_vehicle_is_demoted_below_the_detector_threshold():
    # the night hoarding: 0.88 of the vehicle box. plate_conf is 0.2, so even a confident
    # detection (0.9) must not survive conf * prior.
    p = prior(10, 186)
    assert p < 0.12 and 0.9 * p < 0.2


def test_the_demotion_is_recorded_as_a_reason():
    _, _, reasons = geometry_prior((10.0, 150.0, 186.0, 172.0), VEHICLE, "car")
    assert any(r.startswith("plate_w_frac_high") for r in reasons)
