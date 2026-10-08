"""A second, independent reading of a possible pursuit match, from a free Gemini model.

A possible sighting (app/target_check.py) means our reader finds the pursued plate LIKELY on a crop it
could not read outright. Before an operator looks, a different kind of model reads the same crops
open-ended: it is never told which plate we are after, so it cannot simply agree. Its reading is
compared with the pursued plate here, and the verdict travels with the sighting:

- "agrees": its reading is within one character of the plate, an unread character ('?') counting as one;
- "differs": the characters it did read are two or more away from the plate;
- "unsure": what it read fits the plate, but too much of it was unread to say;
- "unreadable": it could not read the plate at all.

It never removes a sighting, and it never makes one confirmed; it only orders the operator's attention.
Free tier only (GEMINI_API_KEY): a few calls a minute at most, cached per track, and skipped silently
when there is no key or no network.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request

import numpy as np

logger = logging.getLogger("vigentra.edge.second_opinion")

MODELS = [m.strip() for m in os.getenv("SECOND_OPINION_MODELS", "gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]
MIN_INTERVAL_S = 6.0      # free tier: well under its per-minute limit
TIMEOUT_S = 20.0
PROMPT = (
    "These are several views of the same Indian vehicle number plate from a traffic camera, stacked "
    "top to bottom. Transcribe the registration number exactly as printed (letters and digits only, no "
    "spaces). Write '?' for any character you cannot read with confidence; do not guess. If no plate "
    "characters are legible at all, answer UNREADABLE. Reply as JSON: {\"plate\": \"...\"}"
)

_last_call = 0.0


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def stack(crops: list[np.ndarray], height: int = 96) -> bytes:
    """The crops one above another, each scaled to the same height, as one JPEG."""
    import cv2

    rows = []
    for c in crops:
        h, w = c.shape[:2]
        rows.append(cv2.resize(c, (max(1, int(w * height / max(h, 1))), height), interpolation=cv2.INTER_CUBIC))
    width = max(r.shape[1] for r in rows)
    rows = [cv2.copyMakeBorder(r, 4, 4, 0, width - r.shape[1], cv2.BORDER_CONSTANT, value=(0, 0, 0)) for r in rows]
    ok, buf = cv2.imencode(".jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes() if ok else b""


def read(jpeg: bytes, model: str | None = None) -> tuple[str | None, str | None]:
    """(reading or None, model used). None when no key, no network, or every model refused."""
    global _last_call
    key = os.getenv("GEMINI_API_KEY")
    if not key or not jpeg:
        return None, None
    body = json.dumps({
        "contents": [{"parts": [{"text": PROMPT},
                                {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")}}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode()
    for m in ([model] if model else MODELS):
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                out = json.load(resp)
            text = out["candidates"][0]["content"]["parts"][0]["text"]
            plate = str(json.loads(text).get("plate", "")).upper()
            return "".join(ch for ch in plate if ch.isalnum() or ch == "?") or "UNREADABLE", m
        except urllib.error.HTTPError as exc:
            logger.info("second opinion: %s answered %s; trying the next model", m, exc.code)
        except Exception as exc:  # noqa: BLE001 - a side check: any failure means no opinion
            logger.info("second opinion: %s failed (%s)", m, type(exc).__name__)
    return None, None


def distance(reading: str, plate: str) -> int:
    """Edit distance, with an unread '?' matching any character for free."""
    prev = list(range(len(plate) + 1))
    for i, a in enumerate(reading, 1):
        cur = [i]
        for j, b in enumerate(plate, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if a in (b, "?") else 1)))
        prev = cur
    return prev[-1]


def verdict(reading: str | None, plate: str) -> str | None:
    if reading is None:
        return None
    if reading == "UNREADABLE" or not reading.strip("?"):
        return "unreadable"
    d, unread = distance(reading, plate), reading.count("?")
    if d >= 2:
        return "differs"
    return "agrees" if d + unread <= 1 else "unsure"
