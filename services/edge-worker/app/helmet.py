"""Two-wheeler rider without a helmet: a candidate for a human to look at.

Each two-wheeler's closest view is kept while it is tracked (the vehicle box widened 25 % each side and
raised well above the handlebars so the rider's head is in it). When the track ends, that crop is
classified once by a small YOLO11n classifier trained on our own grid riders (models/helmet_cls.pt:
no_helmet / helmet / unclear, labelled by eye; ANPR research repo tools/rider_crops.py, rider_mine.py).

Measured on 134 held-out grid crops (8 Oct): at score >= 0.9 about 9 in 10 calls are right and no rider
wearing a helmet scored above 0.77; it finds roughly 4 in 10 bare-headed riders. Live, by eye (8 Oct, the
first 14 incidents): 10 of 11 judgeable calls right; the miss was a full-face helmet with a face mask. Its remaining mistakes
are crops where the head is out of view, so a person confirms every candidate from the snapshot.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("vigentra.edge.helmet")

WEIGHTS = Path(os.getenv("HELMET_CLS_WEIGHTS", str(Path(__file__).resolve().parent.parent / "models" / "helmet_cls.pt")))
THRESHOLD = float(os.getenv("HELMET_NO_HELMET_SCORE", "0.9"))
MIN_WIDTH = 120.0           # px: below this a person cannot confirm the head from the snapshot (8 Oct live
                            # audit: the three calls at 100-105 px, all night, could not be judged by eye)
END_AFTER_S = 3.0           # a track unseen this long has ended
MIN_TRAVEL_W = 1.0          # it must move at least its own width while tracked: a parked scooter with
                            # someone sitting beside it is not a rider (cam25, 8 Oct, the first live miss)
CLASSES = frozenset({"motorcycle"})

_model = None
_lock = threading.Lock()   # one classifier for every camera thread; predict is not thread-safe


def _classifier():
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(str(WEIGHTS))
    return _model


def available() -> bool:
    return WEIGHTS.exists()


def no_helmet_score(crop) -> float:
    with _lock:
        m = _classifier()
        r = m.predict(crop, imgsz=224, verbose=False, device="cpu")[0]
        idx = [k for k, v in m.names.items() if v == "no_helmet"][0]
        return float(r.probs.data[idx])


class HelmetWatch:
    """Per camera: the closest view of each two-wheeler, judged once when its track ends."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self.views: dict[int, dict] = {}

    def observe(self, frame, detections, pts: float) -> list[dict]:
        import cv2

        h, w = frame.shape[:2]
        seen = set()
        for d in detections:
            tid = (getattr(d, "extra", None) or {}).get("track_id")
            if tid is None or d.class_name not in CLASSES:
                continue
            seen.add(tid)
            x1, y1, x2, y2 = d.bbox_xyxy
            bw, bh = x2 - x1, y2 - y1
            c = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            v = self.views.setdefault(tid, {"first_pts": pts, "max_w": 0.0, "crop": None, "jpeg": None,
                                            "first_c": c, "travel": 0.0})
            v["last_pts"] = pts
            v["travel"] = max(v["travel"], ((c[0] - v["first_c"][0]) ** 2 + (c[1] - v["first_c"][1]) ** 2) ** 0.5 / max(bw, 1.0))
            if bw < MIN_WIDTH or bw <= v["max_w"]:
                continue
            up = max(0.5 * bh, 1.2 * bw)   # high enough that the rider's head is in the crop
            cx1, cy1 = int(max(0, x1 - 0.25 * bw)), int(max(0, y1 - up))
            cx2, cy2 = int(min(w, x2 + 0.25 * bw)), int(min(h, y2))
            if cy1 == 0 and y1 - up < -0.15 * bh:
                continue   # the head would be above the frame: this view cannot show it
            crop = frame[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue
            v["max_w"], v["crop"] = bw, crop.copy()
            snap = frame.copy()
            t = max(2, w // 400)
            cv2.rectangle(snap, (cx1, cy1), (cx2, cy2), (0, 0, 255), t + 1)
            cv2.rectangle(snap, (0, 0), (w, 18 * t + 8), (0, 0, 0), -1)
            cv2.putText(snap, f"NO HELMET  LOW  {self.camera_id}", (8, 14 * t), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45 * t, (255, 255, 255), max(1, t // 2))
            if w > 960:
                snap = cv2.resize(snap, (960, int(h * 960 / w)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", snap, [cv2.IMWRITE_JPEG_QUALITY, 80])
            v["jpeg"] = buf.tobytes() if ok else None
        out = []
        for tid in [t for t, v in self.views.items() if t not in seen and pts - v["last_pts"] > END_AFTER_S]:
            v = self.views.pop(tid)
            if v["crop"] is None or v["travel"] < MIN_TRAVEL_W:
                continue   # never seen close, or never moved: not a rider on the road
            try:
                score = no_helmet_score(v["crop"])
            except Exception as exc:  # pragma: no cover - a side check never stops the pass
                logger.warning("helmet check failed: %s", exc)
                continue
            if score < THRESHOLD:
                continue
            payload = {
                "camera_id": self.camera_id, "kind": "NO_HELMET", "severity": "LOW", "track_ids": [int(tid)],
                "first_seen": float(v["first_pts"]), "last_seen": float(v["last_pts"]),
                "reason": f"two-wheeler rider who appears bare-headed (classifier {score:.2f}, at {int(v['max_w'])} px)",
                "evidence": {"no_helmet_score": round(score, 3), "widest_px": round(v["max_w"]),
                             "threshold": THRESHOLD},
            }
            if v["jpeg"]:
                payload["snapshot_jpeg_b64"] = base64.b64encode(v["jpeg"]).decode("ascii")
            out.append(payload)
        return out
