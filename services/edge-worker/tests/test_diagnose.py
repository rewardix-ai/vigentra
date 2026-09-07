"""The ANPR-suitability verdict: measured floors, favourable-case estimate.

The floors are imported rather than written down again. This tool shares
them with the running pipeline (anpr/readability.py), and a test that
hardcoded its own copy would keep passing while the two drifted apart -
which is precisely the failure the sharing exists to prevent.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "diagnose_cameras",
    Path(__file__).resolve().parent.parent / "tools" / "diagnose_cameras.py",
)
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)


def test_no_vehicles_is_its_own_verdict():
    v = diag._verdict([], frames_scored=40)
    assert v["anpr_verdict"] == "NO_VEHICLES_OBSERVED"


def _boxes_for(plate_px: float) -> float:
    """The vehicle-box width whose estimated plate is this wide."""
    return plate_px / diag.PLATE_FACTOR


def test_large_vehicles_are_capable():
    v = diag._verdict([_boxes_for(diag.COMFORTABLE_PX + 10)] * 20, frames_scored=40)
    assert v["anpr_verdict"] == "ANPR_CAPABLE"


def test_tiny_vehicles_are_infeasible():
    v = diag._verdict([_boxes_for(diag.MARGINAL_PX - 30)] * 20, frames_scored=40)
    assert v["anpr_verdict"] == "ANPR_INFEASIBLE"


def test_the_estimate_is_a_quarter_of_box_width():
    v = diag._verdict([400.0], frames_scored=10)
    assert v["estimated_plate_width_px"]["p90"] == 100.0  # 400 * 0.25


def test_a_borderline_estate_lands_marginal():
    # Between the marginal floor and the readable one: worth trying, not
    # worth promising.
    midway = (diag.MARGINAL_PX + diag.READABLE_PX) / 2
    v = diag._verdict([_boxes_for(midway)] * 20, frames_scored=40)
    assert v["anpr_verdict"] == "ANPR_MARGINAL"


def test_the_floors_are_the_ones_the_pipeline_enforces():
    from anpr import readability
    assert diag.COMFORTABLE_PX == readability.COMFORTABLE_PX
    assert diag.READABLE_PX == readability.READABLE_PX
    assert diag.MARGINAL_PX == readability.MARGINAL_PX
