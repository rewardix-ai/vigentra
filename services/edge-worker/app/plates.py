"""Which detections can carry a number plate.

The adaptive sampler spends dense frames on these, because a vehicle wide
enough to carry a readable plate is about to show its best crop. The plate
itself is read by the track-level engine in `anpr_engine.py`.
"""
from __future__ import annotations

#: Only these classes are examined. Reading text off a person is not a thing
#: this system does.
PLATE_BEARING_CLASSES = frozenset({"car", "motorcycle", "bus", "truck", "auto-rickshaw"})
