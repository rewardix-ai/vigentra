"""Abstract frame source. All timing is PTS-driven; never trust CAP_PROP_FPS.

A source yields Frame objects with a monotonically non-decreasing pts_ms.
Sources must be robust: reconnect with exponential backoff, treat join-time
decoder warnings as non-fatal, and flag scene discontinuities so the tracker
can terminate tracks at loop seams.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

import numpy as np


@dataclass
class Frame:
    image: np.ndarray            # BGR uint8 HxWx3
    pts_ms: float                # presentation timestamp in ms (source clock)
    frame_idx: int               # running index in this session
    camera_id: str
    discontinuity: bool = False  # True on the first frame after a seam/reconnect
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape[0], self.image.shape[1]


class FrameSource:
    """Iterator protocol over Frames. Subclasses implement _open/_read."""

    def __init__(self, camera_id: str, backoff_start_s: float = 2.0, backoff_cap_s: float = 30.0,
                 max_reconnects: Optional[int] = None):
        self.camera_id = camera_id
        self.backoff_start_s = backoff_start_s
        self.backoff_cap_s = backoff_cap_s
        self.max_reconnects = max_reconnects
        self._frame_idx = 0
        self._last_pts: Optional[float] = None
        self._reconnects = 0
        self._pending_jump: Optional[tuple[float, float]] = None

    # -- subclass API ---------------------------------------------------
    def _open(self) -> None:
        raise NotImplementedError

    def _read(self) -> Optional[tuple[np.ndarray, float]]:
        """Return (image, pts_ms) or None at end-of-stream / failure."""
        raise NotImplementedError

    def _close(self) -> None:
        pass

    @property
    def is_live(self) -> bool:
        return False

    # -- iteration ------------------------------------------------------
    def __iter__(self) -> Iterator[Frame]:
        backoff = self.backoff_start_s
        self._open()
        discontinuity = True
        while True:
            item = self._read()
            if item is None:
                if not self.is_live:
                    break
                self._close()
                if self.max_reconnects is not None and self._reconnects >= self.max_reconnects:
                    break
                self._reconnects += 1
                time.sleep(backoff)
                backoff = min(backoff * 2, self.backoff_cap_s)
                self._open()
                discontinuity = True
                continue
            backoff = self.backoff_start_s
            img, pts = item
            # PTS sanity: a large backwards jump (loop seam / new segment) or a large
            # forward gap (stall, reconnect) is a discontinuity. Small jitter is not:
            # lossy sandbox streams show ~4 % of frames with slightly backward PTS
            # (measured cam27: 12 of 326), and flagging those reset the tracker six
            # times in 60 s and fragmented every track.
            # Two-frame hysteresis: cam27 in the sandbox shows isolated +-6 s PTS spikes
            # (broken timestamps on a lossy relay) that snap straight back; a real loop
            # seam or reconnect keeps the new time base on the following frame.
            if self._last_pts is not None:
                jumped = pts + 500.0 < self._last_pts or pts > self._last_pts + 5000.0
                if jumped and self._pending_jump is None:
                    self._pending_jump = (self._last_pts, pts)      # candidate; decide on the next frame
                    self._last_pts = pts
                    yield Frame(img, pts, self._frame_idx, self.camera_id, False)
                    self._frame_idx += 1
                    continue
                if self._pending_jump is not None:
                    before, at = self._pending_jump
                    self._pending_jump = None
                    # persisted if this frame continues from the jumped time base rather than from 'before'
                    if abs(pts - at) < abs(pts - before):
                        discontinuity = True
            self._last_pts = pts
            yield Frame(img, pts, self._frame_idx, self.camera_id, discontinuity)
            discontinuity = False
            self._frame_idx += 1
        self._close()

    def close(self) -> None:
        self._close()
