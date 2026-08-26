"""Capture from the Sentinel camera grid, by its own rules.

Every requirement in §3 of the integrator's guide
(https://sentinel.gujarat.gov.in/resource) is enforced here, because this is
the only module that touches the grid's media plane directly.

The rules, and where each one lives:

  DO force RTSP over TCP            `_force_tcp_transport`
  DON'T trust the reported frame rate
                                    nothing reads CAP_PROP_FPS; `measured_fps`
                                    is computed from PTS deltas instead
  DO drive timing from PTS          `Frame.pts_ms` from CAP_PROP_POS_MSEC
  DON'T assume a constant frame rate
                                    `Frame.dt_ms` is the real elapsed PTS gap;
                                    gaps are tolerated, not treated as a drop
  DO reconnect with backoff         `ReconnectingCapture`, 2 s -> 30 s
  DON'T treat join-time decode warnings as fatal
                                    `GRACE_FAILURES` before declaring a drop
  DON'T assume a uniform grid       properties come from the catalogue per
                                    camera; nothing is hard-coded
  DO expect a scene discontinuity   a backwards PTS jump raises `discontinuity`
  DON'T plan around obtaining copies
                                    frames are decoded from a live capture;
                                    nothing is ever written to disk
  DON'T publish to the gateway      this module only ever reads
  DO pace your load                 one capture per camera, released on exit

Transport: RTSP is what the guide recommends for inference. Port 8554 is
blocked on many networks, and the guide sanctions HLS explicitly for that case,
so `open_capture` probes RTSP and falls back to HLS rather than failing.
"""
from __future__ import annotations

import logging
import os
import socket
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger("sentinel.edge.grid")

CATALOGUE_PATH = "/api/ingest"
#: The gateway 302s to http:// without this, which breaks a TLS-only client.
COOKIE_CHECK = "cookieCheck=1"


def _force_tcp_transport() -> None:
    """Pin FFmpeg's RTSP demuxer to TCP.

    UDP is accepted by the grid but fails across NAT and most corporate
    firewalls, and partial UDP delivery produces corrupt frames that look
    exactly like model bugs. This is a process-wide FFmpeg option read when the
    first capture is constructed, so it has to be set before any VideoCapture
    exists - setting it afterwards silently does nothing.
    """
    existing = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    if "rtsp_transport" in existing:
        return
    merged = "rtsp_transport;tcp"
    if existing:
        merged = f"{existing}|{merged}"
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = merged


@dataclass(frozen=True)
class GridCamera:
    """One camera as the catalogue describes it, not as we assume it to be."""

    id: str
    name: str
    location: str
    live: bool
    codec: str | None
    width: int | None
    height: int | None
    #: The catalogue's own claim. Recorded for provenance, never used for
    #: timing - see `DON'T trust the reported frame rate`.
    declared_fps: float | None
    rtsp_url: str
    hls_url: str

    @property
    def described(self) -> str:
        size = f"{self.width}x{self.height}" if self.width else "size unknown"
        return f"{self.id} ({self.codec or 'codec unknown'}, {size})"


