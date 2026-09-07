"""The dataset machinery's load-bearing guarantees.

Four things must hold or the dataset is worse than useless, and each is tested
here rather than trusted:

  * a size band is derived from the data, and degrades honestly when there is
    too little data to derive one;
  * OCR failure never turns into "no plate";
  * a vehicle sequence cannot straddle train and val/test;
  * rebuilding from the same inputs reproduces the same split.
"""
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from _corpus import (  # noqa: E402
    NEEDS_HUMAN, PHYSICAL_FLOOR_PX, PlateSample, Readability, angle_category,
    classify_difficulty, derive_bands, frame_number_of, readability_of,
    split_of, to_yolo,
)


def _sample(**overrides):
    base = dict(
        image_id="cam06_00001_000", feed_id="cam06", frame_number=1,
        frame_path="cam06_0001.jpg", bbox=(10.0, 10.0, 50.0, 22.0),
        plate_width_px=40.0, plate_height_px=12.0, plate_area_px=480.0,
        plate_area_frac=0.001, plate_aspect=3.3, plate_size_category="SMALL",
        detection_confidence=0.6, detection_source="roi", blur_score=200.0,
        brightness=120.0, contrast=50.0, glare=0.0, crop_quality=0.5,
        skew_deg=2.0, angle_category="FRONTAL", touches_border=False,
        lighting="DAY", vehicles_in_frame=1,
    )
    base.update(overrides)
    return PlateSample(**base)


# --- size bands ------------------------------------------------------------

def test_bands_are_derived_from_the_observed_widths():
    widths = list(range(10, 210))          # 200 observations, 10..209 px
    bands = derive_bands(widths, provenance="test")
    assert bands.sample_size == 200
    # Quantile-derived, so the edges must sit inside the observed range rather
    # than at any preset constant.
    assert 10 < bands.extremely_tiny < bands.very_small < bands.small < bands.medium < 210
    assert bands.classify(11) == "EXTREMELY_TINY"
    assert bands.classify(205) == "LARGE"


def test_too_few_observations_falls_back_and_says_so():
    bands = derive_bands([30.0, 40.0, 50.0], provenance="test")
    assert bands.sample_size == 3
    assert "physical floor" in bands.provenance
    assert bands.small == PHYSICAL_FLOOR_PX


def test_bands_stay_a_partition_when_widths_are_all_the_same():
    # A concentrated distribution collapses every quantile onto one value; the
    # bands must still order strictly or classify() becomes ambiguous.
    bands = derive_bands([42.0] * 50, provenance="test")
    assert bands.extremely_tiny < bands.very_small < bands.small < bands.medium


# --- readability is never allowed to mean "no plate" -----------------------

def test_an_unread_small_plate_is_unreadable_not_absent():
    sample = _sample(plate_width_px=30.0)
    verdict = readability_of(sample, "", 0.0)
    assert verdict == Readability.UNREADABLE_TOO_SMALL.value
    # The crucial property: it is still a plate sample, with a real box.
    assert sample.plate_width_px == 30.0


def test_an_unread_large_plate_is_a_quality_failure_not_a_size_one():
    sample = _sample(plate_width_px=140.0)
    assert readability_of(sample, "", 0.0) == Readability.UNREADABLE_QUALITY.value


def test_a_read_plate_is_readable_at_any_size():
    assert readability_of(_sample(plate_width_px=12.0), "GJ01AB1234", 0.9) == \
        Readability.READABLE.value


# --- difficulty tagging ----------------------------------------------------

def test_tiny_and_blurred_are_tagged_together():
    bands = derive_bands(list(range(5, 205)), provenance="test")
    sample = _sample(plate_width_px=8.0, blur_score=10.0)
    sample.plate_size_category = bands.classify(8.0)
    tags = classify_difficulty(sample, bands)
    assert "tiny" in tags and "blur" in tags


def test_human_only_tags_are_never_assigned_automatically():
    bands = derive_bands(list(range(5, 205)), provenance="test")
    # Deliberately awful on every measurable axis.
    sample = _sample(plate_width_px=6.0, blur_score=1.0, brightness=10.0,
                     contrast=2.0, glare=0.9, angle_category="SEVERE",
                     touches_border=True, vehicles_in_frame=9,
                     detection_confidence=0.01)
    sample.plate_size_category = bands.classify(6.0)
    tags = set(classify_difficulty(sample, bands))
    assert not (tags & {t.value for t in NEEDS_HUMAN}), (
        "occlusion, dirt, non-standard and false-positive cannot be measured "
        "from pixels and must never be guessed")


def test_angle_category_admits_it_does_not_know():
    assert angle_category(None) == "UNKNOWN"
    assert angle_category(1.0) == "FRONTAL"
    assert angle_category(40.0) == "SEVERE"


# --- splitting -------------------------------------------------------------

def test_one_time_block_never_straddles_two_splits():
    # Every frame inside a block must land in the same split, or near-duplicate
    # frames of one vehicle leak between train and test.
    for block_start in range(0, 400, 20):
        splits = {split_of("cam06", block_start + offset) for offset in range(20)}
        assert len(splits) == 1, f"block at {block_start} straddles {splits}"


def test_splits_are_reproducible():
    first = [split_of("cam06", n) for n in range(0, 600, 7)]
    second = [split_of("cam06", n) for n in range(0, 600, 7)]
    assert first == second


def test_different_cameras_split_independently():
    a = [split_of("cam06", n) for n in range(0, 2000, 20)]
    b = [split_of("cam07", n) for n in range(0, 2000, 20)]
    assert a != b, "two cameras hashing identically would defeat the split"


def test_all_three_splits_are_actually_used():
    got = {split_of("cam06", n) for n in range(0, 4000, 20)}
    assert got == {"train", "val", "test"}


# --- coordinates -----------------------------------------------------------

def test_yolo_conversion_round_trips_a_central_box():
    cx, cy, w, h = to_yolo((40, 20, 60, 30), 100, 50)
    assert (cx, cy, w, h) == (0.5, 0.5, 0.2, 0.2)


def test_yolo_conversion_clamps_a_box_running_off_the_frame():
    cx, cy, w, h = to_yolo((-10, -5, 50, 25), 100, 50)
    assert 0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0
    assert 0.0 <= w <= 1.0 and 0.0 <= h <= 1.0


@pytest.mark.parametrize("name,expected", [
    ("cam06_0142.jpg", 142), ("cam06-0007.png", 7), ("frame_00099.jpg", 99),
])
def test_frame_numbers_are_parsed_from_capture_names(name, expected):
    assert frame_number_of(Path(name)) == expected
