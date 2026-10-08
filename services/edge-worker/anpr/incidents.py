"""Incident detection: flag traffic events on a camera for a human to look at.

This sits beside ANPR and shares its governing rule. ANPR must never invent a
registration; incident detection must never assert an accident. Both failures cost the
same thing -- an operator acting on something the pixels did not support -- so the output
here is a CANDIDATE with its evidence attached, never a finding.

That is not hedging. There is no accident footage from this estate, so nothing below has
been validated against a real collision, and the thresholds are reasoned rather than
measured. A module that announced "ACCIDENT DETECTED" on that basis would be claiming a
confidence nobody has earned. `tools/estate_report.py` counts what it raises across the estate, which is the
only number currently knowable: how often it cries wolf on an ordinary day.

WHAT IT WATCHES

Everything is derived from the tracker's own boxes over time; no extra model, so it costs
almost nothing on top of detection that is already running.

    SUDDEN_STOP        (retired) a vehicle decelerating far harder than traffic around it
    STOPPED_IN_LANE    a vehicle stationary while other vehicles keep moving past it
    COLLISION_CANDIDATE two vehicles overlapping AND both losing speed sharply together
    WRONG_WAY          a vehicle travelling against the camera's established flow
    PERSON_ON_CARRIAGEWAY
                       a person in the path of moving traffic

SCALE, AND WHY SPEED IS NOT IN PIXELS

A pixel does not mean the same thing across the frame. On cam06 a vehicle at the top is
40px tall and the same vehicle near the lens is 300px, so a fixed px/s threshold fires
constantly in the near field and never in the far field. Speed here is therefore measured
in VEHICLE HEIGHTS PER SECOND -- box height is a usable proxy for how close the vehicle
is, so dividing by it removes most of the perspective term. It is not a calibration and
gives no real-world km/h; it makes one threshold behave roughly the same across the frame,
which is all that is being claimed.

FLOW IS LEARNED, NOT CONFIGURED

WRONG_WAY needs to know which way traffic goes, and that differs per camera and cannot be
hand-set for 27 of them. The detector accumulates the median heading of confirmed tracks
and only starts judging direction once it has seen enough of them, so a camera teaches it
its own flow. Until then it reports nothing rather than guessing.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from collections import deque
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- tuning
#
# NONE of these are measured against a real accident, because no footage of one exists
# for this estate. They are set to be quiet on ordinary traffic -- the failure mode that
# matters for a system nobody will keep watching if it cries wolf -- and every one of
# them should be re-derived the first time a real incident is recorded here.
STOP_SPEED = 0.12          # vehicle-heights/s below which a vehicle counts as stopped
MOVING_SPEED = 0.45        # ...and above which it counts as genuinely moving
STOPPED_SECONDS = 6.0      # stationary this long, with traffic flowing, is worth a look
# HARD_DECEL was 1.6 and could never fire. Measured on synthetic motion at 6fps, peak
# deceleration this detector can actually report:
#
#     full speed to a dead stop      0.909      the event worth catching
#     gentle braking to a stop (8s)  0.120      ordinary traffic
#     constant speed, approaching    0.077      no deceleration at all
#
# A window-averaged speed cannot report a drop faster than speed/window, so 1.6 was above
# the measurement's own dynamic range -- yet it fired on live cam05 traffic, because the
# approach bias fixed in _speed was inflating it. 0.55 sits 7x above ordinary braking and
# below a genuine stop with margin. Re-derive from real incident footage when any exists.
HARD_DECEL = 0.55          # vehicle-heights/s^2
COLLISION_IOU = 0.18       # boxes overlapping this much in the image plane
# Lower than HARD_DECEL because a collision has to satisfy three things at once -- overlap
# plus BOTH vehicles losing speed -- so each individual bar can be less extreme.
COLLISION_DECEL = 0.40
FLOW_MIN_TRACKS = 12       # tracks needed before a camera's flow is considered known
WRONG_WAY_DEG = 115.0      # heading this far from flow is against it
MIN_TRACK_SECONDS = 1.2    # ignore tracks too short to have a trustworthy velocity
COOLDOWN_S = 20.0          # per track+kind, so one event is not reported repeatedly
# A track is only judged on its motion when it is DENSE and CONTINUOUS. Measured on 6 h of live
# light-mode tracks (8 Oct): a quiet camera gets a frame every few seconds, the tracker hands one id
# from vehicle to vehicle, and those swaps read as hard stops and reversals. By eye, 0 of 11
# wrong-way candidates were real; most were swaps, jitter or junction turns.
TRUST_SAMPLES = 4          # recent samples the judgement rests on
TRUST_MAX_GAP_S = 1.0      # no gap between those samples longer than this
TRUST_MAX_STEP_VH = 1.5    # no step longer than this many vehicle heights (an id swap jumps)
# Flow is learned PER REGION of the frame (FLOW_GRID cells), not per camera. A two-way road has two
# directions; one camera-wide mean put the whole oncoming lane "against the flow" (86 % of the
# 547 wrong-way tracks on 8 Oct moved the way at least 15 % of vehicles in their own region did).
FLOW_GRID = (4, 3)
# 8 Oct, by eye on 48 live snapshots, none could be confirmed real. Sudden stops were mostly vehicles a
# few pixels tall (box jitter reads as braking) or cut off at the frame edge (a box shrinking as the
# vehicle leaves reads as a stop); wrong ways were mostly junction turns in regions that had learned
# 12-21 vehicles since the last restart. So motion is judged only on vehicles that are big enough and
# wholly in view, and wrong way only where a region has a clear, well-learned direction.
# WRONG SIDE by the keep-left rule (Rules of the Road Regulations 1989, reg. 2: keep left, let oncoming
# traffic pass on your right; reg. 17: one-way roads only in the signed direction; MV Act s. 184). A camera
# looking along a road sees its own-side traffic recede and the oncoming lane approach. Approaching is read
# from the box GROWING over the track, receding from it shrinking - robust to perspective, unlike an angle.
# Each region learns which sense its traffic has (one vote per vehicle); a vehicle going the other sense in
# a region that is clearly one-sense is on the wrong side. Crossing traffic (box size steady) is never
# judged, so junction turns (reg. 3) are not flagged.
SENSE_RATIO = 1.12          # last-third box height / first-third: above = approaching, below 1/x = receding
WRONG_SIDE_MIN_REGION = 40  # vehicles a region must have learned
WRONG_SIDE_DOMINANT = 0.85  # ...with at least this share going one sense
WRONG_SIDE_SHARE = 0.08     # wrong side = a sense under this share of the region's traffic
# Retired 8 Oct at the operator's request: on live grid footage every sudden stop checked by eye was
# ordinary braking in traffic, distant-vehicle jitter or a box cut off at the frame edge.
RAISE_SUDDEN_STOP = False
JUDGE_MIN_HEIGHT_FRAC = 0.06   # box at least this share of the frame height
EDGE_MARGIN_FRAC = 0.02        # box this close to any frame edge is cut off: not judged
WRONG_WAY_MIN_REGION = 50      # vehicles a region must have learned before it judges direction
WRONG_WAY_DOMINANT = 0.6       # ...and at least this share of them must go one way (not a junction)
WRONG_WAY_SHARE = 0.05     # wrong way = a direction under 5 % of this region's traffic (within 45 deg)
WRONG_WAY_STRAIGHTNESS = 1.3   # path length / displacement above this is a turn, not a wrong way

#: Labels that behave like traffic. The motion rules below - decelerating,
#: stopping in a lane, travelling against the flow - are statements about a
#: VEHICLE, and firing them on a pedestrian produces nonsense: a person who
#: stops walking has not stopped in a lane, and a person crossing the road is
#: not going the wrong way.
VEHICLE_LABELS = frozenset({"car", "motorcycle", "scooter", "bus", "truck", "auto-rickshaw", "bicycle"})
PERSON_LABEL = "person"
# CROWD_GATHERING: people bunching up where they usually do not - the visible part of a fight, a
# collapse or an accident's aftermath. Fight itself is not recognisable at grid distance and resolution,
# so this does not claim it; a person decides from the snapshot. Learned per region, so a bus stop or a
# signal crossing that is always busy does not count.
CROWD_MIN = 6               # people in one group
CROWD_RADIUS_H = 2.5        # group = within this many person-heights of its centre person
CROWD_FACTOR = 2.0          # ...and at least this many times the region's usual group size
CROWD_SECONDS = 10.0        # held this long
CROWD_HISTORY_S = 1800.0    # usual = 90th percentile of this region's group sizes over this window
CROWD_COOLDOWN_S = 180.0

#: Image-plane overlap between a person and a MOVING vehicle. Overlap is not
#: contact and this is not a collision detector - from a typical CCTV angle it
#: is a usable proxy for "in the carriageway rather than beside it", which is
#: the distinction that matters and the one a footpath pedestrian fails.
PERSON_VEHICLE_IOU = 0.05
#: How long exposure has to persist. A pedestrian crossing at a signal is clear
#: of the carriageway well inside this; someone standing in live traffic is not.
PERSON_EXPOSED_SECONDS = 2.0
#: Exposure is not one continuous overlap. A vehicle passing a stationary person
#: overlaps for a fraction of a second, so what marks danger is that vehicles
#: KEEP passing. Exposure is only considered over once nothing has passed close
#: for this long.
PERSON_CLEAR_SECONDS = 3.0


@dataclass
class Incident:
    """A candidate for review. `kind` says what pattern matched, never what happened."""
    camera_id: str
    kind: str
    severity: str                       # LOW | MEDIUM | HIGH
    track_ids: list
    first_seen: float
    last_seen: float
    reason: str
    evidence: dict = field(default_factory=dict)
    status: str = "CANDIDATE"

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id, "kind": self.kind, "severity": self.severity,
            "status": self.status, "track_ids": list(self.track_ids),
            "first_seen": self.first_seen, "last_seen": self.last_seen,
            "duration_s": round(self.last_seen - self.first_seen, 2),
            "reason": self.reason, "evidence": self.evidence,
            "NOTE": "A pattern in the tracking, not a confirmed event. Thresholds are "
                    "reasoned, not validated against real incident footage.",
        }


@dataclass
class _State:
    """Rolling motion history for one track, in scale-normalised units."""
    speeds: deque = field(default_factory=lambda: deque(maxlen=12))
    times: deque = field(default_factory=lambda: deque(maxlen=12))
    centres: deque = field(default_factory=lambda: deque(maxlen=12))
    heights: deque = field(default_factory=lambda: deque(maxlen=12))
    stopped_since: float | None = None
    #: Has this vehicle ever been seen genuinely moving? A parked car never has, and
    #: without this STOPPED_IN_LANE fires on every parked vehicle beside a busy road --
    #: 40 alarms in ten minutes on the estate, which is a detector nobody will read.
    was_moving: bool = False
    #: When a person first overlapped moving traffic, or None.
    exposed_since: float | None = None
    #: The most recent moment they did. A single vehicle only overlaps for a
    #: fraction of a second as it passes, so exposure is judged over the gap
    #: between passes rather than one continuous overlap.
    last_exposed: float = 0.0
    #: When this track's foot point first entered the camera's intrusion zone, or None.
    zone_since: float | None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    label: str = "?"
    #: big enough and wholly in view on the latest frame (see IncidentDetector._in_view)
    in_view: bool = False
    #: regions this vehicle has already voted a direction in: one vote per vehicle per region
    voted: set = field(default_factory=set)
    #: ...and an approaching/receding sense in
    voted_sense: set = field(default_factory=set)


def _in_polygon(x: float, y: float, poly) -> bool:
    """Ray casting; `poly` is [[x, y], ...]."""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / ua) if ua > 0 else 0.0


class IncidentDetector:
    """Per-camera. Feed it the tracker's confirmed tracks each frame."""

    def __init__(self, camera_id: str, intrusion: dict | None = None):
        self.camera_id = camera_id
        #: The camera's restricted zone, from its profile (config/camera_profiles.yaml):
        #:   intrusion: {zone: [[x, y], ...], classes: [person], dwell_seconds: 2, hours: [22, 6]}
        #: `zone` is a polygon in fractions of the frame (or pixels when no frame size is given),
        #: `hours` the local hours it is armed between (omit for always). No zone, no rule.
        self.intrusion = intrusion if intrusion and len(intrusion.get("zone") or []) >= 3 else None
        self._tracks: dict[int, _State] = {}
        self._flow: deque = deque(maxlen=200)      # headings of completed tracks
        self._flow_count = 0
        self._cells: dict[tuple[int, int], deque] = {}   # region -> recent headings there
        self._extent = [1.0, 1.0]                         # frame size when none is given
        self._frame = None
        #: INCIDENT_FLOW_DIR (opt-in): learned directions are kept per camera across restarts. The
        #: light reader restarts at every grid refusal window, and each restart used to start blind.
        self._flow_file = None
        flow_dir = os.environ.get("INCIDENT_FLOW_DIR")
        if flow_dir:
            self._flow_file = Path(flow_dir) / (re.sub(r"[^A-Za-z0-9_.-]", "_", camera_id) + ".json")
            try:
                saved = json.loads(self._flow_file.read_text())
                for key, hs in saved.items():
                    if key.startswith("s:"):
                        cx, cy = (int(v) for v in key[2:].split(","))
                        self._cells[("s", cx, cy)] = deque(hs, maxlen=300)   # moved to _senses below
                        continue
                    cx, cy = (int(v) for v in key.split(","))
                    self._cells[(cx, cy)] = deque(hs, maxlen=300)
            except (OSError, ValueError):
                pass
        self._learned = 0
        self._senses: dict[tuple[int, int], deque] = {}   # region -> +1 approaching / -1 receding, per vehicle
        for key in [k for k in list(self._cells) if isinstance(k, tuple) and len(k) == 3]:
            self._senses[key[1:]] = self._cells.pop(key)   # persisted as ("s", cx, cy)
        self._crowd_hist: dict[tuple[int, int], deque] = {}   # region -> (time, group size)
        self._crowd_since: dict[tuple[int, int], float] = {}
        self._cooldown: dict[tuple, float] = {}

    def reset(self, *, keep_flow: bool = True) -> None:
        """Drop per-track motion state after a scene discontinuity.

        These feeds loop, and the cut looks like a camera reboot: every track
        id restarts, so state keyed by the old ids is stale and would compute
        a spurious deceleration across the seam. The learned flow direction is
        kept by default - the same camera's footage looping does not reverse
        its traffic - so WRONG_WAY does not have to relearn each loop.
        """
        self._tracks.clear()
        self._cooldown.clear()
        if not keep_flow:
            self._flow.clear()
            self._flow_count = 0
            self._cells.clear()

    # ------------------------------------------------------------------ helpers

    def _speed(self, st: _State) -> float | None:
        """Vehicle-heights per second, over the whole retained window.

        Measured across the window rather than between the last two frames: adjacent
        frames at 6fps give a displacement dominated by box jitter, and differentiating
        that produces enormous fake accelerations.
        """
        if len(st.centres) < 2:
            return None
        dt = st.times[-1] - st.times[0]
        if dt <= 0.05:
            return None
        # Each step is normalised by the height AT THAT STEP, then summed -- not the
        # total displacement over the window's mean height. The difference matters on
        # exactly the cameras that matter: a vehicle approaching the lens grows, so
        # dividing a whole window's travel by a mean height that is smaller than the
        # current one understates late speed and manufactures a deceleration out of
        # constant motion. That bias is what put SUDDEN_STOP above its threshold on
        # ordinary cam05 traffic while a genuine full-speed stop scored 0.91.
        cs, hs = list(st.centres), list(st.heights)
        total = 0.0
        for i in range(1, len(cs)):
            h = max((hs[i] + hs[i - 1]) / 2.0, 1.0)
            total += math.hypot(cs[i][0] - cs[i - 1][0], cs[i][1] - cs[i - 1][1]) / h
        return total / dt

    @staticmethod
    def _decel(st: _State) -> float | None:
        """How fast speed is dropping, vehicle-heights/s^2. Positive = slowing.

        Each speed sample carries its OWN timestamp. `speeds` and `times` were separate
        deques and drifted apart -- `times` gets an entry every frame while `speeds` only
        gets one once there are two centres to difference -- so the divisor came from a
        different span than the numerator and the result was wrong by a varying factor.
        That produced deceleration spikes on ordinary traffic while a real full-speed stop
        scored below threshold.
        """
        if len(st.speeds) < 4:
            return None
        pts = list(st.speeds)
        n = len(pts) // 2
        early, late = pts[:n], pts[-n:]
        t_early = sum(t for t, _ in early) / n
        t_late = sum(t for t, _ in late) / n
        dt = t_late - t_early
        if dt <= 0.05:
            return None
        v_early = sum(v for _, v in early) / n
        v_late = sum(v for _, v in late) / n
        return (v_early - v_late) / dt

    def _heading(self, st: _State) -> float | None:
        if len(st.centres) < 3:
            return None
        dx = st.centres[-1][0] - st.centres[0][0]
        dy = st.centres[-1][1] - st.centres[0][1]
        h = max(sum(st.heights) / len(st.heights), 1.0)
        if math.hypot(dx, dy) / h < 0.25:
            return None                     # barely moved; heading is noise
        return math.degrees(math.atan2(dy, dx))

    def path(self, track_id) -> list[tuple[float, float]]:
        """Recent centres of a track, oldest first (for drawing evidence)."""
        st = self._tracks.get(track_id)
        return list(st.centres) if st else []

    @staticmethod
    def _trusted(st: _State) -> bool:
        """Dense, continuous motion over EVERY sample that heading and deceleration are computed
        from: no long gap, no jump an identity swap would make. (Checking only the last few let a
        swap earlier in the window through: cam01, 8 Oct, a bus-to-car jump read as wrong way.)"""
        if len(st.times) < TRUST_SAMPLES:
            return False
        times, centres, heights = list(st.times), list(st.centres), list(st.heights)
        for i in range(1, len(times)):
            if times[i] - times[i - 1] > TRUST_MAX_GAP_S:
                return False
            step = math.hypot(centres[i][0] - centres[i - 1][0], centres[i][1] - centres[i - 1][1])
            if step / max(heights[i], 1.0) > TRUST_MAX_STEP_VH:
                return False
        return True

    def _in_view(self, box, frame_size) -> bool:
        """Big enough that box jitter is not motion, and not cut off by a frame edge. Without a known
        frame size there is no edge to judge against, so everything counts as in view."""
        if not frame_size:
            return True
        w, h = frame_size
        x1, y1, x2, y2 = box
        mx, my = EDGE_MARGIN_FRAC * w, EDGE_MARGIN_FRAC * h
        return (y2 - y1) >= JUDGE_MIN_HEIGHT_FRAC * h and x1 > mx and y1 > my and x2 < w - mx and y2 < h - my

    def _save_flow(self) -> None:
        if self._flow_file is None:
            return
        try:
            self._flow_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._flow_file.with_suffix(".tmp")
            data = {f"{cx},{cy}": [round(h, 1) for h in hs] for (cx, cy), hs in self._cells.items()}
            data.update({f"s:{cx},{cy}": list(ss) for (cx, cy), ss in self._senses.items()})
            tmp.write_text(json.dumps(data))
            tmp.replace(self._flow_file)
        except OSError:
            pass

    def _cell(self, st: _State, frame_size) -> tuple[int, int]:
        w, h = frame_size if frame_size else self._extent
        cx = sum(c[0] for c in st.centres) / len(st.centres)
        cy = sum(c[1] for c in st.centres) / len(st.centres)
        return (min(FLOW_GRID[0] - 1, int(cx / max(w, 1.0) * FLOW_GRID[0])),
                min(FLOW_GRID[1] - 1, int(cy / max(h, 1.0) * FLOW_GRID[1])))

    @staticmethod
    def _sense(st: _State) -> int:
        """+1 approaching the camera (box growing), -1 receding (shrinking), 0 neither (crossing)."""
        hs = list(st.heights)
        if len(hs) < 6:
            return 0
        k = len(hs) // 3
        early, late = sum(hs[:k]) / k, sum(hs[-k:]) / k
        if late >= SENSE_RATIO * early:
            return 1
        if early >= SENSE_RATIO * late:
            return -1
        return 0

    def _layout(self) -> str:
        """Does the learned layout match keep-left? From the camera, the oncoming (approaching) lane
        lies to the right of the own-side (receding) lane."""
        ax, rx = [], []
        for (cx, cy), ss in self._senses.items():
            if len(ss) < WRONG_SIDE_MIN_REGION:
                continue
            share = sum(1 for v in ss if v > 0) / len(ss)
            if share >= WRONG_SIDE_DOMINANT:
                ax.append(cx)
            elif share <= 1 - WRONG_SIDE_DOMINANT:
                rx.append(cx)
        if not ax or not rx:
            return "one-way or not yet learned"
        return "keep-left" if sum(ax) / len(ax) > sum(rx) / len(rx) else "approaching on the left (one-way road or a mirrored view)"

    @staticmethod
    def _straightness(st: _State) -> float:
        c = list(st.centres)
        path = sum(math.hypot(c[i][0] - c[i - 1][0], c[i][1] - c[i - 1][1]) for i in range(1, len(c)))
        disp = math.hypot(c[-1][0] - c[0][0], c[-1][1] - c[0][1])
        return path / disp if disp > 0 else float("inf")

    def _flow_heading(self) -> float | None:
        if self._flow_count < FLOW_MIN_TRACKS:
            return None
        # Circular mean -- averaging degrees directly is wrong across the +/-180 wrap.
        sx = sum(math.cos(math.radians(a)) for a in self._flow)
        sy = sum(math.sin(math.radians(a)) for a in self._flow)
        if math.hypot(sx, sy) < 1e-6:
            return None
        return math.degrees(math.atan2(sy, sx))

    def _fire(self, key, now: float) -> bool:
        last = self._cooldown.get(key, -1e9)
        if now - last < COOLDOWN_S:
            return False
        self._cooldown[key] = now
        return True

    # -------------------------------------------------------------------- update

    def update(self, tracks, timestamp: float | None = None,
               frame_size: tuple[int, int] | None = None) -> list[Incident]:
        """Returns incidents newly raised on this frame. Never raises the same one twice
        inside the cooldown window."""
        now = time.time() if timestamp is None else timestamp
        out: list[Incident] = []
        seen = set()

        for tr in tracks:
            tid = tr.track_id
            seen.add(tid)
            x1, y1, x2, y2 = (float(v) for v in tr.box)
            st = self._tracks.get(tid)
            if st is None:
                st = self._tracks[tid] = _State(first_seen=now)
            st.last_seen = now
            st.label = getattr(tr, "label", st.label) or st.label
            st.centres.append(((x1 + x2) / 2.0, (y1 + y2) / 2.0))
            self._extent = [max(self._extent[0], x2), max(self._extent[1], y2)]
            st.heights.append(max(y2 - y1, 1.0))
            st.times.append(now)
            sp = self._speed(st)
            if sp is not None:
                st.speeds.append((now, sp))

            # before the motion gate: someone standing still inside a fence has no speed to measure
            out.extend(self._intrusion(tid, st, (x1, y1, x2, y2), now, frame_size))

            if now - st.first_seen < MIN_TRACK_SECONDS or sp is None:
                continue
            if not self._trusted(st):
                continue   # sparse or swapped: its speed and heading are not the vehicle's
            st.in_view = self._in_view((x1, y1, x2, y2), frame_size)
            if not st.in_view and st.label in VEHICLE_LABELS:
                continue   # too small or cut off at the edge: its box moves when the vehicle does not

            if sp > MOVING_SPEED:
                st.was_moving = True
            if sp < STOP_SPEED:
                if st.stopped_since is None:
                    st.stopped_since = now
            else:
                st.stopped_since = None

            hd = self._heading(st)
            cell = self._cell(st, frame_size)
            out.extend(self._per_track(tid, st, sp, hd, tracks, now, cell))
            sense = self._sense(st)
            if sense and sp > MOVING_SPEED and cell not in st.voted_sense:
                st.voted_sense.add(cell)
                self._senses.setdefault(cell, deque(maxlen=300)).append(sense)
            if hd is not None and sp > MOVING_SPEED and cell not in st.voted:   # after judging: never votes for itself
                st.voted.add(cell)
                self._flow.append(hd)
                self._flow_count += 1
                self._cells.setdefault(cell, deque(maxlen=300)).append(hd)
                self._learned += 1
                if self._learned % 100 == 0:
                    self._save_flow()

        out.extend(self._pairs(tracks, now))
        out.extend(self._crowd(tracks, now, frame_size))

        # Forget tracks the tracker has dropped, so state does not grow without bound.
        for tid in [t for t in self._tracks if t not in seen
                    and now - self._tracks[t].last_seen > 30.0]:
            self._tracks.pop(tid, None)
        return out

    def _per_track(self, tid, st, sp, hd, tracks, now, cell=None) -> list[Incident]:
        # A person is not traffic. The motion rules below describe a vehicle,
        # and applying them to a pedestrian says things that are not true.
        if st.label == PERSON_LABEL:
            return self._person(tid, st, tracks, now)
        if st.label not in VEHICLE_LABELS:
            return []

        out = []
        decel = self._decel(st)

        # Corroboration: a vehicle that really stopped IS slow now. A deceleration
        # figure on its own is satisfied just as well by a tracking artefact -- a box
        # that shrinks abruptly, or an identity swap between two vehicles -- and those
        # were producing 1.0-1.5 on live cam01 and cam05 traffic, above the 0.909 that a
        # clean synthetic full-speed stop can even reach. Requiring the vehicle to be
        # near-stationary afterwards costs nothing on a real stop and rejects the
        # artefacts, which do not leave a stationary vehicle behind.
        stopped_now = sp < STOP_SPEED * 2.0
        # "far harder than traffic around it": braking in a queue is not an incident, so other
        # trusted vehicles in view must still be moving (cam01, 8 Oct: a car braking behind a bus)
        others_moving = sum(
            1 for t in tracks
            if t.track_id != tid and (o := self._tracks.get(t.track_id)) is not None
            and o.label in VEHICLE_LABELS and o.speeds and o.speeds[-1][1] > MOVING_SPEED
            and self._trusted(o))
        if (decel is not None and decel >= HARD_DECEL and stopped_now and others_moving >= 2
                and RAISE_SUDDEN_STOP and self._fire((tid, "SUDDEN_STOP"), now)):
            out.append(Incident(
                self.camera_id, "SUDDEN_STOP", "MEDIUM", [tid], st.first_seen, now,
                reason=(f"{st.label} lost {decel:.1f} vehicle-heights/s of speed per "
                        f"second (threshold {HARD_DECEL}) and is now near-stationary at "
                        f"{sp:.2f}"),
                evidence={"decel_vh_per_s2": round(decel, 2),
                          "speed_after_vh_per_s": round(sp, 3),
                          "vehicle": st.label}))

        # `was_moving` is what makes this an EVENT rather than an observation. A vehicle
        # that stopped is news; a vehicle that was parked before the camera ever saw it is
        # street furniture, and reporting it every time traffic passes is how a detector
        # gets ignored.
        if (st.was_moving and st.stopped_since is not None
                and now - st.stopped_since >= STOPPED_SECONDS):
            # Stationary only matters if the road is not. A camera over a car park or a
            # queue at a signal would otherwise alarm continuously.
            movers = sum(1 for t in tracks
                         if t.track_id != tid
                         and (s := self._tracks.get(t.track_id)) is not None
                         and s.speeds and s.speeds[-1][1] > MOVING_SPEED)
            if movers >= 1 and self._fire((tid, "STOPPED_IN_LANE"), now):
                out.append(Incident(
                    self.camera_id, "STOPPED_IN_LANE", "LOW", [tid], st.stopped_since,
                    now,
                    reason=(f"{st.label} was moving, then stationary "
                            f"{now - st.stopped_since:.0f}s while {movers} other "
                            f"vehicle(s) kept moving past it"),
                    evidence={"stationary_s": round(now - st.stopped_since, 1),
                              "other_vehicles_moving": movers, "vehicle": st.label}))

        # wrong side by keep-left: going the other sense in a clearly one-sense region
        senses = self._senses.get(cell) if cell is not None else None
        sense = self._sense(st)
        if senses is not None and len(senses) >= WRONG_SIDE_MIN_REGION and sense and sp > MOVING_SPEED:
            same = sum(1 for v in senses if v == sense) / len(senses)
            dominant = max(same, 1 - same)
            straight = self._straightness(st)
            if (same < WRONG_SIDE_SHARE and dominant >= WRONG_SIDE_DOMINANT and straight <= WRONG_WAY_STRAIGHTNESS
                    and self._fire((tid, "WRONG_WAY"), now)):
                layout = self._layout()
                out.append(Incident(
                    self.camera_id, "WRONG_WAY", "MEDIUM", [tid], st.first_seen, now,
                    reason=(f"{st.label} {'coming towards the camera' if sense > 0 else 'moving away'} where "
                            f"{dominant:.0%} of {len(senses)} vehicles go the other way: wrong-side driving "
                            "(keep left, Rules of the Road Reg. 2; one-way Reg. 17; MV Act s. 184)"),
                    evidence={"sense": "approaching" if sense > 0 else "receding", "region": list(cell),
                              "share_same_sense_here": round(same, 3), "region_vehicles": len(senses),
                              "camera_layout": layout, "straightness": round(straight, 2),
                              "vehicle": st.label}))
        here = self._cells.get(cell) if cell is not None else None
        if here is not None and len(here) >= WRONG_WAY_MIN_REGION and hd is not None and sp > MOVING_SPEED:
            same = sum(abs((hd - h + 180.0) % 360.0 - 180.0) <= 45.0 for h in here) / len(here)
            bins = [0] * 12
            for h in here:
                bins[int((h + 180.0) // 30.0) % 12] += 1
            dominant = max(bins[b] + bins[(b - 1) % 12] + bins[(b + 1) % 12] for b in range(12)) / len(here)
            straight = self._straightness(st)
            if (same < WRONG_WAY_SHARE and dominant >= WRONG_WAY_DOMINANT and straight <= WRONG_WAY_STRAIGHTNESS
                    and self._fire((tid, "WRONG_WAY"), now)):
                out.append(Incident(
                    self.camera_id, "WRONG_WAY", "MEDIUM", [tid], st.first_seen, now,
                    reason=(f"{st.label} moving a way only {same:.0%} of {len(here)} vehicles in this "
                            f"part of the frame went"),
                    evidence={"heading_deg": round(hd, 1),
                              "region": list(cell),
                              "share_same_direction_here": round(same, 3),
                              "region_tracks": len(here),
                              "straightness": round(straight, 2),
                              "vehicle": st.label}))
        return out

    def _intrusion(self, tid, st, box, now, frame_size) -> list[Incident]:
        """Anything tracked - or the classes the profile lists - inside the camera's restricted zone.

        Judged on the foot point - bottom centre of the box - because that is where the object
        stands; a tall box beside a fence otherwise "enters" it with its head. Held for
        `dwell_seconds` so a track that clips a corner of the zone is not an alarm.
        """
        cfg = self.intrusion
        # No classes listed: anything the camera's tracker follows. The live ANPR tracker follows
        # vehicles only, so a zone with `classes: [person]` needs a tracker that follows people.
        if cfg is None or (cfg.get("classes") and st.label not in cfg["classes"]):
            return []
        hours = cfg.get("hours")
        if hours:
            h, (start, end) = time.localtime().tm_hour, hours
            armed = start <= h < end if start <= end else (h >= start or h < end)
            if not armed:
                return []
        w, h_ = frame_size or (1.0, 1.0)
        x, y = (box[0] + box[2]) / 2.0 / w, box[3] / h_
        if not _in_polygon(x, y, cfg["zone"]):
            st.zone_since = None
            return []
        if st.zone_since is None:
            st.zone_since = now
        dwell = float(cfg.get("dwell_seconds", 2.0))
        if now - st.zone_since < dwell or not self._fire((tid, "INTRUSION"), now):
            return []
        return [Incident(
            self.camera_id, "INTRUSION", str(cfg.get("severity", "HIGH")), [tid],
            st.zone_since, now,
            reason=f"a {st.label} has been inside this camera's restricted zone for "
                   f"{now - st.zone_since:.0f}s",
            evidence={"dwell_s": round(now - st.zone_since, 1), "foot_point": [round(x, 3), round(y, 3)],
                      "armed_hours": hours or "always"})]

    def _crowd(self, tracks, now, frame_size) -> list[Incident]:
        people = [t for t in tracks if getattr(t, "label", "") == PERSON_LABEL]
        w, h = frame_size if frame_size else self._extent
        best: list = []
        for p in people:
            px, py = (p.box[0] + p.box[2]) / 2.0, (p.box[1] + p.box[3]) / 2.0
            r = CROWD_RADIUS_H * max(p.box[3] - p.box[1], 1.0)
            group = [q for q in people
                     if math.hypot((q.box[0] + q.box[2]) / 2.0 - px, (q.box[1] + q.box[3]) / 2.0 - py) <= r]
            if len(group) > len(best):
                best = group
        if not best:
            return []
        cx = sum((q.box[0] + q.box[2]) / 2.0 for q in best) / len(best)
        cy = sum((q.box[1] + q.box[3]) / 2.0 for q in best) / len(best)
        cell = (min(FLOW_GRID[0] - 1, int(cx / max(w, 1.0) * FLOW_GRID[0])),
                min(FLOW_GRID[1] - 1, int(cy / max(h, 1.0) * FLOW_GRID[1])))
        hist = self._crowd_hist.setdefault(cell, deque())
        while hist and now - hist[0][0] > CROWD_HISTORY_S:
            hist.popleft()
        past = sorted(n for t, n in hist if now - t > CROWD_SECONDS * 2)
        usual = past[int(0.9 * (len(past) - 1))] if past else None
        hist.append((now, len(best)))
        if usual is None or len(past) < 20:
            return []   # this region's usual is not known yet: say nothing rather than guess
        if len(best) < max(CROWD_MIN, CROWD_FACTOR * usual):
            self._crowd_since.pop(cell, None)
            return []
        since = self._crowd_since.setdefault(cell, now)
        if now - since < CROWD_SECONDS or not self._fire((cell, "CROWD"), now):
            return []
        self._cooldown[(cell, "CROWD")] = now + CROWD_COOLDOWN_S - COOLDOWN_S
        return [Incident(
            self.camera_id, "CROWD_GATHERING", "MEDIUM", [q.track_id for q in best], since, now,
            reason=(f"{len(best)} people gathered close together for {now - since:.0f}s where "
                    f"{usual} is usual"),
            evidence={"people": len(best), "usual_here": usual, "region": list(cell),
                      "held_s": round(now - since, 1)})]

    def _person(self, tid, st, tracks, now) -> list[Incident]:
        """A person in the carriageway while traffic is moving.

        Not an accident, and deliberately not named as one - it is the
        condition that precedes one, and the thing an operator can still act
        on. The test is overlap in the image plane with a vehicle that is
        genuinely moving, held for a couple of seconds: a pedestrian crossing
        at a signal passes through that in well under a second, and one
        standing on a footpath beside traffic never overlaps at all.
        """
        exposed_to = None
        for other in tracks:
            if other.track_id == tid:
                continue
            os_ = self._tracks.get(other.track_id)
            if os_ is None or os_.label not in VEHICLE_LABELS:
                continue
            if not (os_.speeds and os_.speeds[-1][1] > MOVING_SPEED):
                continue
            # Boxes rather than centre points: a person is narrow, and a
            # centre-only test misses a vehicle passing right alongside.
            if _iou(self._box_of(st), self._box_of(os_)) >= PERSON_VEHICLE_IOU:
                exposed_to = other.track_id
                break

        if exposed_to is not None:
            st.last_exposed = now
            if st.exposed_since is None:
                st.exposed_since = now
        elif st.exposed_since is not None and now - st.last_exposed > PERSON_CLEAR_SECONDS:
            # Nothing has passed close for a while: they are out of the road.
            st.exposed_since = None

        if st.exposed_since is None or now - st.exposed_since < PERSON_EXPOSED_SECONDS:
            return []
        exposed_to = exposed_to if exposed_to is not None else "recent"
        if not self._fire((tid, "PERSON_ON_CARRIAGEWAY"), now):
            return []
        involved = [tid] + ([exposed_to] if isinstance(exposed_to, int) else [])
        return [Incident(
            self.camera_id, "PERSON_ON_CARRIAGEWAY", "HIGH", involved,
            st.exposed_since, now,
            reason=(f"a person has been in the path of moving traffic for "
                    f"{now - st.exposed_since:.0f}s, with vehicles passing "
                    f"through their position"),
            evidence={"exposed_s": round(now - st.exposed_since, 1),
                      "last_pass_s_ago": round(now - st.last_exposed, 1),
                      "overlap_threshold_iou": PERSON_VEHICLE_IOU})]

    @staticmethod
    def _box_of(st: _State) -> tuple[float, float, float, float]:
        """Reconstruct the last box from the retained centre and height.

        Width is not kept per frame, so it is taken as the height - close
        enough for an overlap test whose threshold is deliberately loose, and
        it avoids carrying a fourth deque for one rule.
        """
        cx, cy = st.centres[-1]
        h = max(st.heights[-1], 1.0)
        return (cx - h / 2.0, cy - h / 2.0, cx + h / 2.0, cy + h / 2.0)

    def _pairs(self, tracks, now) -> list[Incident]:
        """Two vehicles overlapping while both lose speed. Overlap alone is not enough --
        on an overhead view vehicles in adjacent lanes overlap constantly."""
        out = []
        cand = [t for t in tracks
                if (s := self._tracks.get(t.track_id)) is not None
                and len(s.speeds) >= 4
                and self._trusted(s)   # both tracks dense and continuous: a swap is not a crash
                and getattr(s, "in_view", False)   # big enough and not cut off at an edge
                # Vehicles only: a person overlapping a car is the pedestrian
                # rule's business, not a two-vehicle collision.
                and s.label in VEHICLE_LABELS]
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                a, b = cand[i], cand[j]
                iou = _iou(a.box, b.box)
                if iou < COLLISION_IOU:
                    continue
                sa, sb = self._tracks[a.track_id], self._tracks[b.track_id]
                da, db = self._decel(sa), self._decel(sb)
                if da is None or db is None:
                    continue
                if da < COLLISION_DECEL or db < COLLISION_DECEL:
                    continue
                # Both must actually be slow now, for the same reason SUDDEN_STOP
                # requires it: overlapping boxes plus a deceleration number is also what
                # an identity swap between two passing vehicles looks like.
                if not (sa.speeds and sb.speeds
                        and sa.speeds[-1][1] < MOVING_SPEED
                        and sb.speeds[-1][1] < MOVING_SPEED):
                    continue
                # a queue at a signal is two overlapping vehicles slowing together too: a
                # collision leaves traffic around it moving (cam01-cam30, 8 Oct: 32 "collisions"
                # in 6 h with the same overlap-and-slow signature)
                flowing = sum(
                    1 for t in tracks
                    if t.track_id not in (a.track_id, b.track_id)
                    and (o := self._tracks.get(t.track_id)) is not None
                    and o.label in VEHICLE_LABELS and o.speeds and o.speeds[-1][1] > MOVING_SPEED
                    and self._trusted(o))
                if flowing < 2:
                    continue
                key = (min(a.track_id, b.track_id), max(a.track_id, b.track_id),
                       "COLLISION")
                if not self._fire(key, now):
                    continue
                out.append(Incident(
                    self.camera_id, "COLLISION_CANDIDATE", "HIGH",
                    [a.track_id, b.track_id], min(sa.first_seen, sb.first_seen), now,
                    reason=(f"{sa.label} and {sb.label} boxes overlap {iou:.0%} while "
                            f"both decelerate hard ({da:.1f} and {db:.1f} "
                            f"vehicle-heights/s^2). Overlap in the image plane is not "
                            f"contact -- this needs a human to look at the clip"),
                    evidence={"iou": round(iou, 3),
                              "decel_a_vh_per_s2": round(da, 2),
                              "decel_b_vh_per_s2": round(db, 2),
                              "vehicles": [sa.label, sb.label]}))
        return out
