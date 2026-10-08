"""Vehicle without a visible number plate: a candidate for a human to look at.

The ANPR engine already knows when a vehicle's track closed without a single plate box in any frame
(`no_plate_detected`). On the grid that is usually innocent: the vehicle was far away, side-on, or its
front was lost in headlight glare. It is worth an operator's look only when the plate SHOULD have been
seen, so a candidate needs all of:

- a car, bus or truck (two-wheeler plates are small and often hidden by the rider's legs);
- big in the frame: at its widest at least MIN_WIDTH_FRAC of the frame width, seen in at least
  MIN_FRAMES frames at a useful size;
- moving towards or away from the camera, so its front or rear (where plates are) faces the lens,
  not crossing the frame side-on;
- a usable picture: the widest view neither dark nor blown out by glare, and sharp.

It is a CANDIDATE, never a finding: covered, missing and unreadable plates all look the same here, and
the snapshot (the frame at the vehicle's widest, boxed) is what lets a person decide.
"""
from __future__ import annotations

import base64
import logging

import numpy as np

logger = logging.getLogger("vigentra.edge.no_plate")

CLASSES = frozenset({"car", "bus", "truck"})
MIN_WIDTH_FRAC = 0.22       # ~280 px on a 1280 px frame: a plate there is >= ~35 px, readable size
USEFUL_WIDTH_FRAC = 0.15    # frames at least this wide count toward MIN_FRAMES
MIN_FRAMES = 6
TOWARDS_RATIO = 1.5         # vertical travel at least this many times the horizontal
MIN_LUMA, MAX_LUMA = 55, 205   # median brightness of the vehicle at its widest
MIN_SHARPNESS = 40.0        # Laplacian variance of that view
FORGET_SECONDS = 120.0


def candidate(view: dict, frame_w: float) -> tuple[bool, str]:
    """Should a closed track with no plate box become a candidate? (decision, why not)"""
    if view.get("label") not in CLASSES:
        return False, "class"
    if view["max_w"] < MIN_WIDTH_FRAC * frame_w:
        return False, "too_small"
    if view["useful_frames"] < MIN_FRAMES:
        return False, "too_few_frames"
    dx = abs(view["last_c"][0] - view["first_c"][0])
    dy = abs(view["last_c"][1] - view["first_c"][1])
    if dy < TOWARDS_RATIO * max(dx, 1.0):
        return False, "side_on"
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
            if tid is None:
                continue
            x1, y1, x2, y2 = d.bbox_xyxy
            bw = x2 - x1
            c = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            v = self.views.get(tid)
            if v is None:
                v = self.views[tid] = {"label": d.class_name, "max_w": 0.0, "useful_frames": 0,
                                       "first_c": c, "first_pts": pts, "jpeg": None}
            v["last_c"], v["last_pts"] = c, pts
            if bw >= USEFUL_WIDTH_FRAC * w:
                v["useful_frames"] += 1
            if bw > v["max_w"] and bw >= MIN_WIDTH_FRAC * w:
                v["max_w"] = bw
                crop = frame[int(max(0, y1)):int(y2), int(max(0, x1)):int(x2)]
                if crop.size:
                    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    v["luma"] = float(np.median(g))
                    v["sharpness"] = float(cv2.Laplacian(g, cv2.CV_64F).var())
                    snap = frame.copy()
                    t = max(2, w // 400)
                    cv2.rectangle(snap, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), t + 1)
                    cv2.rectangle(snap, (0, 0), (w, 18 * t + 8), (0, 0, 0), -1)
                    cv2.putText(snap, f"NO PLATE VISIBLE  LOW  {self.camera_id}", (8, 14 * t),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45 * t, (255, 255, 255), max(1, t // 2))
                    if w > 960:
                        snap = cv2.resize(snap, (960, int(h * 960 / w)), interpolation=cv2.INTER_AREA)
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
            try:
                tid = int(str(rec.get("track_id", "")).rsplit("_", 1)[-1])
            except ValueError:
                continue
            v = self.views.pop(tid, None)
            if v is None:
                continue
            ok, _why = candidate(v, frame_w)
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
