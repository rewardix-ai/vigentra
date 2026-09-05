"""The overlay suppressor: condemn what stays still, spare what moves or reads."""
from anpr.osd import OsdSuppressor


def _run_frames(sup, boxes_per_frame):
    for boxes in boxes_per_frame:
        for b in boxes:
            sup.observe(b)
        sup.advance()


def test_a_static_box_is_learned_as_an_overlay():
    sup = OsdSuppressor(min_frames=8)
    clock = (10.0, 10.0, 60.0, 30.0)
    # The same box, frame after frame - a burned-in clock.
    _run_frames(sup, [[clock]] * 12)
    assert sup.is_overlay((20.0, 15.0, 55.0, 28.0))  # a candidate inside it
    assert clock in [tuple(r) for r in sup.learned_regions]


def test_a_moving_plate_is_never_masked():
    sup = OsdSuppressor(min_frames=8)
    # A plate crossing the frame: a different position every frame.
    frames = [[(float(x), 100.0, float(x) + 40.0, 120.0)] for x in range(0, 240, 20)]
    _run_frames(sup, frames)
    assert sup.learned_regions == []
    assert not sup.is_overlay((100.0, 100.0, 140.0, 120.0))


def test_a_confirmed_plate_rescues_its_region():
    sup = OsdSuppressor(min_frames=8)
    spot = (50.0, 50.0, 90.0, 70.0)
    # A busy lane where plates keep appearing at nearly the same spot, and one
    # of them CONFIRMED there - it is a lane, not furniture.
    for i in range(12):
        sup.observe(spot, confirmed=(i == 6))
        sup.advance()
    assert not sup.is_overlay((60.0, 55.0, 85.0, 68.0))
    assert spot not in [tuple(r) for r in sup.learned_regions]


def test_it_waits_before_condemning():
    sup = OsdSuppressor(min_frames=8)
    clock = (10.0, 10.0, 60.0, 30.0)
    _run_frames(sup, [[clock]] * 4)  # only 4 frames, below the floor
    assert not sup.is_overlay((20.0, 15.0, 55.0, 28.0))


def test_reset_forgets_everything():
    sup = OsdSuppressor(min_frames=8)
    clock = (10.0, 10.0, 60.0, 30.0)
    _run_frames(sup, [[clock]] * 12)
    assert sup.learned_regions
    sup.reset()
    assert sup.learned_regions == []
