"""Per-stage timings, reported as percentiles rather than averages.

A mean latency is the wrong summary for a real-time video system, and it is
wrong in the direction that hides the problem. If a pipeline handles ninety-nine
frames in 40 ms and the hundredth in 900 ms, the mean says 48 ms and everything
looks fine. But it is the 900 ms frame that drops the stream, overruns the
sampler's budget and makes an operator say the wall "freezes sometimes". The
average is a number that describes no frame that actually happened.

So every stage keeps a bounded window of its recent samples and reports P50,
P95 and P99 computed from that data. P99 is the one to read: it is roughly the
worst frame in each hundred, which on a 25 fps feed is something the viewer
sees every four seconds.

The window is bounded on purpose. An unbounded history would drift toward a
lifetime average and stop reflecting the camera's behaviour now - which is what
matters when a feed degrades in the afternoon sun. It also keeps memory flat
over a run that lasts days.

Percentiles here are computed by nearest-rank on a sorted copy, without numpy.
The sample counts are small (a few thousand), the copy is only made when
something asks for a report, and avoiding the dependency keeps this module
importable in the worker's light build, where torch and numpy are not installed
at all.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Iterator

#: Samples kept per stage. Large enough that P99 means something (a hundred
#: samples cannot express a 99th percentile), small enough to stay recent.
DEFAULT_WINDOW = 2000


class Series:
    """A bounded window of recent measurements for one stage."""

    __slots__ = ("values", "count", "total", "worst")

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self.values: deque[float] = deque(maxlen=window)
        #: Lifetime count and total, kept separately from the window so the
        #: run's throughput is not distorted by the window discarding history.
        self.count = 0
        self.total = 0.0
        self.worst = 0.0

    def add(self, value: float) -> None:
        self.values.append(value)
        self.count += 1
        self.total += value
        if value > self.worst:
            self.worst = value

    def percentile(self, p: float) -> float:
        """Nearest-rank percentile over the current window."""
        if not self.values:
            return 0.0
        ordered = sorted(self.values)
        # Nearest-rank, the textbook definition: the smallest value at or
        # above the p-th position, rank = ceil(p/100 * N). Rounding instead of
        # taking the ceiling pulls the high percentiles down by one position,
        # which is the wrong direction for a number whose job is to show the
        # tail.
        rank = max(1, min(len(ordered), math.ceil(p / 100.0 * len(ordered))))
        return ordered[rank - 1]

    def describe(self) -> dict:
        return {
            "count": self.count,
            "mean_ms": round(self.total / self.count, 2) if self.count else 0.0,
            "p50_ms": round(self.percentile(50), 2),
            "p95_ms": round(self.percentile(95), 2),
            "p99_ms": round(self.percentile(99), 2),
            "max_ms": round(self.worst, 2),
        }


class _Timer:
    """Context manager returned by `Metrics.time`."""

    __slots__ = ("_metrics", "_stage", "_started")

    def __init__(self, metrics: "Metrics", stage: str) -> None:
        self._metrics = metrics
        self._stage = stage
        self._started = 0.0

    def __enter__(self) -> "_Timer":
        self._started = time.perf_counter()
        return self

    def __exit__(self, *exc) -> bool:
        elapsed_ms = (time.perf_counter() - self._started) * 1000.0
        self._metrics.record(self._stage, elapsed_ms)
        # Never swallow the exception: a stage that raised still gets its
        # timing recorded, but the failure must propagate.
        return False


class Metrics:
    """Timings for one camera's pipeline, stage by stage.

    Not thread-safe by design. One pipeline belongs to one camera and is fed
    frames in order by one thread; adding a lock would cost every frame to
    protect against a situation the architecture already forbids.
    """

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = window
        self._stages: dict[str, Series] = {}
        self._started = time.perf_counter()

    def time(self, stage: str) -> _Timer:
        """Time a block: `with metrics.time("ocr"): ...`"""
        return _Timer(self, stage)

    def record(self, stage: str, elapsed_ms: float) -> None:
        series = self._stages.get(stage)
        if series is None:
            series = self._stages[stage] = Series(self._window)
        series.add(elapsed_ms)

    def stage(self, name: str) -> Series | None:
        return self._stages.get(name)

    def __iter__(self) -> Iterator[tuple[str, Series]]:
        return iter(self._stages.items())

    def reset(self) -> None:
        self._stages.clear()
        self._started = time.perf_counter()

    def describe(self) -> dict:
        """Every stage, plus the throughput the whole run achieved.

        `frame` is the stage that contains all the others, so its percentiles
        are the ones that answer "can this keep up with the camera": a 25 fps
        feed needs P99 under 40 ms per frame to never fall behind.
        """
        elapsed = max(time.perf_counter() - self._started, 1e-9)
        frame = self._stages.get("frame")
        out: dict = {
            "wall_seconds": round(elapsed, 1),
            "stages": {name: s.describe() for name, s in sorted(self._stages.items())},
        }
        if frame is not None and frame.count:
            out["fps"] = round(frame.count / elapsed, 2)
            # The rate this machine could sustain if it never waited for
            # anything else - useful for telling "the box is too slow" apart
            # from "the network delivered frames slowly".
            mean_ms = frame.total / frame.count
            out["capacity_fps"] = round(1000.0 / mean_ms, 2) if mean_ms else 0.0
        return out
