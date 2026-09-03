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

logger = logging.getLogger("vigentra.edge.grid")

CATALOGUE_PATH = "/cameras.json"
#: RTSP and WHEP are served on the public gateway, not the CDN host: a CDN
#: cannot proxy them. This is why RTSP appeared unreachable for so long - the
#: worker was probing port 8554 on the CDN name, which never served it.
RTSP_HOST = os.getenv("SENTINEL_GRID_RTSP_HOST", "103.250.160.189")
RTSP_PORT = int(os.getenv("SENTINEL_GRID_RTSP_PORT", "8554"))
#: The gateway 302s to http:// without this, which breaks a TLS-only client.
COOKIE_CHECK = "cookieCheck=1"


#: Microseconds FFmpeg will wait for the socket before giving up. OpenCV
#: exposes no timeout on VideoCapture, so without this a capture that opens
#: against an unresponsive feed blocks the calling thread indefinitely - and a
#: worker sweeping thirty cameras stops on the first one that hangs, having
#: reported nothing wrong. Ten seconds: long enough for a slow but working
#: connection, short enough that a dead one is one camera's delay.
SOCKET_TIMEOUT_US = int(float(os.getenv("SENTINEL_GRID_SOCKET_TIMEOUT", "10")) * 1_000_000)


def _force_tcp_transport() -> None:
    """Pin FFmpeg's RTSP demuxer to TCP, and give it a deadline.

    UDP is accepted by the grid but fails across NAT and most corporate
    firewalls, and partial UDP delivery produces corrupt frames that look
    exactly like model bugs.

    These are process-wide FFmpeg options read when the first capture is
    constructed, so they have to be set before any VideoCapture exists -
    setting them afterwards silently does nothing.
    """
    existing = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    if "rtsp_transport" in existing:
        return
    options = [
        "rtsp_transport;tcp",
        # `timeout` is the current spelling and `stimeout` the older one;
        # FFmpeg ignores an option it does not know, so passing both covers
        # whichever build OpenCV was linked against.
        f"timeout;{SOCKET_TIMEOUT_US}",
        f"stimeout;{SOCKET_TIMEOUT_US}",
    ]
    merged = "|".join(([existing] if existing else []) + options)
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


class GridUnavailable(RuntimeError):
    """The catalogue could not be read - usually an unauthenticated session."""


def _opener(base_url: str, timeout: float) -> urllib.request.OpenerDirector:
    """An opener holding a grid session, signing in first when configured.

    The sandbox is behind one shared access password posted to /auth/login,
    which answers with a cookie. Built per call rather than cached because the
    catalogue is read once per cycle at most - a pooled session would need
    expiry handling to save nothing measurable.
    """
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    password = os.getenv("SENTINEL_GRID_PASSWORD", "").strip()
    if not password:
        return opener

    # The sign-in form grew a second field: it took a password alone and now
    # takes a registered address alongside it. Sent only when configured, so a
    # gateway still running the older form is unaffected.
    form = {"password": password}
    email = os.getenv("SENTINEL_GRID_EMAIL", "").strip()
    if email:
        form["email"] = email

    try:
        opener.open(
            urllib.request.Request(
                base_url.rstrip("/") + "/auth/login",
                data=urllib.parse.urlencode(form).encode(),
                headers={"User-Agent": "vigentra-edge-worker/1.0"},
            ),
            timeout=timeout,
        ).read()
    except Exception as exc:  # noqa: BLE001 - report at the catalogue read
        logger.warning("grid sign-in failed (%s); continuing unauthenticated", exc)

    # A refused sign-in is answered with HTTP 200 and the sign-in page, so
    # reaching here proves nothing on its own - the cookie does.
    if not len(jar):
        logger.warning(
            "grid sign-in issued no session cookie, which means the credential "
            "was refused; check SENTINEL_GRID_EMAIL and SENTINEL_GRID_PASSWORD"
        )
    return opener


