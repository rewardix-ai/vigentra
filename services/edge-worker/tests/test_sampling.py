"""The adaptive sampler: look cheaply, spend densely when a plate is coming."""
from anpr.sampling import AdaptiveSampler


def test_it_scans_on_the_stride_when_nothing_is_close():
    s = AdaptiveSampler(stride=10)
    processed = [i for i in range(30) if s.should_process(i)]
    assert processed == [0, 10, 20]


def test_a_large_vehicle_triggers_a_dense_burst():
    s = AdaptiveSampler(stride=10, burst_frames=5)
    assert s.should_process(0)
    # 400px vehicle -> ~100px estimated plate, well over the trigger.
    s.note([400.0])
    assert s.bursting
    # The next five frames are all processed, stride notwithstanding.
    assert [s.should_process(i) for i in range(1, 6)] == [True] * 5
    # ...then it falls back to the stride.
    assert s.should_process(7) is False


def test_a_distant_vehicle_does_not_trigger():
    s = AdaptiveSampler(stride=10, burst_frames=5)
    s.should_process(0)
    # 100px vehicle -> ~25px estimated plate, nothing worth catching.
    s.note([100.0])
    assert not s.bursting
    assert s.should_process(1) is False


def test_a_vehicle_that_stays_large_re_arms_rather_than_stacking():
    s = AdaptiveSampler(stride=100, burst_frames=3)
    s.should_process(0)
    s.note([400.0])
    s.should_process(1)
    s.note([400.0])          # re-arm, not 3+3
    left = [s.should_process(i) for i in range(2, 6)]
    assert left[:3] == [True, True, True]
    assert left[3] is False
    assert s.stats.bursts == 1


def test_no_vehicles_is_not_a_trigger():
    s = AdaptiveSampler(stride=10, burst_frames=5)
    s.should_process(0)
    s.note([])
    assert not s.bursting


def test_it_reports_what_it_spent():
    s = AdaptiveSampler(stride=5, burst_frames=4)
    for i in range(20):
        if s.should_process(i):
            s.note([400.0] if i == 5 else [50.0])
    d = s.describe()
    assert d["looked"] == 20
    assert d["bursts"] == 1
    assert d["processed"] > 20 // 5  # more than a bare stride would have taken
