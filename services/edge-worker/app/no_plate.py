"""Vehicle without a visible number plate: a candidate for a human to look at.

The ANPR engine already knows when a vehicle's track closed without a single plate box in any frame
(`no_plate_detected`). On the grid that is usually innocent: the vehicle was far away, side-on, or its
front was lost in headlight glare. It is worth an operator's look only when the plate SHOULD have been
seen, so a candidate needs all of:

- any vehicle (car, bus, truck, two-wheeler; autos are detected as one of these), big enough in the
  frame that its plate would be readable: at its widest at least MIN_WIDTH_FRAC[class] of the frame
  width (a two-wheeler's plate is about a third of its width, a car's about a quarter), seen in at
  least MIN_FRAMES frames at a useful size;
- moving towards or away from the camera, so its front or rear (where plates are) faces the lens,
  not crossing the frame side-on: over the whole track, AND in the view that counts (its widest),
  where the box must be no wider than a front or rear view of that vehicle is (MAX_ASPECT) and the
  vehicle must be moving more up/down the frame than across it. The widest view of a vehicle that
  turns is its side (cam04, 8 Oct: the first live call was a car turning at the junction);
- a usable picture: the widest view fully inside the frame (cam30, 8 Oct: a car cut off by the bottom
  edge, where its rear plate was), neither dark nor blown out by glare, and sharp.

It is a CANDIDATE, never a finding: covered, missing and unreadable plates all look the same here, and
the snapshot (the frame at the vehicle's widest, boxed) is what lets a person decide.
"""
from __future__ import annotations

import base64
import logging
import re
from collections import Counter

import numpy as np

logger = logging.getLogger("vigentra.edge.no_plate")

#: per class, the width (fraction of the frame) at which its plate is >= ~40 px on a 1280 px frame
MIN_WIDTH_FRAC = {"car": 0.18, "bus": 0.22, "truck": 0.22, "auto-rickshaw": 0.15, "motorcycle": 0.11, "scooter": 0.11}
CLASSES = frozenset(MIN_WIDTH_FRAC)
USEFUL_SHARE = 0.7          # frames at least this share of that width count toward MIN_FRAMES
MIN_FRAMES = 4              # light mode samples a busy camera a few times a second
TOWARDS_RATIO = 1.5         # vertical travel at least this many times the horizontal
#: width / height of the box at its widest: a front or rear view is no wider than this
MAX_ASPECT = {"car": 1.45, "bus": 1.5, "truck": 1.5, "auto-rickshaw": 1.3, "motorcycle": 0.9, "scooter": 0.9}
MIN_LUMA, MAX_LUMA = 55, 205   # median brightness of the vehicle at its widest
MIN_SHARPNESS = 40.0        # Laplacian variance of that view
EDGE_MARGIN = 0.015         # the widest view must be at least this share of the frame clear of every edge
FORGET_SECONDS = 120.0

#: why closed no-plate tracks did not become candidates, for the reader's per-minute log
REJECTED: Counter = Counter()


def candidate(view: dict, frame_w: float) -> tuple[bool, str]:
    """Should a closed track with no plate box become a candidate? (decision, why not)"""
    if view.get("label") not in CLASSES:
        return False, "class"
    if view["max_w"] < MIN_WIDTH_FRAC[view["label"]] * frame_w:
        return False, "too_small"
    if view["useful_frames"] < MIN_FRAMES:
        return False, "too_few_frames"
    dx = abs(view["last_c"][0] - view["first_c"][0])
    dy = abs(view["last_c"][1] - view["first_c"][1])
    if dy < TOWARDS_RATIO * max(dx, 1.0):
        return False, "side_on"
    if view.get("aspect", 9.9) > MAX_ASPECT[view["label"]]:
        return False, "side_on_at_widest"
    ldx, ldy = view.get("local", (1.0, 0.0))
    if abs(ldy) < abs(ldx):
        return False, "turning_at_widest"
    if view.get("at_edge", False):
        return False, "cut_by_frame_edge"
    if not (MIN_LUMA <= view.get("luma", 0) <= MAX_LUMA):
        return False, "exposure"
    if view.get("sharpness", 0.0) < MIN_SHARPNESS:
        return False, "blur"
    return True, ""


