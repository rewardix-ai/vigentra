"""The ANPR-suitability verdict: measured floors, favourable-case estimate."""
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


def test_large_vehicles_are_capable():
    # ~500px cars -> ~125px estimated plate, above the comfortable floor.
    v = diag._verdict([500.0] * 20, frames_scored=40)
    assert v["anpr_verdict"] == "ANPR_CAPABLE"


def test_tiny_vehicles_are_infeasible():
    # ~120px cars -> ~30px estimated plate, below the marginal floor.
    v = diag._verdict([120.0] * 20, frames_scored=40)
    assert v["anpr_verdict"] == "ANPR_INFEASIBLE"


def test_the_estimate_is_a_quarter_of_box_width():
    v = diag._verdict([400.0], frames_scored=10)
    assert v["estimated_plate_width_px"]["p90"] == 100.0  # 400 * 0.25


def test_a_borderline_estate_lands_marginal():
    # ~280px vehicles -> ~70px estimated plate: over the 60px marginal floor,
    # under the 90px readable one. Marginal, not capable.
    v = diag._verdict([280.0] * 20, frames_scored=40)
    assert v["anpr_verdict"] == "ANPR_MARGINAL"
