"""Wrong way is judged per region of the frame, on tracks that can be trusted.

8 Oct, 6 h of live grid tracks: one camera-wide flow put the whole oncoming lane of every two-way road
"against the flow", and sparse light-mode frames let the tracker pass one id from car to car. Both
read as wrong-way vehicles; by eye none of 11 sampled was one.
"""
from __future__ import annotations

from types import SimpleNamespace

from anpr.incidents import IncidentDetector

FRAME = (1280, 720)


def drive(det, track_id, start, step, *, t0, n=12, dt=0.2, y=None, label="car"):
    """A vehicle moving in a straight line, sampled every dt seconds; returns incidents raised."""
    out = []
    x, yy = start
    for i in range(n):
        box = (x - 40, yy - 25, x + 40, yy + 25)
        out += det.update([SimpleNamespace(track_id=track_id, box=box, label=label)], t0 + i * dt, frame_size=FRAME)
        x, yy = x + step[0], yy + step[1]
    return out


def wrong_way(incidents):
    return [i for i in incidents if i.kind == "WRONG_WAY"]


def test_the_oncoming_lane_of_a_two_way_road_is_not_wrong_way():
    det = IncidentDetector("cam-two-way")
    t = 0.0
    for k in range(20):   # eastbound in the top lane, westbound in the bottom lane
        drive(det, 100 + k, (60, 150), (40, 0), t0=t, n=29)
        drive(det, 200 + k, (1220, 550), (-40, 0), t0=t + 0.05, n=29)
        t += 8
    assert not wrong_way(drive(det, 999, (1100, 550), (-40, 0), t0=t))   # one more westbound, in its lane


def test_against_the_flow_in_its_own_lane_is_wrong_way():
    det = IncidentDetector("cam-one-way")
    t = 0.0
    for k in range(20):
        drive(det, 100 + k, (60, 150), (40, 0), t0=t, n=29)
        t += 8
    assert wrong_way(drive(det, 999, (1100, 150), (-40, 0), t0=t))


def test_an_identity_swap_across_the_frame_is_not_judged():
    det = IncidentDetector("cam-swap")
    t = 0.0
    for k in range(20):
        drive(det, 100 + k, (60, 150), (40, 0), t0=t, n=29)
        t += 8
    # sparse frames: the same id lands on a different vehicle 4 s and 600 px later, heading back
    out = []
    for i, x in enumerate((1100, 500, 1150, 520, 1180)):
        out += det.update([SimpleNamespace(track_id=999, box=(x - 40, 125, x + 40, 175), label="car")], t + 4 * i, frame_size=FRAME)
    assert not out
