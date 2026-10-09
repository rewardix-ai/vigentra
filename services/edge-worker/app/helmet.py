"""Two-wheeler rider without a helmet: a candidate for a human to look at.

Each two-wheeler's closest views are kept while it is tracked (the vehicle box widened 25 % each side and
raised well above the handlebars so the rider's head is in it): up to VIEWS of them, each at least
VIEW_SHARE of the widest. When the track ends they are classified and the scores averaged, so one bad
frame (head turned, motion blur) neither makes nor blocks a call. The classifier is a small YOLO11n
classifier trained on our own grid riders (models/helmet_cls.pt:
no_helmet / helmet / unclear, labelled by eye; ANPR research repo tools/rider_crops.py, rider_mine.py).

Threshold 0.8 and riders from 70 px (8 Oct, for more calls): on the same held-out riders 23 calls, 21
right, none on a helmet. Measured per crop on 203 held-out grid riders cut the live way (8 Oct, ANPR repo tools/helmet_pair_eval.py):
the three averaged, at >= 0.85, made 20 calls, 19 right, none on a rider wearing a helmet, and found 19
of the 45 bare-headed riders; v2 alone at 0.9 made 27 calls, 23 right, one on a helmet (a full-face helmet
with a face mask, also the one miss of the first 14 live incidents). Its remaining mistakes are crops
where the head is out of view, so a person confirms every candidate from the snapshot.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
from collections import Counter
from pathlib import Path

from . import vehicle_type

logger = logging.getLogger("vigentra.edge.helmet")

MODELS = Path(__file__).resolve().parent.parent / "models"
#: three classifiers, their no-helmet scores averaged: v2 (models/helmet_cls.pt), v4 fine-tuned from it on
#: the riders labelled 8 Oct and cut exactly as below, and v5 fine-tuned from v4 on 200 more. v2 alone puts
#: some helmeted riders above 0.95 (cam06's looping checked-shirt rider in a black helmet, 8 Oct live);
#: the later two, trained on such riders, pull them below the threshold.
#: A name ending _head.pt sees only the head band of the crop (head_band): the head ~1.7x larger.
#: v7 (full crop) and v8 (head band) were fine-tuned from v5 and v6 on 79 more riders the live average
#: found borderline, plus the two cam06 helmeted riders it called bare (9 Oct). Held out (221 riders,
#: 50 bare-headed), at >= 0.8: 21 calls, all right, none on a helmet (v2+v4+v5+v6: 23 calls, 22 right).
#: The cam06 black-helmet rider now averages 0.42 (was 0.79).
WEIGHTS = [Path(p) for p in os.getenv(
    "HELMET_CLS_WEIGHTS", ",".join(str(MODELS / n) for n in (
        "helmet_cls.pt", "helmet_cls_v4.pt", "helmet_cls_v7.pt", "helmet_cls_v8_head.pt"))).split(",") if p]
THRESHOLD = float(os.getenv("HELMET_NO_HELMET_SCORE", "0.8"))
MIN_WIDTH = 70.0            # px: held out, riders 60-120 px wide drew 7 calls at >= 0.8, all right; the snapshot
                            # carries the judged rider enlarged, so a person can check a small one
SAME_RIDER_S, SAME_RIDER_W = 2.0, 2.5   # a call whose track starts within this time and this many widths of
                            # where a raised rider was last seen is that rider again under a new track id
                            # (cam06, 8 Oct: one red-shirted rider called twice a second apart)
LOOKALIKE_S, LOOKALIKE_W, LOOKALIKE_CORR = 10.0, 3.0, 0.85  # ...or, with a longer gap (light mode can lose a
                            # rider for several seconds), one whose colours match: the same rider again
                            # (cam30, 9 Oct: one woman called twice, her second track starting > 2 s later;
                            # her two crops correlate 0.94, two different cam06 riders 0.72)
TWO_WHEELER_MIN = 0.1       # the vehicle-type classifier must find it plausibly a motorbike or scooter
PARKED_FRAMES, PARKED_S = 4, 3.0   # only a two-wheeler seen this often, this long, and not moving is parked:
                            # light mode often sees a passing rider in one or two frames (8 Oct: 36 of 78
                            # two-wheelers were set aside as parked, most of them just seen too briefly)
END_AFTER_S = 3.0           # a track unseen this long has ended
VIEWS = 3                   # closest views judged per rider, scores averaged
VIEW_SHARE = 0.8            # a view counts if it is at least this share of the widest
MIN_TRAVEL_W = 1.0          # it must move at least its own width while tracked: a parked scooter with
                            # someone sitting beside it is not a rider (cam25, 8 Oct, the first live miss)
CLASSES = frozenset({"motorcycle", "scooter"})

#: what became of each two-wheeler track, for the reader's per-minute log (light mode)
TALLY: Counter = Counter()

_models: list = []
_lock = threading.Lock()   # one classifier for every camera thread; predict is not thread-safe


def head_band(crop):
    """The part of a rider crop the head is in: 5-65 % of its height, the middle 80 % of its width."""
    h, w = crop.shape[:2]
    return crop[int(0.05 * h):int(0.65 * h), int(0.1 * w):int(0.9 * w)]


def _classifiers() -> list:
    """(model, sees the head band only) for every installed classifier."""
    if not _models:
        from ultralytics import YOLO
        _models.extend((YOLO(str(w)), w.stem.endswith("_head")) for w in WEIGHTS if w.exists())
    return _models


def available() -> bool:
    return any(w.exists() for w in WEIGHTS)


def no_helmet_score(crop) -> float:
    """The classifiers' mean probability that the rider in this crop is bare-headed."""
    with _lock:
        scores = []
        for m, head in _classifiers():
            r = m.predict(head_band(crop) if head else crop, imgsz=224, verbose=False, device="cpu")[0]
            idx = [k for k, v in m.names.items() if v == "no_helmet"][0]
            scores.append(float(r.probs.data[idx]))
        return sum(scores) / len(scores)