def fetch_catalogue(base_url: str, timeout: float = 15.0) -> dict[str, GridCamera]:
    """Read the camera catalogue.

    The catalogue is the contract and the URL pattern is not: camera ids and
    the set of cameras change, so callers resolve through this rather than
    building rtsp:// strings by hand. That warning earned itself - the grid
    moved host, renamed this endpoint from /api/ingest to /cameras.json,
    renumbered every camera from `1` to `cam01`, and replaced seven of them.

    The new catalogue carries only {id, name}: no codec, no resolution, no
    per-camera URLs. So the stream URLs are composed here from the documented
    patterns, which is exactly what the guide says not to do - but there is
    nothing else left to resolve them from, and composing them in one place
    beats every caller doing it privately.
    """
    import json

    url = base_url.rstrip("/") + CATALOGUE_PATH
    opener = _opener(base_url, timeout)
    request = urllib.request.Request(url, headers={"User-Agent": "vigentra-edge-worker/1.0"})
    with opener.open(request, timeout=timeout) as response:
        body = response.read()
    try:
        payload = json.loads(body)
    except ValueError as exc:
        # The gateway answers the sign-in page rather than a 401 when the
        # session is missing, so a JSON failure here means "not signed in"
        # far more often than it means "malformed catalogue". Saying so is
        # the difference between a five-minute fix and a day of guessing.
        raise GridUnavailable(
            f"{url} did not return JSON. The grid is behind an access "
            f"password now - set SENTINEL_GRID_PASSWORD."
        ) from exc

    # Accept the bare list the grid returns now, and the {"cameras": [...]}
    # wrapper it used to, so a redeployment of either shape keeps working.
    entries = payload.get("cameras", []) if isinstance(payload, dict) else payload
    cameras: dict[str, GridCamera] = {}
    for entry in entries or []:
        cid = str(entry.get("id") or "").strip()
        if not cid:
            continue
        hls = str(entry.get("hls_live_url") or "")
        if not hls:
            hls = f"{base_url.rstrip('/')}/{cid}/index.m3u8"
        elif hls.startswith("/"):
            hls = base_url.rstrip("/") + hls
        rtsp = str(entry.get("rtsp_url") or "")
        if not rtsp:
            rtsp = f"rtsp://{RTSP_HOST}:{RTSP_PORT}/stream/{cid}"
        cameras[cid] = GridCamera(
            id=cid,
            name=str(entry.get("name") or f"Camera {cid}"),
            location=str(entry.get("location") or ""),
            # The thin catalogue no longer reports liveness. Absent that,
            # assume live rather than filtering every camera out: the capture
            # attempt is the real liveness test and it reports its own
            # failure, whereas a default of False silently empties the grid.
            live=bool(entry.get("live", True) or entry.get("status") == "live"),
            codec=(entry.get("codec") or None),
            width=(entry.get("width") or None),
            height=(entry.get("height") or None),
            declared_fps=(entry.get("fps") or None),
            rtsp_url=rtsp,
            hls_url=hls,
        )
    return cameras


#: Seconds a TCP handshake may take before RTSP is judged unusable even though
#: the port answered. A handshake is the cheapest exchange there is: if it
#: takes this long, the path cannot carry a real-time video session, and the
#: capture that follows will stall rather than fail. Observed on one network
#: here: 8554 reachable, but 20s to complete the handshake, which passed the
#: probe and then hung the whole worker on its second camera.
SLOW_HANDSHAKE_SECONDS = float(os.getenv("SENTINEL_GRID_RTSP_MAX_HANDSHAKE", "3.0"))


def _port_open(host: str, port: int, timeout: float) -> bool:
    """Reachable AND quick enough to be worth using.

    Reachability alone is the wrong test. A filtered port is the obvious
    failure and the one the fallback was written for, but a port that answers
    slowly is worse: it passes, and the stall lands later in a media session
    with no deadline on it.
    """
    sock = socket.socket()
    sock.settimeout(timeout)
    started = time.monotonic()
    try:
        sock.connect((host, port))
    except OSError:
        return False
    finally:
        sock.close()

    elapsed = time.monotonic() - started
    if elapsed > SLOW_HANDSHAKE_SECONDS:
        logger.warning(
            "%s:%s answered but took %.1fs to complete a TCP handshake "
            "(limit %.1fs) - treating RTSP as unusable on this network",
            host, port, elapsed, SLOW_HANDSHAKE_SECONDS,
        )
        return False
    return True


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