def fetch_catalogue(base_url: str, timeout: float = 15.0) -> dict[str, GridCamera]:
    """Read /api/ingest.

    The catalogue is the contract and the URL pattern is not: camera ids and
    the set of cameras change, so callers resolve through this rather than
    building rtsp:// strings by hand.
    """
    url = base_url.rstrip("/") + CATALOGUE_PATH
    request = urllib.request.Request(url, headers={"User-Agent": "sentinel-edge-worker/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        import json

        payload = json.load(response)

    cameras: dict[str, GridCamera] = {}
    for entry in payload.get("cameras", []):
        cid = str(entry.get("id") or "").strip()
        if not cid:
            continue
        hls = str(entry.get("hls_live_url") or "")
        if hls.startswith("/"):
            hls = base_url.rstrip("/") + hls
        cameras[cid] = GridCamera(
            id=cid,
            name=str(entry.get("name") or f"Camera {cid}"),
            location=str(entry.get("location") or ""),
            live=bool(entry.get("live") or entry.get("status") == "live"),
            codec=(entry.get("codec") or None),
            width=(entry.get("width") or None),
            height=(entry.get("height") or None),
            declared_fps=(entry.get("fps") or None),
            rtsp_url=str(entry.get("rtsp_url") or ""),
            hls_url=hls,
        )
    return cameras


def _port_open(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _with_cookie_check(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query["cookieCheck"] = ["1"]
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
    )


@dataclass(frozen=True)
class Frame:
    """A decoded frame and the timing that belongs to it."""

    index: int
    image: Any
    #: Presentation timestamp, from the stream. Never arrival time.
    pts_ms: float
    #: Elapsed PTS since the previous frame. None on the first frame and
    #: across a discontinuity. Feed THIS to a tracker's motion model, not a
    #: fixed cadence derived from a declared frame rate.
    dt_ms: float | None
    #: True when the feed looped and the scene cut. Long-lived state -
    #: background models, re-identification galleries, track ids - must reset.
    discontinuity: bool


class ReconnectingCapture:
    """A live capture that survives the things the guide says will happen.

    Feeds are supervised and restart; playlists briefly 502; attaching
    mid-stream on an H.265 camera throws decoder errors until the first IDR
    arrives. None of those is fatal, and a client that treats them as fatal
    bounces permanently on exactly the cameras that need watching.
    """

    MIN_BACKOFF = 2.0
    MAX_BACKOFF = 30.0
    #: Failed reads tolerated after a (re)connect before declaring a real drop.
    #: Join-time decoder noise on a mixed H.264/H.265 grid is normal.
    GRACE_FAILURES = 25
    #: A PTS that goes backwards by more than this is the documented scene
    #: loop, not jitter.
    LOOP_TOLERANCE_MS = 500.0

    def __init__(self, url: str, *, label: str = "") -> None:
        _force_tcp_transport()
        self.url = url
        self.label = label or url
        self._capture: Any = None
        self._backoff = self.MIN_BACKOFF
        self._consecutive_failures = 0
        self._last_pts: float | None = None
        self._index = 0
        self._first_pts: float | None = None
        self._frames_seen = 0

    # -- lifecycle ---------------------------------------------------------

    def _connect(self) -> None:
        import cv2

        if self._capture is not None:
            self._capture.release()
        logger.info("[connect] %s", self.label)
        self._capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        self._consecutive_failures = 0

    def _reconnect(self) -> None:
        logger.info("[reconnect] %s in %.1fs", self.label, self._backoff)
        time.sleep(self._backoff)
        # Exponential, capped. Never a tight loop - a reconnect storm is worse
        # for a shared sandbox than a slow recovery.
        self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)
        self._connect()

    def release(self) -> None:
        """Give the capture back. Each connected client gets its own copy of
        the stream, so holding one you are not processing is load abuse."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> "ReconnectingCapture":
        self._connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    # -- reading -----------------------------------------------------------

    def frames(self, max_frames: int | None = None) -> Iterator[Frame]:
        """Yield decoded frames until `max_frames`, or for ever.

        Benign gaps yield nothing and do not raise; the loop simply continues.
        """
        if self._capture is None:
            self._connect()

        while max_frames is None or self._frames_seen < max_frames:
            ok, image = self._capture.read()

            if not ok:
                self._consecutive_failures += 1
                if self._consecutive_failures > self.GRACE_FAILURES:
                    self._reconnect()
                continue

            # A good read means the stream is healthy again.
            self._backoff = self.MIN_BACKOFF
            self._consecutive_failures = 0

            import cv2

            pts_ms = float(self._capture.get(cv2.CAP_PROP_POS_MSEC))

            discontinuity = False
            dt_ms: float | None = None
            if self._last_pts is not None:
                delta = pts_ms - self._last_pts
                if delta < -self.LOOP_TOLERANCE_MS:
                    # The feed is a continuous recording that loops; at the
                    # loop point the scene cuts like a camera reboot.
                    discontinuity = True
                    logger.info("[loop] %s scene discontinuity - reset track state", self.label)
                    self._first_pts = pts_ms
                else:
                    dt_ms = delta
            else:
                self._first_pts = pts_ms

            self._last_pts = pts_ms
            self._index += 1
            self._frames_seen += 1

            yield Frame(
                index=self._index,
                image=image,
                pts_ms=pts_ms,
                dt_ms=dt_ms,
                discontinuity=discontinuity,
            )

    # -- measured, not declared -------------------------------------------

    @property
    def measured_fps(self) -> float | None:
        """Frame rate derived from PTS, or None until there is enough span.

        The catalogue's `fps` and OpenCV's CAP_PROP_FPS both routinely
        disagree with the real delivery rate, and using either to turn
        pixels-per-frame into speed or dwell time produces confidently wrong
        numbers. This is the only frame rate this module will report.
        """
        if self._first_pts is None or self._last_pts is None or self._frames_seen < 2:
            return None
        span_ms = self._last_pts - self._first_pts
        if span_ms <= 0:
            return None
        return (self._frames_seen - 1) * 1000.0 / span_ms


def open_capture(
    camera: GridCamera,
    *,
    prefer: str = "rtsp",
    probe_seconds: float = 5.0,
    allow_hls_fallback: bool = True,
) -> ReconnectingCapture:
    """Open the best transport actually available for this camera.

    RTSP is what the guide recommends for inference. Where port 8554 is
    filtered - which is common, and true on some of the networks this runs on -
    the guide sanctions HLS instead, so falling back is following the guidance
    rather than working around it.
    """
    if prefer == "rtsp" and camera.rtsp_url:
        parsed = urllib.parse.urlparse(camera.rtsp_url)
        host, port = parsed.hostname, parsed.port or 554
        if host and _port_open(host, port, probe_seconds):
            return ReconnectingCapture(camera.rtsp_url, label=f"rtsp {camera.described}")
        logger.warning(
            "RTSP port %s:%s is not reachable; %s",
            host, port,
            "falling back to HLS as the guide allows" if allow_hls_fallback
            else "no fallback permitted",
        )
        if not allow_hls_fallback:
            raise RuntimeError(f"RTSP unreachable for camera {camera.id} and HLS fallback is off")

    if not camera.hls_url:
        raise RuntimeError(f"camera {camera.id} advertises no usable transport")
    return ReconnectingCapture(
        _with_cookie_check(camera.hls_url), label=f"hls {camera.described}"
    )