def _colours(crop):
    """Hue-saturation histogram of a rider crop: what tells one rider's clothes from another's."""
    import cv2

    if crop is None or crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).flatten()


class HelmetWatch:
    """Per camera: the closest view of each two-wheeler, judged once when its track ends."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        self.views: dict[int, dict] = {}
        self.raised: list[tuple] = []   # (last pts, last centre, width, colour histogram) of riders called

    def observe(self, frame, detections, pts: float) -> list[dict]:
        import cv2
        import numpy as np

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
            v = self.views.setdefault(tid, {"first_pts": pts, "max_w": 0.0, "crops": [], "jpeg": None,
                                            "first_c": c, "travel": 0.0, "frames": 0, "path": []})
            v["last_pts"], v["last_c"] = pts, c
            v["path"] = (v["path"] + [c])[-40:]
            v["frames"] += 1
            v["travel"] = max(v["travel"], ((c[0] - v["first_c"][0]) ** 2 + (c[1] - v["first_c"][1]) ** 2) ** 0.5 / max(bw, 1.0))
            if bw < MIN_WIDTH or (len(v["crops"]) >= VIEWS and bw <= min(cw for cw, _ in v["crops"])):
                continue
            up = max(0.5 * bh, 1.2 * bw)   # high enough that the rider's head is in the crop
            cx1, cy1 = int(max(0, x1 - 0.25 * bw)), int(max(0, y1 - up))
            cx2, cy2 = int(min(w, x2 + 0.25 * bw)), int(min(h, y2))
            if cy1 == 0 and y1 - up < -0.15 * bh:
                continue   # the head would be above the frame: this view cannot show it
            crop = frame[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue
            v["crops"] = sorted(v["crops"] + [(bw, crop.copy())], key=lambda k: -k[0])[:VIEWS]
            if bw <= v["max_w"]:
                continue
            v["max_w"] = bw
            bx1, by1 = int(max(0, x1 - 0.08 * bw)), int(max(0, y1 - 0.08 * bh))
            bx2, by2 = int(min(w, x2 + 0.08 * bw)), int(min(h, y2 + 0.08 * bh))
            v["vehicle"] = frame[by1:by2, bx1:bx2].copy()   # cut as the vehicle-type classifier was trained
            snap = frame.copy()
            t = max(2, w // 400)
            cv2.rectangle(snap, (cx1, cy1), (cx2, cy2), (0, 0, 255), t + 1)
            # the judged rider, enlarged, in the corner away from it: a small rider can be checked by eye
            k = min(3.0, 0.45 * h / max(1, crop.shape[0]))
            if k > 1.0:
                inset = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
                inset = cv2.copyMakeBorder(inset, t, t, t, t, cv2.BORDER_CONSTANT, value=(0, 0, 255))
                ih, iw = inset.shape[:2]
                if iw < w // 2 and ih < h:
                    ox = 0 if (cx1 + cx2) / 2 > w / 2 else w - iw
                    snap[0:ih, ox:ox + iw] = inset
            # the title goes on a strip above the frame, never over it: a rider near the top edge has
            # its head there (cam06, 8 Oct: a call that could not be checked because the title hid it)
            strip = np.zeros((18 * t + 8, w, 3), np.uint8)
            cv2.putText(strip, f"NO HELMET  LOW  {self.camera_id}", (8, 14 * t), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45 * t, (255, 255, 255), max(1, t // 2))
            snap = np.vstack([strip, snap])
            h_snap = snap.shape[0]
            if w > 960:
                snap = cv2.resize(snap, (960, int(h_snap * 960 / w)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", snap, [cv2.IMWRITE_JPEG_QUALITY, 80])
            v["jpeg"] = buf.tobytes() if ok else None
        out = []
        for tid in [t for t, v in self.views.items() if t not in seen and pts - v["last_pts"] > END_AFTER_S]:
            v = self.views.pop(tid)
            if not v["crops"]:
                TALLY["never_close"] += 1
                continue   # never seen close enough to judge the head
            if (v["travel"] < MIN_TRAVEL_W and v["frames"] >= PARKED_FRAMES
                    and v["last_pts"] - v["first_pts"] >= PARKED_S):
                TALLY["parked"] += 1
                continue   # never moved: not a rider on the road
            if v.get("vehicle") is not None and vehicle_type.available():
                try:
                    if vehicle_type.two_wheeler_score(v["vehicle"]) < TWO_WHEELER_MIN:
                        TALLY["not_a_two_wheeler"] += 1
                        continue   # a cycle cart, an auto or a handcart the detector took for a motorcycle
                except Exception as exc:  # pragma: no cover - a side check never stops the pass
                    logger.warning("two-wheeler check failed: %s", exc)
            views = [crop for cw, crop in v["crops"] if cw >= VIEW_SHARE * v["max_w"]]
            try:
                scores = [no_helmet_score(crop) for crop in views]
                score = sum(scores) / len(scores)
            except Exception as exc:  # pragma: no cover - a side check never stops the pass
                logger.warning("helmet check failed: %s", exc)
                continue
            if score < THRESHOLD:
                TALLY["judged_ok" if score < 0.5 else "judged_unsure"] += 1
                continue
            fc, fw = v["first_c"], max(v["max_w"], 1.0)
            look = _colours(views[0])

            def same(t_first, t_last, path_last, w_last, h_last):
                # time apart: 0 when the tracks overlap (an id switch mid-track), else the gap between them
                gap = max(0.0, v["first_pts"] - t_last, t_first - v["last_pts"])
                # nearest approach of the two paths, in widths: a new id can pick the rider up anywhere along
                # the old one's path (cam06, 9 Oct: tracks 4 then 3 on one rider a second apart)
                far = min(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a in v["path"] for b in path_last) / max(fw, w_last)
                if gap <= SAME_RIDER_S and far <= SAME_RIDER_W:
                    return True
                return (gap <= LOOKALIKE_S and far <= LOOKALIKE_W and h_last is not None and look is not None
                        and float(cv2.compareHist(look, h_last, cv2.HISTCMP_CORREL)) >= LOOKALIKE_CORR)

            if any(same(*r) for r in self.raised):
                TALLY["same_rider_again"] += 1
                continue
            self.raised = [r for r in self.raised if v["last_pts"] - r[1] <= 15.0] + [
                (v["first_pts"], v["last_pts"], list(v["path"]), fw, look)]
            TALLY["raised"] += 1
            payload = {
                "camera_id": self.camera_id, "kind": "NO_HELMET", "severity": "LOW", "track_ids": [int(tid)],
                "first_seen": float(v["first_pts"]), "last_seen": float(v["last_pts"]),
                "reason": f"two-wheeler rider who appears bare-headed (classifier {score:.2f} over {len(scores)} "
                           f"view{'s' if len(scores) > 1 else ''}, at {int(v['max_w'])} px)",
                "evidence": {"no_helmet_score": round(score, 3), "view_scores": [round(x, 3) for x in scores],
                             "widest_px": round(v["max_w"]), "threshold": THRESHOLD,
                             # where the track began and ended: what tells a second call on one rider apart
                             "track_from": [round(v["first_c"][0]), round(v["first_c"][1]), round(v["first_pts"], 2)],
                             "track_to": [round(v["last_c"][0]), round(v["last_c"][1]), round(v["last_pts"], 2)]},
            }
            if v["jpeg"]:
                payload["snapshot_jpeg_b64"] = base64.b64encode(v["jpeg"]).decode("ascii")
            out.append(payload)
        return out
