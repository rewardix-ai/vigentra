"""Count vehicles, not tracker ids.

A tracker hands one vehicle several ids, and counting ids counts each of them as another vehicle:

- an id switch: the vehicle is lost behind another, or for a few missed frames, and picked up again
  under a new id a moment later, a little further along its path;
- a duplicate: the same vehicle boxed twice in the same frames (a car also as a truck, a lorry as cab
  and as whole), each box tracked on its own;
- a parked vehicle, dropped when traffic passes in front of it and re-acquired where it stands.

`link_fragments` groups the ids that are one physical vehicle from their boxes alone, plus any groups
the plate reader already knows are one vehicle, and numbers the groups in order of first appearance.
It errs towards keeping vehicles apart: a moving link needs the new box where the old one was heading,
of a similar size and the same kind of vehicle, and each fragment continues at most one other; a
parked link needs two ids that each stood still all their lives on the same spot. A track whose best
detection confidence stays low - a box on a lane marking or a shadow - is not a vehicle at all.
"""
from __future__ import annotations

import math
from statistics import median

TWO_WHEEL = {"motorcycle", "bicycle", "motorbike", "scooter"}
#: A track that never moved and never reached this detection confidence is a box on nothing. Every parked
#: vehicle on the CAM06 noon clip scored 0.70 or more; the lane-marking boxes 0.32-0.62.
STILL_MIN_SCORE = 0.65
#: Two fragments must look at least this alike to be linked by their boxes (see link_fragments).
MIN_SIMILARITY = 0.0


def _family(name: str) -> str:
    return "two-wheeler" if name in TWO_WHEEL else "four-wheeler"


def _area(b) -> float:
    return max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)


