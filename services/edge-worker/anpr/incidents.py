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

    SUDDEN_STOP        a vehicle decelerating far harder than traffic around it
    STOPPED_IN_LANE    a vehicle stationary while other vehicles keep moving past it
    COLLISION_CANDIDATE two vehicles overlapping AND both losing speed sharply together
    WRONG_WAY          a vehicle travelling against the camera's established flow

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

import math
import time
from collections import defaultdict, deque
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
    first_seen: float = 0.0
    last_seen: float = 0.0
    label: str = "?"


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

    def __init__(self, camera_id: str):
        self.camera_id = camera_id
        self._tracks: dict[int, _State] = {}
        self._flow: deque = deque(maxlen=200)      # headings of completed tracks
        self._flow_count = 0
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

    def update(self, tracks, timestamp: float | None = None) -> list[Incident]:
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
            st.heights.append(max(y2 - y1, 1.0))
            st.times.append(now)
            sp = self._speed(st)
            if sp is not None:
                st.speeds.append((now, sp))

            if now - st.first_seen < MIN_TRACK_SECONDS or sp is None:
                continue

            if sp > MOVING_SPEED:
                st.was_moving = True
            if sp < STOP_SPEED:
                if st.stopped_since is None:
                    st.stopped_since = now
            else:
                st.stopped_since = None

            hd = self._heading(st)
            if hd is not None and sp > MOVING_SPEED:
                self._flow.append(hd)
                self._flow_count += 1

            out.extend(self._per_track(tid, st, sp, hd, tracks, now))

        out.extend(self._pairs(tracks, now))

        # Forget tracks the tracker has dropped, so state does not grow without bound.
        for tid in [t for t in self._tracks if t not in seen
                    and now - self._tracks[t].last_seen > 30.0]:
            self._tracks.pop(tid, None)
        return out

    def _per_track(self, tid, st, sp, hd, tracks, now) -> list[Incident]:
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
        if (decel is not None and decel >= HARD_DECEL and stopped_now
                and self._fire((tid, "SUDDEN_STOP"), now)):
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

        flow = self._flow_heading()
        if flow is not None and hd is not None and sp > MOVING_SPEED:
            diff = abs((hd - flow + 180.0) % 360.0 - 180.0)
            if diff >= WRONG_WAY_DEG and self._fire((tid, "WRONG_WAY"), now):
                out.append(Incident(
                    self.camera_id, "WRONG_WAY", "MEDIUM", [tid], st.first_seen, now,
                    reason=(f"{st.label} heading {diff:.0f}deg from this camera's "
                            f"established flow, learned from {self._flow_count} tracks"),
                    evidence={"heading_deg": round(hd, 1),
                              "flow_deg": round(flow, 1),
                              "difference_deg": round(diff, 1),
                              "flow_learned_from_tracks": self._flow_count,
                              "vehicle": st.label}))
        return out

    def _pairs(self, tracks, now) -> list[Incident]:
        """Two vehicles overlapping while both lose speed. Overlap alone is not enough --
        on an overhead view vehicles in adjacent lanes overlap constantly."""
        out = []
        cand = [t for t in tracks
                if (s := self._tracks.get(t.track_id)) is not None
                and len(s.speeds) >= 4]
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
