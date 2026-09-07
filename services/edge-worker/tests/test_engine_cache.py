"""Per-camera ANPR engines that survive between cycles.

The cache exists to stop a worker throwing away what each camera has learned
about itself every 25 frames. These tests hold the two things that makes
correct: a reused engine must be told the stream restarted, and the cache must
stay bounded on a box that cannot hold thirty loaded engines.

`build_engine` is stubbed throughout - the real one loads several hundred
megabytes of weights, and none of the behaviour here depends on them.
"""
import app.anpr_engine as ae


class FakeEngine:
    def __init__(self, camera: str) -> None:
        self.camera = camera
        self.new_streams = 0

    def new_stream(self) -> None:
        self.new_streams += 1


def _stub(monkeypatch, *, available=True):
    built = []

    def build(**_kw):
        if not available:
            return None
        e = FakeEngine(f"engine{len(built)}")
        built.append(e)
        return e

    monkeypatch.setattr(ae, "build_engine", build)
    return built


def test_the_same_camera_gets_the_same_engine_back(monkeypatch):
    built = _stub(monkeypatch)
    cache = ae.EngineCache(capacity=4)
    first = cache.get("cam01")
    assert cache.get("cam01") is first
    assert len(built) == 1
    assert cache.hits == 1 and cache.misses == 1


def test_a_reused_engine_is_told_the_stream_restarted(monkeypatch):
    # Between cycles the vehicles are gone. Inheriting their track ids would
    # attach an old plate to a new car, which is the one error a route must
    # never contain.
    _stub(monkeypatch)
    cache = ae.EngineCache(capacity=4)
    engine = cache.get("cam01")
    assert engine.new_streams == 0        # a fresh build has nothing to reset
    cache.get("cam01")
    cache.get("cam01")
    assert engine.new_streams == 2


def test_different_cameras_never_share_an_engine(monkeypatch):
    _stub(monkeypatch)
    cache = ae.EngineCache(capacity=4)
    assert cache.get("cam01") is not cache.get("cam02")


def test_it_evicts_the_least_recently_used(monkeypatch):
    _stub(monkeypatch)
    cache = ae.EngineCache(capacity=2)
    a = cache.get("cam01")
    cache.get("cam02")
    cache.get("cam01")                    # cam01 is now the most recent
    cache.get("cam03")                    # so cam02 goes
    assert cache.evictions == 1
    assert "cam02" not in cache.describe()["cameras"]
    assert cache.get("cam01") is a


def test_more_cameras_than_capacity_degrades_to_rebuilding(monkeypatch):
    # The 30-camera sandbox. Every lookup misses; nothing is corrupted and
    # nothing is retained. Same behaviour as before the cache existed.
    built = _stub(monkeypatch)
    cache = ae.EngineCache(capacity=2)
    for cycle in range(2):
        for cam in ("cam01", "cam02", "cam03"):
            cache.get(cam)
    assert cache.hits == 0
    assert len(built) == 6
    assert cache.describe()["loaded"] == 2


def test_an_unavailable_engine_is_not_retried_on_every_camera(monkeypatch):
    # Without the analytics extras `build_engine` returns None. Retrying it
    # per camera per cycle would log the same failure for ever.
    calls = []

    def build(**_kw):
        calls.append(1)
        return None

    monkeypatch.setattr(ae, "build_engine", build)
    cache = ae.EngineCache()
    assert cache.get("cam01") is None
    assert cache.get("cam02") is None
    assert cache.get("cam01") is None
    assert len(calls) == 1


def test_capacity_is_never_zero(monkeypatch):
    _stub(monkeypatch)
    assert ae.EngineCache(capacity=0).capacity == 1
