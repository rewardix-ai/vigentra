"""Per-stage timings. The point of these is that the tail is preserved.

A summary that smooths away the one slow frame in a hundred is worse than no
summary, because it reports health during exactly the failure it should show.
"""
import time

from anpr.metrics import Metrics, Series


def test_percentiles_come_from_the_data_not_from_the_mean():
    s = Series()
    for v in range(1, 101):          # 1..100 ms
        s.add(float(v))
    assert s.percentile(50) == 50.0
    assert s.percentile(95) == 95.0
    assert s.percentile(99) == 99.0


def test_the_slow_one_percent_survives_into_p99():
    # 1000 frames, the worst 20 of them slow. A mean says ~57ms and reports a
    # healthy pipeline; P99 has to show the 900ms frames, because those are
    # the ones a viewer sees as a freeze.
    s = Series()
    for _ in range(980):
        s.add(40.0)
    for _ in range(20):
        s.add(900.0)
    d = s.describe()
    assert d["p50_ms"] == 40.0
    assert d["p99_ms"] == 900.0
    assert d["max_ms"] == 900.0
    assert d["mean_ms"] < 60.0


def test_a_single_outlier_is_the_max_not_the_p99():
    # Honesty in the other direction: one slow frame in a hundred is the 100th
    # percentile, and calling it P99 would overstate how often it happens.
    s = Series()
    for _ in range(99):
        s.add(40.0)
    s.add(900.0)
    d = s.describe()
    assert d["p99_ms"] == 40.0
    assert d["max_ms"] == 900.0


def test_an_empty_series_reports_zero_rather_than_raising():
    assert Series().describe()["p99_ms"] == 0.0


def test_the_window_is_bounded_but_the_count_is_not():
    s = Series(window=10)
    for i in range(100):
        s.add(float(i))
    assert len(s.values) == 10
    assert s.count == 100          # lifetime throughput is not distorted
    assert s.worst == 99.0


def test_timing_a_block_records_it():
    m = Metrics()
    with m.time("ocr"):
        time.sleep(0.01)
    d = m.describe()["stages"]["ocr"]
    assert d["count"] == 1
    assert d["p50_ms"] >= 5.0


def test_a_stage_that_raises_is_still_timed_and_still_raises():
    m = Metrics()
    try:
        with m.time("detect"):
            raise ValueError("model exploded")
    except ValueError:
        pass
    else:                            # pragma: no cover - guards the assertion
        raise AssertionError("the exception must propagate")
    assert m.describe()["stages"]["detect"]["count"] == 1


def test_frame_timings_produce_a_throughput_figure():
    m = Metrics()
    for _ in range(10):
        m.record("frame", 40.0)
    d = m.describe()
    assert d["capacity_fps"] == 25.0     # 40ms per frame
    assert "fps" in d


def test_reset_drops_every_stage():
    m = Metrics()
    m.record("frame", 10.0)
    m.reset()
    assert m.describe()["stages"] == {}
