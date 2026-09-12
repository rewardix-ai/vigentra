"""HLS mirror source. Same contract as RTSPSource; ffmpeg handles segment
stitching and PTS continuity. Used when RTSP is firewalled."""
from __future__ import annotations

from typing import Optional

from .rtsp_source import RTSPSource


class HLSSource(RTSPSource):
    def __init__(self, url: str, camera_id: str, backend: str = "ffmpeg", width: Optional[int] = None,
                 height: Optional[int] = None, max_reconnects: Optional[int] = None,
                 cookie: Optional[str] = None, user_agent: Optional[str] = None):
        super().__init__(url, camera_id, backend=backend, width=width, height=height,
                         max_reconnects=max_reconnects)
        # Sentinel HLS mirror (cctv.corp8.cloud): session cookie from POST /auth/login
        # (email + password), presented with the sign-in's browser UA + Referer / Origin
        # (anpr/sources/grid.hls_http_args), AES-128 segments
        from .grid import BROWSER_UA
        self.cookie = cookie
        self.user_agent = user_agent or BROWSER_UA

    def _http_args(self) -> list[str]:
        from .grid import hls_http_args
        return hls_http_args(self.cookie, self.user_agent)

    def _probe_size(self):
        import shutil, subprocess
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise RuntimeError("ffprobe not found on PATH; install ffmpeg and add it to PATH")
        cmd = [ffprobe, "-v", "error", *self._http_args(), "-select_streams", "v:0", "-show_entries", "stream=width,height",
               "-of", "csv=p=0", self.url]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
        w, h = [int(x) for x in out.split(",")[:2]]
        return w, h

    def _open(self) -> None:
        # HLS has no rtsp_transport flag; strip it by overriding the command.
        import shutil, subprocess, threading
        if self.backend == "opencv":
            import cv2
            self._cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                raise ConnectionError(f"HLS open failed: {self.url}")
            return
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg not found on PATH; install ffmpeg and add it to PATH")
        if self.width is None or self.height is None:
            self.width, self.height = self._probe_size()
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "info", "-nostats", *self._http_args(), "-live_start_index", "-1",
               "-fflags", "nobuffer+discardcorrupt", "-err_detect", "ignore_err", "-i", self.url,
               "-vf", "showinfo", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10 ** 8)
        self._pts_q.clear()
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_thread.start()
