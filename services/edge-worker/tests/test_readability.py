"""The readability ledger: saying why a camera produced nothing.

The behaviour under test is mostly a refusal. It would be easy to write a gate
that discards more and reports a tidier number, and the tests here exist to
stop that: the floor must stay low enough to admit the plate sizes that have
actually been read correctly on this estate.
"""
from anpr.readability import (
    HARD_FLOOR_PX,
    MARGINAL_PX,
    ReadabilityLedger,
    VERDICT_CONFIRMED,
    VERDICT_UNCERTAIN,
    VERDICT_UNREADABLE,
    tier_of,
    worth_reading,
)


def test_the_size_bands_are_ordered_as_documented():
    assert tier_of(200) == "COMFORTABLE"
    assert tier_of(130) == "READABLE"
    assert tier_of(90) == "MARGINAL"
    assert tier_of(50) == "SUB_MARGINAL"
    assert tier_of(20) == "UNREADABLE"


def test_the_floor_admits_every_plate_this_estate_has_actually_read():
    # Widths of adjudicated crops a human read the registration off. If a
    # change to the floor ever fails this, it is discarding real evidence.
    for width in (61.0, 64.0, 53.0, 85.0, 90.0):
        assert worth_reading(width), f"{width}px was readable in the field"


def test_below_the_floor_nothing_is_attempted():
    assert not worth_reading(HARD_FLOOR_PX - 1)
    led = ReadabilityLedger()
    assert led.observe(1, 30.0) is False
    assert led.below_floor == 1


def test_a_settled_plate_is_confirmed():
    led = ReadabilityLedger()
    led.observe(1, 140.0)
    assert led.retire(1, confirmed=True) == VERDICT_CONFIRMED


def test_a_big_plate_that_never_agreed_is_uncertain_not_unreadable():
    # This is the distinction the whole module exists for: the pixels were
    # there, so the failure belongs to this vehicle, not to the camera.
    led = ReadabilityLedger()
    led.observe(1, MARGINAL_PX + 20)
    assert led.retire(1, confirmed=False) == VERDICT_UNCERTAIN


def test_a_plate_never_big_enough_is_unreadable():
    led = ReadabilityLedger()
    for _ in range(5):
        led.observe(1, 55.0)
    assert led.retire(1, confirmed=False) == VERDICT_UNREADABLE


def test_a_track_that_was_never_seen_at_all_is_unreadable():
    led = ReadabilityLedger()
    assert led.retire(99, confirmed=False) == VERDICT_UNREADABLE


def test_a_camera_with_no_evidence_refuses_to_judge_itself():
    led = ReadabilityLedger()
    for i in range(4):
        led.observe(i, 45.0)
    assert led.describe()["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_a_camera_that_only_ever_sees_smears_is_called_infeasible():
    led = ReadabilityLedger()
    for i in range(20):
        led.observe(i, 45.0)
        led.retire(i, confirmed=False)
    d = led.describe()
    assert d["verdict"] == "ANPR_INFEASIBLE_HERE"
    assert d["unreadable"] == 20
    assert d["crops_at_readable_size"] == 0


def test_a_camera_that_confirms_plates_is_called_working():
    led = ReadabilityLedger()
    for i in range(20):
        led.observe(i, 130.0)
        led.retire(i, confirmed=i < 5)
    d = led.describe()
    assert d["verdict"] == "ANPR_WORKING"
    assert d["confirmed"] == 5
    assert d["uncertain"] == 15


def test_reset_clears_the_ledger():
    led = ReadabilityLedger()
    led.observe(1, 130.0)
    led.retire(1, confirmed=True)
    led.reset()
    d = led.describe()
    assert d["tracks_settled"] == 0
    assert d["crops_measured"] == 0
