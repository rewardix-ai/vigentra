"""RTSP frame source for the Sentinel sandbox.

Forces RTSP over TCP (UDP survives neither NAT nor most firewalls), drives
timing from PTS, reconnects with exponential backoff, and does not abort on
join-time decoder warnings (H.265 'Could not find ref with POC' until IDR).

Two backends:
  * opencv  - cv2.VideoCapture with FFMPEG backend and OPENCV_FFMPEG_CAPTURE_OPTIONS
  * ffmpeg  - ffmpeg subprocess piping rawvideo + a PTS side-channel (-vf showinfo)
The ffmpeg backend is the reliable one for mixed H.264/H.265 grids; opencv is
the zero-dependency fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

from .frame_source import FrameSource
from .grid import safe_url


class RTSPSource(FrameSource):
    def __init__(self, url: str, camera_id: str, backend: str = "ffmpeg", width: Optional[int] = None,
                 height: Optional[int] = None, max_reconnects: Optional[int] = None,
                 discontinuity_gap_ms: float = 2000.0):
        super().__init__(camera_id, max_reconnects=max_reconnects)
        self.url = url
        self.backend = backend
        self.width = width
        self.height = height
        self.discontinuity_gap_ms = discontinuity_gap_ms
        self._cap: Optional[cv2.VideoCapture] = None
        self._proc: Optional[subprocess.Popen] = None
        self._pts_q: deque = deque()
        self._stderr_thread: Optional[threading.Thread] = None
        self._t0 = time.monotonic()
        self._first_pts: Optional[float] = None
        self._prev_pts: Optional[float] = None

    @property
    def is_live(self) -> bool:
        return True

    # ------------------------------------------------------------------
    def _probe_size(self) -> tuple[int, int]:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise RuntimeError("ffprobe not found on PATH; install ffmpeg (https://ffmpeg.org) and add it to PATH")
        cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "csv=p=0", self.url]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        w, h = [int(x) for x in out.split(",")[:2]]
        return w, h

    def _open(self) -> None:
        if self.backend == "opencv":
            # socket deadline: FFmpeg >= 5 (OpenCV 5 bundles 7.x) knows only `timeout`, older builds
            # only `stimeout`; an unknown option is ignored, so both are passed
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|timeout;10000000|stimeout;10000000")
            self._cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                raise ConnectionError(f"RTSP open failed: {safe_url(self.url)}")
            return
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not found on PATH; install ffmpeg (https://ffmpeg.org) and add it to PATH")
        if self.width is None or self.height is None:
            self.width, self.height = self._probe_size()
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostats",
               "-rtsp_transport", "tcp", "-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay",
               "-err_detect", "ignore_err", "-i", self.url,
               "-vf", "showinfo", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10 ** 8)
        self._pts_q.clear()
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_thread.start()

    _SHOWINFO = re.compile(rb"pts_time:\s*([0-9.]+)")

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in iter(self._proc.stderr.readline, b""):
            m = self._SHOWINFO.search(line)
            if m:
                self._pts_q.append(float(m.group(1)) * 1000.0)
            # decoder warnings on join are expected; do not treat as fatal

    def _read(self) -> Optional[tuple[np.ndarray, float]]:
        if self.backend == "opencv":
            assert self._cap is not None
            for _ in range(30):  # tolerate corrupt frames on join
                ok, img = self._cap.read()
                if ok and img is not None:
                    pts = float(self._cap.get(cv2.CAP_PROP_POS_MSEC))
                    if pts <= 0:
                        pts = (time.monotonic() - self._t0) * 1000.0
                    return img, pts
            return None
        assert self._proc is not None and self._proc.stdout is not None
        n = self.width * self.height * 3
        buf = self._proc.stdout.read(n)
        if len(buf) < n:
            return None
        img = np.frombuffer(buf, dtype=np.uint8).reshape(self.height, self.width, 3).copy()
        # showinfo lines arrive on stderr slightly before/after the frame bytes; pair FIFO
        deadline = time.monotonic() + 0.2
        while not self._pts_q and time.monotonic() < deadline:
            time.sleep(0.001)
        pts = self._pts_q.popleft() if self._pts_q else (time.monotonic() - self._t0) * 1000.0
        return img, pts

    def _close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