def _centre(b) -> tuple[float, float]:
    return (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0


def _inter(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a, b) -> float:
    i = _inter(a, b)
    return i / max(_area(a) + _area(b) - i, 1e-9)


def similarity(h1, h2) -> float:
    """Bhattacharyya coefficient of two normalised histograms: 1 identical, 0 nothing in common."""
    import numpy as np
    return float(np.sum(np.sqrt(np.asarray(h1) * np.asarray(h2))))


def appearance_from_video(path, obs: dict) -> dict:
    """{track: {"first" | "mid" | "last": hue-saturation histogram}} of each track's box at its first,
    middle and last sighting, from one decode of the clip. Brightness is left out so a vehicle moving
    from sun into shade still looks like itself; the box is trimmed by a tenth so less road is counted."""
    import cv2
    import numpy as np
    want: dict = {}
    for t, o in obs.items():
        for name, (f, b) in (("first", o[0]), ("mid", o[len(o) // 2]), ("last", o[-1])):
            want.setdefault(f, []).append((t, name, b))
    out: dict = {}
    cap, i = cv2.VideoCapture(str(path)), 0
    while want:
        ok, frame = cap.read()
        if not ok:
            break
        for t, name, b in want.pop(i, []):
            x1, y1, x2, y2 = b
            dx, dy = (x2 - x1) * 0.1, (y2 - y1) * 0.1
            crop = frame[int(max(0, y1 + dy)):int(max(0, y2 - dy)), int(max(0, x1 + dx)):int(max(0, x2 - dx))]
            if crop.size == 0:
                continue
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            h = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256]).ravel()
            out.setdefault(t, {})[name] = h / max(float(h.sum()), 1e-9)
        i += 1
    cap.release()
    return out


def link_fragments(obs: dict, classes: dict, *, fps: float = 25.0, same_vehicle=(), scores=None,
                   min_score: float = 0.0, max_gap_s: float = 2.0, parked_gap_s: float = 120.0,
                   min_obs: int = 3, appearance=None, min_similarity: float = 0.0, explain=None):
    """Group tracker ids into vehicles.

    obs:          {track: [(frame, (x1, y1, x2, y2)), ...]}, each list in frame order
    classes:      {track: class name}, the track's majority class
    same_vehicle: groups of tracks already known to be one vehicle (e.g. the plate reader merged them)
    appearance:   {track: {"first"|"mid"|"last": histogram}} (appearance_from_video); when given, every
                  box-based link also needs the two fragments to look alike (similarity >= min_similarity):
                  in a queue, where the next vehicle stands where the last one stood, position is not enough
    explain:      a list to append (rule, a, b, similarity) to for every link made, for checking by eye
    scores:       {track: its highest detection confidence}; a track that stood still all its life and
                  never reached `min_score` is a box on nothing - a lane marking, a shadow, the time
                  overlay - and is neither linked nor counted. A moving box is kept whatever its
                  score: a distant motorcycle scores as low as a lane marking, and it moves.

    Returns ({track: vehicle number or None}, number of vehicles). Vehicles are numbered from 1 in
    order of first appearance; a group seen in fewer than `min_obs` frames in all is tracker noise
    and gets None.
    """
    scores = scores or {}

    def still(t):
        """Stood still all its life: four centres in five within a quarter of its size of the median
        centre. Not every one - traffic passing in front of a parked vehicle makes its box jitter,
        and a parked motorcycle on CAM06 was counted three times until this allowed for it."""
        o = obs[t]
        mid = tuple(median(b[k] for _, b in o) for k in range(4))
        size = math.sqrt(max(_area(mid), 1.0))
        cx, cy = _centre(mid)
        near = sum(1 for _, b in o if math.hypot(_centre(b)[0] - cx, _centre(b)[1] - cy) < 0.25 * size)
        return len(o) >= 3 and near >= 0.8 * len(o), mid

    standing = {t: m for t, (ok, m) in ((t, still(t)) for t in obs if obs[t]) if ok}
    tracks = [t for t in obs if obs[t] and not (t in standing and scores.get(t, 1.0) < min_score)]
    parent = {t: t for t in tracks}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for group in same_vehicle:
        members = [t for t in group if t in parent]
        for t in members[1:]:
            union(members[0], t)

    def looks_alike(a, which_a, b, which_b):
        if not appearance:
            return True, None
        ha, hb = appearance.get(a, {}).get(which_a), appearance.get(b, {}).get(which_b)
        if ha is None or hb is None:
            return False, None
        sim = similarity(ha, hb)
        return sim >= min_similarity, round(sim, 3)

    def link(rule, a, b, sim):
        if explain is not None and find(a) != find(b):
            explain.append((rule, a, b, sim))
        union(a, b)

    span = {t: (obs[t][0][0], obs[t][-1][0]) for t in tracks}
    at = {t: dict(obs[t]) for t in tracks}

    # 1. duplicates: two ids boxing the same thing in the same frames
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            if span[a][1] < span[b][0] or span[b][1] < span[a][0]:
                continue
            common = at[a].keys() & at[b].keys()
            if len(common) < 3:
                continue
            ious = [iou(at[a][f], at[b][f]) for f in common]
            # one box inside the other, of comparable size: a cab inside its lorry, not a bike passing a bus
            cover = [_inter(at[a][f], at[b][f]) / max(min(_area(at[a][f]), _area(at[b][f])), 1e-9) for f in common]
            ratio = [min(_area(at[a][f]), _area(at[b][f])) / max(_area(at[a][f]), _area(at[b][f]), 1e-9) for f in common]
            if median(ious) >= 0.6 or (median(cover) >= 0.85 and median(ratio) >= 0.5):
                ok, sim = looks_alike(a, "mid", b, "mid")
                if ok:
                    link("duplicate", a, b, sim)

    # 2. parked: every id that stood still all its life, at the same spot, is one vehicle however often
    #    passing traffic made the tracker drop it
    still_ids = [t for t in tracks if t in standing]
    for i, a in enumerate(still_ids):
        for b in still_ids[i + 1:]:
            gap = max(span[a][0], span[b][0]) - min(span[a][1], span[b][1])
            if (gap <= parked_gap_s * fps and _family(classes.get(a, "")) == _family(classes.get(b, ""))
                    and iou(standing[a], standing[b]) >= 0.5):
                ok, sim = looks_alike(a, "mid", b, "mid")
                if ok:
                    link("parked", a, b, sim)

    # 3. continuations: an id that starts where an id that just ended was heading
    def motion(t):
        """Velocity (px per frame) over the last second of the track, and whether it stood still."""
        o = obs[t]
        f1, b1 = o[-1]
        k = len(o) - 1
        while k > 0 and f1 - o[k - 1][0] <= fps:
            k -= 1
        f0, b0 = o[k]
        size = math.sqrt(max(_area(b1), 1.0))
        if f1 == f0:
            return (0.0, 0.0), True
        (x0, y0), (x1, y1) = _centre(b0), _centre(b1)
        v = ((x1 - x0) / (f1 - f0), (y1 - y0) / (f1 - f0))
        return v, math.hypot(x1 - x0, y1 - y0) < 0.15 * size

    def still_at_start(t):
        o = obs[t]
        f0, b0 = o[0]
        k = 0
        while k + 1 < len(o) and o[k + 1][0] - f0 <= fps:
            k += 1
        size = math.sqrt(max(_area(b0), 1.0))
        (x0, y0), (x1, y1) = _centre(b0), _centre(o[k][1])
        return math.hypot(x1 - x0, y1 - y0) < 0.15 * size

    pairs = []
    for a in tracks:
        (vx, vy), parked = motion(a)
        fa, ba = obs[a][-1]
        for b in tracks:
            fb, bb = obs[b][0]
            gap = fb - fa
            if b == a or gap <= 0 or _family(classes.get(a, "")) != _family(classes.get(b, "")):
                continue
            ratio = _area(bb) / max(_area(ba), 1e-9)
            if parked and still_at_start(b) and len(obs[b]) >= 3 and gap <= parked_gap_s * fps:
                overlap = iou(ba, bb)               # stood, then picked up again where it stood
                if overlap >= 0.5 and looks_alike(a, "last", b, "first")[0]:
                    pairs.append((1.0 - overlap + gap / (parked_gap_s * fps), a, b))
            elif gap <= max_gap_s * fps and 0.4 <= ratio <= 2.5:
                (cx, cy), (bx, by) = _centre(ba), _centre(bb)
                d = math.hypot(cx + vx * gap - bx, cy + vy * gap - by) / math.sqrt(max(_area(ba), _area(bb)))
                if d <= 0.6 and looks_alike(a, "last", b, "first")[0]:
                    pairs.append((d + 0.5 * gap / (max_gap_s * fps), a, b))   # the nearest in time first
    has_next, has_prev = set(), set()
    for _, a, b in sorted(pairs):
        if a in has_next or b in has_prev or find(a) == find(b):
            continue
        has_next.add(a)
        has_prev.add(b)
        link("continuation", a, b, looks_alike(a, "last", b, "first")[1])

    groups: dict = {}
    for t in tracks:
        groups.setdefault(find(t), []).append(t)
    kept = sorted((min(span[t][0] for t in g), g) for g in groups.values()
                  if sum(len(obs[t]) for t in g) >= min_obs)
    number = {t: None for t in obs}
    for n, (_, g) in enumerate(kept, 1):
        for t in g:
            number[t] = n
    return number, len(kept)


def reader_groups(records, first_frame: dict, fps: float, window_s: float = 15.0) -> list[list]:
    """Groups of track ids the plate reader says are one vehicle: the fragments it merged, and
    tracks that confirmed the same plate within `window_s` of each other.

    records:     the pipeline's records ({"track_id", "status", "plate", "merged_from", ...})
    first_frame: {track key: first video frame it was seen}, to measure the window in video time
    """
    key = lambda t: str(t).split("_")[-1]
    groups = [[key(m) for m in (r.get("merged_from") or [])] for r in records if r.get("merged_from")]
    seen: dict = {}
    for r in records:
        if r.get("status") == "CONFIRMED" and r.get("plate"):
            k = key(r.get("track_id"))
            seen.setdefault(r["plate"], []).append((first_frame.get(k, 0), k))
    for sightings in seen.values():
        sightings.sort()
        run = [sightings[0]]
        for f, t in sightings[1:]:
            if f - run[-1][0] <= window_s * fps:
                run.append((f, t))
            else:
                groups.append([x for _, x in run])
                run = [(f, t)]
        groups.append([x for _, x in run])
    return groups