class NoPlateWatch:
    """Per camera: remembers each vehicle's widest view while it is tracked."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self.views: dict[int, dict] = {}

    def observe(self, frame, detections, pts: float) -> None:
        import cv2

        h, w = frame.shape[:2]
        for d in detections:
            tid = (getattr(d, "extra", None) or {}).get("track_id")
            if tid is None or d.class_name not in CLASSES:
                continue
            x1, y1, x2, y2 = d.bbox_xyxy
            bw = x2 - x1
            c = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            v = self.views.get(tid)
            if v is None:
                v = self.views[tid] = {"label": d.class_name, "max_w": 0.0, "useful_frames": 0,
                                       "first_c": c, "first_pts": pts, "jpeg": None}
            prev = v.get("last_c", c)
            v["last_c"], v["last_pts"], v["label"] = c, pts, d.class_name   # its type may be refined as it nears
            need = MIN_WIDTH_FRAC[v["label"]] * w
            if bw >= USEFUL_SHARE * need:
                v["useful_frames"] += 1
            if bw > v["max_w"] and bw >= need:
                v["max_w"] = bw
                v["aspect"] = bw / max(1.0, y2 - y1)
                mx, my = EDGE_MARGIN * w, EDGE_MARGIN * h
                v["at_edge"] = x1 < mx or y1 < my or x2 > w - mx or y2 > h - my
                v["local"] = (c[0] - prev[0], c[1] - prev[1])
                crop = frame[int(max(0, y1)):int(y2), int(max(0, x1)):int(x2)]
                if crop.size:
                    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    v["luma"] = float(np.median(g))
                    v["sharpness"] = float(cv2.Laplacian(g, cv2.CV_64F).var())
                    snap = frame.copy()
                    t = max(2, w // 400)
                    cv2.rectangle(snap, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), t + 1)
                    strip = np.zeros((18 * t + 8, w, 3), np.uint8)   # title above the frame, never over it
                    cv2.putText(strip, f"NO PLATE VISIBLE  {v['label'].upper()}  {self.camera_id}", (8, 14 * t),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45 * t, (255, 255, 255), max(1, t // 2))
                    snap = np.vstack([strip, snap])
                    if w > 960:
                        snap = cv2.resize(snap, (960, int(snap.shape[0] * 960 / w)), interpolation=cv2.INTER_AREA)
                    ok, buf = cv2.imencode(".jpg", snap, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    v["jpeg"] = buf.tobytes() if ok else None
            elif bw > v["max_w"]:
                v["max_w"] = bw
        for tid in [t for t, v in self.views.items() if pts - v.get("last_pts", pts) > FORGET_SECONDS]:
            self.views.pop(tid, None)

    def closed(self, records: list[dict], frame_w: float) -> list[dict]:
        """Incident payloads for tracks the engine closed without a plate box, where one should show."""
        out = []
        for rec in records:
            # the engine keys a track "<camera>_s<segment>_t<n>"; the detections carry n
            tail = re.search(r"\d+$", str(rec.get("track_id", "")))
            if tail is None:
                continue
            tid = int(tail.group())
            v = self.views.pop(tid, None)
            if v is None:
                continue
            ok, why = candidate(v, frame_w)
            REJECTED[why or "raised"] += 1
            if not ok:
                continue
            payload = {
                "camera_id": self.camera_id, "kind": "NO_PLATE_VISIBLE", "severity": "LOW",
                "track_ids": [tid], "first_seen": float(v["first_pts"]), "last_seen": float(v["last_pts"]),
                "reason": (f"{v['label']} seen close ({int(v['max_w'])} px wide, {v['useful_frames']} frames), "
                           "front or rear towards the camera, and no plate was found on it in any frame"),
                "evidence": {"vehicle": v["label"], "widest_px": round(v["max_w"]), "frames": v["useful_frames"],
                             "luma": round(v.get("luma", 0)), "sharpness": round(v.get("sharpness", 0.0), 1)},
            }
            if v.get("jpeg"):
                payload["snapshot_jpeg_b64"] = base64.b64encode(v["jpeg"]).decode("ascii")
            out.append(payload)
        return out
