"""Evidence pack writer (spec 9). For every CONFIRMED (and, optionally,
CANDIDATE) read: raw best crop, fused crop, enhanced crop, per-character
confidence bar, the frame with the box drawn, and the JSON record."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


def _confidence_bar(text: str, per_char: list[float], width: int = 480, height: int = 90) -> np.ndarray:
    img = np.full((height, width, 3), 255, np.uint8)
    n = max(len(text), 1)
    cw = width // n
    for i, (ch, p) in enumerate(zip(text, per_char)):
        x0 = i * cw
        col = (0, int(200 * p), int(200 * (1 - p)))
        bh = int((height - 30) * p)
        cv2.rectangle(img, (x0 + 4, height - 20 - bh), (x0 + cw - 4, height - 20), col, -1)
        cv2.putText(img, ch, (x0 + cw // 2 - 8, height - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        cv2.putText(img, f"{p:.2f}", (x0 + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (60, 60, 60), 1)
    return img


def write_evidence(root: str | Path, record: dict, best_crop: np.ndarray | None, fused: np.ndarray | None,
                   enhanced: np.ndarray | None, frame_bgr: np.ndarray | None, box=None,
                   per_char: list[float] | None = None) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    tid = record["track_id"]
    uris = {}
    if best_crop is not None:
        p = root / f"{tid}_best.png"
        cv2.imwrite(str(p), best_crop)
        uris["best_crop_uri"] = str(p.as_posix())
    if fused is not None:
        p = root / f"{tid}_fused.png"
        cv2.imwrite(str(p), fused)
        uris["fused_crop_uri"] = str(p.as_posix())
    if enhanced is not None:
        p = root / f"{tid}_enhanced.png"
        cv2.imwrite(str(p), enhanced)
        uris["enhanced_crop_uri"] = str(p.as_posix())
    if per_char and record.get("plate"):
        p = root / f"{tid}_charconf.png"
        cv2.imwrite(str(p), _confidence_bar(record["plate"], per_char))
        uris["char_conf_uri"] = str(p.as_posix())
    if frame_bgr is not None:
        f = frame_bgr.copy()
        if box is not None:
            x1, y1, x2, y2 = [int(v) for v in box]
            cv2.rectangle(f, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"{record.get('plate','')} {record.get('confidence',0):.2f} {record.get('status','')}"
            cv2.putText(f, label, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        vb = record.get("vehicle_box")
        if vb is not None:
            cv2.rectangle(f, (int(vb[0]), int(vb[1])), (int(vb[2]), int(vb[3])), (255, 160, 0), 1)
        p = root / f"{tid}_frame.jpg"
        cv2.imwrite(str(p), f, [cv2.IMWRITE_JPEG_QUALITY, 85])
        uris["frame_uri"] = str(p.as_posix())
    record = {**record, **uris}
    with open(root / f"{tid}.json", "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    return record
