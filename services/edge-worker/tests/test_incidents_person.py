"""Person tracking and the person-in-traffic rule.

Two things are being held here: a pedestrian must never be judged by the
vehicle motion rules, and a person genuinely in the path of moving traffic
must be raised - once, not every frame.

The scenarios use traffic moving at a crawl past a stationary person, because
that is what the condition looks like on a real junction and because a track
needs MIN_TRACK_SECONDS of history before any rule may judge it. A vehicle at
full speed is past the person before that history exists, which is correct
behaviour and not the case this rule is for.
"""
from dataclasses import dataclass

from anpr.incidents import IncidentDetector, PERSON_EXPOSED_SECONDS


@dataclass
class T:
    track_id: int
    box: tuple
    label: str


PERSON = (300, 300, 360, 380)


def _run(det, *, steps, person_box, vehicle_at, t0=1000.0, step=0.25):
    """Feed a person plus a vehicle placed by `vehicle_at(i)`; collect incidents.

    `vehicle_at` returning None means no vehicle in frame that step.
    """
    raised = []
    for i in range(steps):
        now = t0 + i * step
        tracks = [T(1, person_box, "person")]
        vb = vehicle_at(i)
        if vb is not None:
            tracks.append(T(2, vb, "car"))
        raised += det.update(tracks, timestamp=now)
    return raised


def _crawling(i):
    """A vehicle creeping past the person - moving, but slowly enough to be
    beside them for a few seconds, as in congestion."""
    x = 150 + 12 * i
    return (x, 300, x + 80, 380)


def test_a_person_in_the_path_of_crawling_traffic_is_raised():
    det = IncidentDetector("cam-test")
    out = _run(det, steps=40, person_box=PERSON, vehicle_at=_crawling)
    hits = [i for i in out if i.kind == "PERSON_ON_CARRIAGEWAY"]
    assert hits, "a person overlapped by moving traffic should be raised"
    inc = hits[0]
    assert inc.severity == "HIGH"
    assert 1 in inc.track_ids
    assert inc.evidence["exposed_s"] >= PERSON_EXPOSED_SECONDS


def test_it_is_raised_once_not_every_frame():
    det = IncidentDetector("cam-test")
    out = _run(det, steps=40, person_box=PERSON, vehicle_at=_crawling)
    assert len([i for i in out if i.kind == "PERSON_ON_CARRIAGEWAY"]) == 1


def test_a_person_on_the_footpath_is_not_raised():
    det = IncidentDetector("cam-test")
    # Traffic crawls along the carriageway; the person stands well clear of it.
    out = _run(det, steps=40, person_box=(10, 300, 40, 380), vehicle_at=_crawling)
    assert [i for i in out if i.kind == "PERSON_ON_CARRIAGEWAY"] == []


def test_stationary_traffic_does_not_expose_anyone():
    det = IncidentDetector("cam-test")
    # Overlapping a parked vehicle is not danger from it.
    out = _run(det, steps=40, person_box=PERSON,
               vehicle_at=lambda i: (300, 300, 380, 380))
    assert [i for i in out if i.kind == "PERSON_ON_CARRIAGEWAY"] == []


def test_vehicle_rules_never_fire_on_a_pedestrian():
    det = IncidentDetector("cam-test")
    out = _run(det, steps=60, person_box=PERSON, vehicle_at=_crawling)
    for inc in out:
        if inc.kind in {"SUDDEN_STOP", "STOPPED_IN_LANE", "WRONG_WAY", "COLLISION_CANDIDATE"}:
            assert 1 not in inc.track_ids, f"{inc.kind} fired on a pedestrian"
