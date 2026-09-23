"""One vehicle is counted once, however many tracker ids it was given; two vehicles are never one."""
from anpr.track.vehicle_count import link_fragments


def moving(start, frames, x0, y0, vx=0.0, vy=8.0, w=120, h=90):
    return [(f, (x0 + vx * (f - start), y0 + vy * (f - start), x0 + vx * (f - start) + w, y0 + vy * (f - start) + h))
            for f in range(start, start + frames, 2)]


def count(obs, classes=None, **kw):
    return link_fragments(obs, classes or {t: "car" for t in obs}, **kw)[1]


def test_an_id_switch_along_the_path_is_one_vehicle():
    a = moving(0, 20, 400, 100)                      # frames 0-18, moving down 8 px a frame
    b = moving(26, 20, 400, 100 + 8 * 26)            # picked up again 8 frames later, where it was heading
    assert count({"t1": a, "t2": b}) == 1


def test_the_same_vehicle_boxed_twice_is_one_vehicle():
    a = moving(0, 30, 400, 100)
    b = [(f, (x1 + 4, y1 + 3, x2 - 2, y2 + 2)) for f, (x1, y1, x2, y2) in a]
    assert count({"t1": a, "t2": b}, {"t1": "car", "t2": "truck"}) == 1


def test_a_parked_car_reacquired_later_is_one_vehicle():
    parked = [(f, (900, 50, 1100, 200)) for f in range(0, 200, 2)]
    again = [(f, (902, 51, 1101, 201)) for f in range(600, 900, 2)]
    assert count({"t1": parked, "t9": again}) == 1


def test_the_next_vehicle_in_the_lane_is_another_vehicle():
    a = moving(0, 40, 400, 100)                      # leaves at the bottom
    b = moving(46, 40, 400, 100)                     # a new one enters at the top, far from where a was heading
    assert count({"t1": a, "t2": b}) == 2


def test_a_motorcycle_passing_a_bus_is_another_vehicle():
    bus = moving(0, 40, 300, 100, w=400, h=300)
    bike = moving(0, 40, 450, 250, w=60, h=90)       # inside the bus box, a quarter of its size
    assert count({"t1": bus, "t2": bike}, {"t1": "bus", "t2": "motorcycle"}) == 2


def test_a_car_and_a_motorcycle_never_continue_each_other():
    a = moving(0, 20, 400, 100)
    b = moving(26, 20, 400, 100 + 8 * 26)
    assert count({"t1": a, "t2": b}, {"t1": "car", "t2": "motorcycle"}) == 2


def test_a_fragment_continues_at_most_one_other():
    a = moving(0, 20, 400, 100)
    b = moving(26, 20, 400, 100 + 8 * 26)
    c = moving(26, 20, 450, 100 + 8 * 26)            # a neighbour close enough to be a candidate too
    assert count({"t1": a, "t2": b, "t3": c}) == 2


def test_the_plate_reader_can_join_fragments_the_boxes_cannot():
    a = moving(0, 20, 100, 100)
    b = moving(300, 20, 900, 100)
    assert count({"t1": a, "t2": b}, same_vehicle=[["t1", "t2"]]) == 1


def test_a_two_frame_blip_is_not_a_vehicle():
    assert count({"t1": [(0, (0, 0, 10, 10)), (2, (0, 0, 10, 10))]}) == 0


def test_a_parked_car_dropped_three_times_is_still_one_vehicle():
    spot = lambda f0, f1, dx=0: [(f, (900 + dx, 50, 1100 + dx, 200)) for f in range(f0, f1, 2)]
    obs = {"t1": spot(0, 100), "t5": spot(300, 330, 2), "t7": spot(150, 250, 1), "t9": spot(900, 1200, 3)}
    assert count(obs) == 1


def test_a_box_on_nothing_is_not_a_vehicle_and_joins_nothing():
    ghost = [(f, (400, 300, 480, 380)) for f in range(0, 40, 2)]            # a lane marking, all clip long
    bike = [(f, (405, 302, 478, 381)) for f in range(600, 640, 2)]          # a bike stopping on it later
    number, n = link_fragments({"g": ghost, "b": bike}, {"g": "motorcycle", "b": "motorcycle"},
                               scores={"g": 0.28, "b": 0.81}, min_score=0.6)
    assert n == 1 and number["g"] is None and number["b"] == 1


def test_a_distant_motorcycle_scoring_like_a_lane_marking_is_still_a_vehicle():
    bike = moving(0, 40, 400, 100, w=40, h=60)
    number, n = link_fragments({"b": bike}, {"b": "motorcycle"}, scores={"b": 0.34}, min_score=0.6)
    assert n == 1
