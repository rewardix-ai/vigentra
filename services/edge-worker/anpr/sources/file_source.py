"""File-backed frame source (development path). PTS from CAP_PROP_POS_MSEC."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .frame_source import FrameSource


class FileSource(FrameSource):
    def __init__(self, path: str | Path, camera_id: str = "file", start_ms: float = 0.0,
                 end_ms: Optional[float] = None, stride: int = 1, loop: bool = False):
        super().__init__(camera_id)
        self.path = str(path)
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.stride = max(1, int(stride))
        self.loop = loop
        self._cap: Optional[cv2.VideoCapture] = None
        self._raw_idx = 0

    def _open(self) -> None:
        self._cap = cv2.VideoCapture(self.path)
        if not self._cap.isOpened():
            raise FileNotFoundError(f"cannot open video: {self.path}")
        if self.start_ms > 0:
            self._cap.set(cv2.CAP_PROP_POS_MSEC, self.start_ms)
        self._raw_idx = 0

    def _read(self) -> Optional[tuple[np.ndarray, float]]:
        assert self._cap is not None
        while True:
            ok = self._cap.grab()
            if not ok:
                if self.loop:
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._raw_idx = 0
                    continue
                return None
            pts = float(self._cap.get(cv2.CAP_PROP_POS_MSEC))
            self._raw_idx += 1
            if self.end_ms is not None and pts > self.end_ms:
                return None
            if (self._raw_idx - 1) % self.stride != 0:
                continue
            ok, img = self._cap.retrieve()
            if not ok or img is None:
                continue
            return img, pts

    def _close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_live(self) -> bool:
        return False

    @property
    def nominal_fps(self) -> float:
        """Advisory only. Never used for timing."""
        cap = cv2.VideoCapture(self.path)
        try:
            return float(cap.get(cv2.CAP_PROP_FPS))
        finally:
            cap.release()
