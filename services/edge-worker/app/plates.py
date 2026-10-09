"""Which detections can carry a number plate.

The adaptive sampler spends dense frames on these, because a vehicle wide
enough to carry a readable plate is about to show its best crop. The plate
itself is read by the track-level engine in `anpr_engine.py`.
"""
from __future__ import annotations

#: Only these classes are examined. Reading text off a person is not a thing
#: this system does.
PLATE_BEARING_CLASSES = frozenset({"car", "motorcycle", "scooter", "bus", "truck", "auto-rickshaw",
                                   "goods-auto", "lcv", "minibus", "tractor"})


#: a read this close (character edits, same first two letters) to a camera's known scene text is that
#: text, not a plate: GJ05DB3008 is 3 edits from TS75DB3008 but a real Gujarat plate
SCENE_TEXT_MAX_EDITS = 3
_SCENE_TEXT: dict[str, list[str]] | None = None


def _edits(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def scene_text(camera_id: str, text: str, config_dir: str | None = None) -> bool:
    """Is this read on this camera really scene text (a signboard) listed in config/scene_text.yaml?"""
    global _SCENE_TEXT
    if _SCENE_TEXT is None:
        import os
        from pathlib import Path

        import yaml

        path = Path(config_dir or os.environ.get("ANPR_CONFIG_DIR") or Path(__file__).resolve().parents[1] / "config") / "scene_text.yaml"
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except (OSError, ValueError):
            raw = {}
        _SCENE_TEXT = {str(k).lower(): [str(t).upper() for t in (v or [])] for k, v in raw.items()}
    cam = (camera_id or "").lower()
    plate = "".join(c for c in (text or "").upper() if c.isalnum())
    return any(key in cam and any(plate[:2] == t[:2] and _edits(plate, t) <= SCENE_TEXT_MAX_EDITS for t in texts)
               for key, texts in _SCENE_TEXT.items())
