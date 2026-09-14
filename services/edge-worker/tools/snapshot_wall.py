"""A live wall that works on this network, in two grids.

The grid's HLS CDN delivers a 6-second segment in 15-80 seconds and 403s under
concurrency, so an in-browser HLS wall goes black on every tile - the network
cannot feed a player, and several cameras are HEVC which hls.js cannot decode
in MPEG-TS at all. RTSP on the public gateway is real-time and every codec
decodes there. This service decodes over RTSP with a bounded, persistent pool
(the box holds about six concurrent decoders) and offers two grids:

* Coverage wall (`/`, all cameras): the pool rotates through the whole estate,
  so every camera is refreshed within one rotation and shows its most recent
  frame in between - never a black tile. Served as periodically reloaded JPEGs.

* Motion grid (`/motion`, the few cameras the box can decode at once): the pool
  is pinned to those cameras and never rotates, so each one streams continuous
  frames (MJPEG) - actual live motion, not a still that updates now and then.
  The size is chosen by the code from a short capacity probe at start.

Consume-only per the guide: RTSP forced over TCP with a read timeout so a
stalled feed frees its slot instead of hanging it, at most `pool` streams open
at once, nothing written to the grid, credentials from .env never reaching the
browser or a log.

    python tools/snapshot_wall.py --pool 6 --port 9100
"""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

import cv2

# OpenCV's FFmpeg backend defaults to a 30 s open/read interrupt, which lets one
# slow feed hang a decoder slot for half a minute. These pin it to a few seconds
# so a stalled camera frees its slot fast. Passed per capture, below.
OPEN_TIMEOUT_MS = 25000
READ_TIMEOUT_MS = 5000

# A slot that decodes nothing - a dead feed, or the gateway refusing the
# account - waits before opening again, doubling up to five minutes. Retrying
# at once sent ~60 refused RTSP logins a minute while the account was blocked,
# which is how a block stays in place.
FAIL_BACKOFF_MIN_S = 5.0
FAIL_BACKOFF_MAX_S = 300.0

#: A camera is decoded while it has been asked for within this window. The
#: dashboard tile re-requests every ~2.5s while on screen, so a viewer keeps
#: it live; a few seconds after they scroll away the slot drops it.
DEMAND_TTL = 20.0


def _open(url: str):
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS,
                             cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS])


HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent

#: Force TCP and, crucially, a network read timeout: without it cv2.read()
#: blocks for ever on a stalled RTSP feed and freezes the slot that camera is
#: on - which is exactly what made the first wall "stick". Set before any
#: VideoCapture is constructed; FFmpeg reads it once, at construction.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|timeout;6000000|stimeout;6000000",
)


#: Box colours (BGR) by the detector's canonical class.
BOX_COLOURS = {"car": (240, 180, 80), "truck": (60, 140, 250), "bus": (60, 200, 250),
               "motorcycle": (210, 120, 250), "bicycle": (160, 210, 110), "person": (110, 220, 120)}


def draw_detections(image, detections) -> None:
    """Each detection as a box and a 'class 0.87' tag, in place."""
    for det in detections:
        x1, y1, x2, y2 = (int(v) for v in det.bbox_xyxy)
        colour = BOX_COLOURS.get(det.class_name, (230, 230, 230))
        cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
        label = f"{det.class_name} {det.confidence:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        top = max(th + 4, y1)
        cv2.rectangle(image, (x1, top - th - 4), (x1 + tw + 6, top), colour, -1)
        cv2.putText(image, label, (x1 + 3, top - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (15, 15, 15), 1, cv2.LINE_AA)


def _via(vms_url: str, media_url: str) -> str:
    """The VMS names its media by the address it knows itself by (inside the
    compose network); fetch it the way the VMS itself was reached."""
    from urllib.parse import urlsplit

    return urlsplit(media_url)._replace(netloc=urlsplit(vms_url).netloc).geturl()


#: Settled readings, as the engine labels them: (background, text) in BGR.
READ_COLOURS = {"CONFIRMED": ((71, 127, 26), (255, 255, 255)), "CANDIDATE": ((48, 168, 240), (20, 20, 20))}
READ_HOLD_S = 4.0


def draw_anpr(image, vehicles, plates, labels, last_box) -> None:
    """The ANPR engine's view of a frame: vehicle tracks, plate boxes, and each
    track's reading for a few seconds after the engine settles it."""
    for tid, b, cls in vehicles:
        x1, y1, x2, y2 = (int(v) for v in b)
        cv2.rectangle(image, (x1, y1), (x2, y2), (224, 160, 111), 3)
        cv2.putText(image, f"#{tid} {cls}", (x1 + 3, max(22, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (224, 160, 111), 2, cv2.LINE_AA)
    for b in plates:
        x1, y1, x2, y2 = (int(v) for v in b)
        cv2.rectangle(image, (x1, y1), (x2, y2), (159, 220, 125), 3)
    now = time.time()
    h, w = image.shape[:2]
    for tid, (rec, at) in list(labels.items()):
        if now - at > READ_HOLD_S:
            labels.pop(tid, None)
            continue
        b = last_box.get(tid) or rec.get("bbox")
        if not b:
            continue
        bg, fg = READ_COLOURS[rec["status"]]
        text = f"{rec['plate']}  {float(rec.get('confidence') or 0):.2f}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, 1.1, 2)
        x = min(max(0, int(b[0])), w - tw - 20)
        y = min(int(b[3]) + 12, h - th - 20)
        cv2.rectangle(image, (x, y), (x + tw + 18, y + th + 18), bg, -1)
        cv2.putText(image, text, (x + 9, y + th + 8), cv2.FONT_HERSHEY_DUPLEX, 1.1, fg, 2, cv2.LINE_AA)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class Estate:
    """Latest JPEG per camera, kept fresh by ONE bounded pool of decoders.

    The pool has two modes, because 30 cameras and a box that decodes about six
    at once cannot do both jobs at full quality at the same time:

    * coverage: every slot rotates through its share of the whole estate,
      holding each camera a few seconds, so all thirty are refreshed on a short
      cycle and none goes stale for long - the all-cameras wall.
    * focus: the slots pin to the handful of `focus` cameras and decode them
      without interruption, so each streams continuous frames - the motion grid.

    The page you open sets the mode, so the same six decoders serve whichever
    wall is on screen. Total open streams never exceed `pool`.
    """

    def __init__(self, cameras: list[str], pool: int, focus: list[str], width: int,
                 hold_s: float, max_fps: float, urls: dict[str, str] | None = None):
        self.cameras = cameras
        #: cam -> the stream URL the CATALOGUE gave us. The guide is explicit:
        #: "start from the catalogue rather than hard-coding - the camera set
        #: can change". Composing the URL pattern here would survive exactly
        #: until the gateway renames it, as it already has once.
        self.urls = dict(urls or {})
        self.focus = focus
        self.width = width
        self.hold_s = hold_s
        self.min_dt = 1.0 / max_fps if max_fps > 0 else 0.0
        #: cam -> latest decoded frame (already at wall width) and its sequence,
        #: rendered to JPEG only when asked for: detection runs per shown frame,
        #: not per decoded one.
        self.frames: dict[str, object] = {}
        self.seq: dict[str, int] = {}
        self.rendered: dict[str, tuple[int, bytes]] = {}
        self.updated: dict[str, float] = {}
        #: optional object detector (--detect); one model, so one call at a time
        self.detector = None
        self.detect_lock = threading.Lock()
        #: cameras the ANPR engine draws on itself (--anpr): the detector skips them
        #: and the rotating pool leaves them to their own continuous decoder
        self.predrawn: set[str] = set()
        #: cam -> when it was last asked for. A camera is decoded only while it
        #: is being watched (the guide's "open only the cameras you are
        #: processing"); when nobody is watching, no stream is open at all.
        self.demand: dict[str, float] = {}
        self.lock = threading.Lock()
        self.pool = max(1, min(pool, len(cameras)))
        self.mode = "coverage"
        self.generation = 0

    def set_mode(self, mode: str) -> None:
        if mode in ("coverage", "focus") and mode != self.mode:
            self.mode = mode
            self.generation += 1  # signals every slot to drop and re-pick

    def start(self) -> None:
        for i in range(self.pool):
            threading.Thread(target=self._slot, args=(i,), daemon=True).start()

    def _url(self, cam: str) -> str:
        url = self.urls.get(cam)
        if url:
            return url
        # Only when the catalogue itself was unreachable AND had no entry for
        # this camera: grid.fallback_catalogue composes the documented pattern
        # in the one place that is allowed to.
        from app import grid

        return grid.fallback_catalogue(
            os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud"), (cam,)
        )[cam].rtsp_url

    def note_demand(self, cam: str) -> None:
        """Record that `cam` was just asked for, so a slot will decode it."""
        self.demand[cam] = time.monotonic()

    def is_demanded(self, cam: str) -> bool:
        return time.monotonic() - self.demand.get(cam, 0.0) < DEMAND_TTL

    def _active_targets(self) -> list[str]:
        """The cameras currently being watched, in this mode's order."""
        pool_cams = self.focus if self.mode == "focus" else self.cameras
        return [c for c in pool_cams if self.is_demanded(c) and c not in self.predrawn]

    def _slot(self, index: int) -> None:
        rot = index
        backoff = 0.0
        while True:
            gen = self.generation
            targets = self._active_targets()
            # Nobody watching this slot's share: no capture is opened at all.
            if not targets or index >= len(targets):
                for _ in range(6):  # ~1.2s, then re-check demand and generation
                    if self.generation != gen:
                        break
                    time.sleep(0.2)
                rot = index
                continue
            cam = targets[rot % len(targets)]
            rot += self.pool
            # Few enough to pin one per slot: hold it continuously. More than
            # the pool: rotate on the hold interval so all get refreshed.
            hold = 1e12 if len(targets) <= self.pool else self.hold_s
            if self._pump(cam, hold_s=hold, gen=gen):
                backoff = 0.0
                continue
            backoff = min(max(backoff * 2, FAIL_BACKOFF_MIN_S), FAIL_BACKOFF_MAX_S)
            deadline = time.time() + backoff
            while time.time() < deadline and self.generation == gen:
                time.sleep(0.5)

    def _pump(self, cam: str, hold_s: float, gen: int) -> bool:
        """Decode `cam` until hold_s elapses or the mode changes, publishing
        every frame. A read timeout means a dead feed returns quickly rather
        than freezing the slot. Returns whether any frame was decoded."""
        cap = _open(self._url(cam))
        got_any = False
        try:
            deadline = time.time() + hold_s
            got_at = time.time()
            last_pub = 0.0
            while time.time() < deadline and self.generation == gen and self.is_demanded(cam):
                ok, frame = cap.read()
                now = time.time()
                if not ok:
                    if now - got_at > 6.0:
                        return got_any  # dead feed: free the slot
                    time.sleep(0.05)  # a refused open fails instantly; do not spin
                    continue
                got_at = now
                got_any = True
                if self.min_dt and now - last_pub < self.min_dt:
                    continue
                last_pub = now
                self._publish(cam, frame, now)
            return got_any
        finally:
            cap.release()

    def _publish(self, cam: str, frame, now: float, width: int | None = None) -> None:
        h, w = frame.shape[:2]
        width = width or self.width
        if w > width:
            frame = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        with self.lock:
            self.frames[cam] = frame
            self.seq[cam] = self.seq.get(cam, 0) + 1
            self.updated[cam] = now

    def snapshot(self, cam: str) -> bytes | None:
        """The latest frame as JPEG, at once.

        With the detector on, a background worker draws the boxes and this
        returns the newest frame it has finished - a request never waits on
        detection, so fifty polling tiles cost the API only a copy. The first
        request for a camera gets its frame unboxed rather than nothing.
        """
        self.note_demand(cam)
        with self.lock:
            frame, seq = self.frames.get(cam), self.seq.get(cam, 0)
            done = self.rendered.get(cam)
        if done and (done[0] == seq or (self.detector is not None and cam not in self.predrawn)):
            return done[1]
        if frame is None:
            return None
        return self._render(cam, frame, seq, detect=False)

    def _render(self, cam: str, frame, seq: int, detect: bool) -> bytes | None:
        image = frame.copy()
        if detect:
            with self.detect_lock:
                found = self.detector.detect(image)
            draw_detections(image, found)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 74])
        if not ok:
            return None
        data = buf.tobytes()
        with self.lock:
            if seq >= self.rendered.get(cam, (-1, b""))[0]:
                self.rendered[cam] = (seq, data)
        return data

    def annotate_forever(self) -> None:
        """Box each watched camera's newest frame, round-robin, as fast as the detector goes."""
        while True:
            with self.lock:
                due = [c for c, s in self.seq.items() if c not in self.predrawn and self.is_demanded(c)
                       and self.rendered.get(c, (-1, b""))[0] < s]
            if not due:
                time.sleep(0.05)
                continue
            for cam in due:
                with self.lock:
                    frame, seq = self.frames.get(cam), self.seq.get(cam, 0)
                if frame is not None:
                    self._render(cam, frame, seq, detect=True)

    # -- live ANPR ------------------------------------------------------------
    def add_anpr(self, cams: list[str], vms_url: str | None, api_key: str) -> None:
        """Run the full ANPR engine live on these cameras, drawing what it sees.

        Plates need every frame at full resolution and a tracker that sees them
        in order, so each gets its own continuous decoder instead of a rotating
        pool slot, and its frames are published already marked up: vehicle
        tracks, plate boxes and each reading as the engine settles it.
        """
        from app.anpr_engine import MIN_GRAMMAR_PRIOR, AnprEngine

        # One engine runs at a time: on a Mac the engines share the GPU, and
        # PyTorch's Metal backend does not survive calls from several threads.
        self.engine_lock = threading.Lock()
        for cam in cams:
            self.predrawn.add(cam)
            threading.Thread(target=self._anpr_feed, args=(cam, vms_url, api_key, AnprEngine, MIN_GRAMMAR_PRIOR),
                             daemon=True).start()

    def _open_feed(self, cam: str, vms_url: str | None, api_key: str):
        """(capture, fps) - fps only for a recording, which is paced; a live stream sets its own pace."""
        if not vms_url or cam in self.cameras:
            return _open(self._url(cam)), None
        import json
        import urllib.request

        req = urllib.request.Request(f"{vms_url.rstrip('/')}/traffic/cameras/{cam}/live", headers={"X-API-Key": api_key})
        cap = cv2.VideoCapture(_via(vms_url, json.load(urllib.request.urlopen(req, timeout=10))["playback_url"]),
                               cv2.CAP_FFMPEG)
        return cap, cap.get(cv2.CAP_PROP_FPS) or 25.0

    def _anpr_feed(self, cam: str, vms_url: str | None, api_key: str, engine_cls, min_prior: float) -> None:
        engine = engine_cls(camera_id=cam)
        pipe = engine._pipeline
        seen, labels, last_box = 0, {}, {}
        while True:
            if not self.is_demanded(cam):
                time.sleep(1.0)
                continue
            try:
                cap, fps = self._open_feed(cam, vms_url, api_key)
            except Exception:  # noqa: BLE001 - the source refused; ask again shortly
                time.sleep(10)
                continue
            start, n, taken = time.time(), 0, 0
            newest = {"frame": None, "n": 0, "alive": True}
            grabber = None
            if fps is None:  # live: a reader keeps only the newest frame, so the stream never backs up
                def keep_newest(cap=cap, newest=newest) -> None:
                    try:
                        while newest["alive"]:
                            ok, fr = cap.read()
                            if not ok:
                                break
                            newest["frame"], newest["n"] = fr, newest["n"] + 1
                    finally:
                        newest["alive"] = False
                        cap.release()  # only this thread reads the capture, so only it may free it

                grabber = threading.Thread(target=keep_newest, daemon=True)
                grabber.start()
            try:
                while self.is_demanded(cam):
                    if grabber is not None:
                        if not newest["alive"]:
                            break  # the stream dropped: open it again
                        if newest["n"] == taken:
                            time.sleep(0.02)
                            continue
                        taken, frame = newest["n"], newest["frame"]
                    else:
                        ok, frame = cap.read()
                        if not ok:
                            break  # the recording ended: take a fresh ticket and play it again
                        n += 1
                        due = start + n / fps  # camera pace; frames the engine is too slow for are dropped
                        if due < time.time() - 0.2:
                            continue
                        if due > time.time():
                            time.sleep(due - time.time())
                    began = time.time()
                    with self.engine_lock:
                        engine.process(frame, captured_at=began)
                    vehicles = [(str(t).split("_")[-1], b, c) for t, b, c, _ in pipe.last_vehicles]
                    for tid, b, _ in vehicles:
                        last_box[tid] = b
                    records = pipe.records
                    for rec in (records[seen:] if seen <= len(records) else records[-5:]):
                        prior = rec.get("grammar_prior")
                        sure = rec.get("status") == "CONFIRMED" or float(rec.get("confidence") or 0) >= 0.10
                        if rec.get("status") in READ_COLOURS and rec.get("plate") and sure and (prior is None or prior >= min_prior):
                            labels[str(rec.get("track_id")).split("_")[-1]] = (rec, began)
                    seen = len(records)
                    draw_anpr(frame, vehicles, [b for _, b, _, _ in pipe.last_plates], labels, last_box)
                    self._publish(cam, frame, time.time(), width=1280)
            finally:
                newest["alive"] = False
                if grabber is not None:
                    # Freeing a capture while a read is still in it segfaults inside
                    # FFmpeg, so wait for the reader: it releases on its way out,
                    # bounded by the stream's read timeout.
                    grabber.join()
                else:
                    cap.release()

    # -- department VMS feeds ---------------------------------------------
    def add_vms(self, vms_url: str, api_key: str) -> None:
        """Serve the Traffic Police VMS's cameras beside the grid's.

        Each is opened through the VMS's own authorised live handle (a ticketed
        URL, as a real VMS hands out), decoded at the pace a camera delivers
        and played again when a recording ends. Retries until the VMS answers.
        """
        import json
        import urllib.request

        def run() -> None:
            while True:
                try:
                    req = urllib.request.Request(vms_url.rstrip("/") + "/traffic/approved-cameras",
                                                 headers={"X-API-Key": api_key})
                    records = json.load(urllib.request.urlopen(req, timeout=10)).get("records", [])
                    break
                except Exception as exc:  # noqa: BLE001 - the VMS may still be starting
                    print(f"VMS not answering ({type(exc).__name__}); retrying in 10 s", flush=True)
                    time.sleep(10)
            codes = [r["cam_code"] for r in records if r.get("state") in ("REGISTERED", "SYNCED")]
            for code in codes:
                threading.Thread(target=self._vms_feed, args=(vms_url, api_key, code), daemon=True).start()
            print(f"department VMS feeds: {len(codes)}", flush=True)

        threading.Thread(target=run, daemon=True).start()

    def _vms_feed(self, vms_url: str, api_key: str, code: str) -> None:
        import json
        import urllib.request

        while True:
            if code in self.predrawn:
                return  # the ANPR engine reads this feed itself
            if not self.is_demanded(code):
                time.sleep(1.0)
                continue
            try:
                req = urllib.request.Request(f"{vms_url.rstrip('/')}/traffic/cameras/{code}/live",
                                             headers={"X-API-Key": api_key})
                url = _via(vms_url, json.load(urllib.request.urlopen(req, timeout=10))["playback_url"])
            except Exception:  # noqa: BLE001 - the VMS refused or is down; ask again shortly
                time.sleep(10)
                continue
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            start, n, last_pub = time.time(), 0, 0.0
            try:
                while self.is_demanded(code):
                    ok, frame = cap.read()
                    if not ok:
                        break  # the recording ended: take a fresh ticket and play it again
                    n += 1
                    lag = start + n / fps - time.time()
                    if lag > 0:
                        time.sleep(lag)
                    now = time.time()
                    if now - last_pub >= self.min_dt:
                        last_pub = now
                        self._publish(code, frame, now)
            finally:
                cap.release()

    def ages(self) -> dict[str, float]:
        now = time.time()
        with self.lock:
            return {c: round(now - t, 1) for c, t in self.updated.items()}


def probe_capacity(cameras: list[str], candidates=(6, 4), secs: float = 6.0,
                   target_fps: float = 6.0, urls: dict[str, str] | None = None) -> int:
    """The largest camera count the box decodes concurrently at a healthy rate.

    Opens that many known feeds at once, reads for a few seconds and measures
    the median per-camera frame rate; the biggest candidate whose median clears
    target_fps wins. This is what sizes the motion grid, rather than a guess.
    """
    from app import grid

    def one(cam: str, out: dict) -> None:
        url = (urls or {}).get(cam) or grid.fallback_catalogue(
            os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud"), (cam,)
        )[cam].rtsp_url
        cap = _open(url)
        n = 0
        t0 = time.time()
        first = None
        while time.time() - t0 < secs:
            ok, _ = cap.read()
            if ok:
                n += 1
                first = first or time.time()
        cap.release()
        out[cam] = n / max(0.1, time.time() - (first or t0))

    for k in candidates:
        pick = cameras[:k]
        out: dict[str, float] = {}
        threads = [threading.Thread(target=one, args=(c, out), daemon=True) for c in pick]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        vals = sorted(out.values())
        median = vals[len(vals) // 2] if vals else 0.0
        alive = sum(1 for v in vals if v >= 1.0)
        print(f"  probe k={k}: median {median:.1f} fps, {alive}/{k} alive")
        if alive >= k - 1 and median >= target_fps:
            return k
    return min(candidates)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
COVERAGE_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Vigentra live wall</title>
<style>html,body{margin:0;background:#0b0d12;height:100%}
#g{display:grid;gap:2px;padding:2px;height:100vh;box-sizing:border-box}
.t{position:relative;background:#000;overflow:hidden;min-height:0}
.t img{width:100%;height:100%;object-fit:cover;display:block}
.t .l{position:absolute;left:0;bottom:0;right:0;background:#000a;color:#e8eaed;font:600 11px system-ui;padding:2px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hd{position:fixed;right:6px;top:6px;z-index:9;background:#000b;color:#cbd2e0;font:12px system-ui;padding:3px 8px;border-radius:4px}
a{color:#7fb0ff}</style></head><body>
<div class=hd><a href="/motion">motion grid &rarr;</a> · <span id=s>live wall</span></div>
<div id=g></div>
<script>
const cams=__CAMS__;
const cols=Math.max(1,Math.round(Math.sqrt(cams.length*(window.innerWidth/window.innerHeight)/(16/9))))||6;
const g=document.getElementById('g'); g.style.gridTemplateColumns=`repeat(${cols},1fr)`;
const t={};
for(const c of cams){const d=document.createElement('div');d.className='t';d.innerHTML=`<img><div class=l>${c.name||c.id}</div>`;g.appendChild(d);t[c.id]=d.querySelector('img');}
function tick(){const now=Date.now();for(const c of cams)t[c.id].src=`/snap/${c.id}.jpg?t=${now}`;}
async function stat(){try{const s=await(await fetch('/status')).json();let n=0;for(const c of cams)if(s[c.id]!=null&&s[c.id]<20)n++;document.getElementById('s').textContent=`${n}/${cams.length} live now`;}catch(e){}}
fetch('/mode?set=coverage').catch(()=>{});
tick();setInterval(tick,1000);setInterval(stat,2000);stat();
</script></body></html>"""

MOTION_PAGE = """<!doctype html><html><head><meta charset=utf-8><title>Vigentra motion grid</title>
<style>html,body{margin:0;background:#0b0d12;height:100%}
#g{display:grid;gap:3px;padding:3px;height:100vh;box-sizing:border-box}
.t{position:relative;background:#000;overflow:hidden;min-height:0}
.t img{width:100%;height:100%;object-fit:contain;display:block}
.t .l{position:absolute;left:0;bottom:0;right:0;background:#000a;color:#e8eaed;font:600 12px system-ui;padding:3px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hd{position:fixed;right:6px;top:6px;z-index:9;background:#000b;color:#cbd2e0;font:12px system-ui;padding:3px 8px;border-radius:4px}
a{color:#7fb0ff}</style></head><body>
<div class=hd><a href="/">&larr; all cameras</a> · __N__ cameras, live motion (chosen for this machine)</div>
<div id=g></div>
<script>
const cams=__CAMS__;
const cols=Math.max(1,Math.round(Math.sqrt(cams.length*(window.innerWidth/window.innerHeight)/(16/9))))||3;
const g=document.getElementById('g'); g.style.gridTemplateColumns=`repeat(${cols},1fr)`;
fetch('/mode?set=focus').then(()=>{for(const c of cams){const d=document.createElement('div');d.className='t';d.innerHTML=`<img src="/mjpeg/${c.id}"><div class=l>${c.name||c.id}</div>`;g.appendChild(d);}});
</script></body></html>"""


def build_handler(estate: Estate, motion: list[str], catalogue: dict[str, str]):
    def cams_json(ids: list[str]) -> str:
        return "[" + ",".join(
            '{"id":"%s","name":"%s"}' % (c, catalogue.get(c, c).replace('"', "'")[:40]) for c in ids
        ) + "]"

    coverage = COVERAGE_PAGE.replace("__CAMS__", cams_json(estate.cameras)).encode()
    motion_page = (MOTION_PAGE.replace("__CAMS__", cams_json(motion))
                   .replace("__N__", str(len(motion))).encode())

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, ct, body, cache=False):
            self.send_response(code)
            self.send_header("content-type", ct)
            self.send_header("content-length", str(len(body)))
            if not cache:
                self.send_header("cache-control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def do_GET(self):
            import urllib.parse

            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", ""):
                self._send(200, "text/html", coverage)
            elif path == "/motion":
                self._send(200, "text/html", motion_page)
            elif path == "/mode":
                import urllib.parse as _up

                q = _up.parse_qs(_up.urlsplit(self.path).query)
                if "set" in q:
                    estate.set_mode(q["set"][0])
                self._send(200, "application/json", ('{"mode":"%s"}' % estate.mode).encode())
            elif path == "/status":
                import json

                self._send(200, "application/json", json.dumps(estate.ages()).encode())
            elif path.startswith("/snap/"):
                data = estate.snapshot(path[len("/snap/"):].split(".")[0])
                if data is None:
                    self._send(503, "text/plain", b"warming up")
                else:
                    self._send(200, "image/jpeg", data)
            elif path.startswith("/mjpeg/"):
                self._mjpeg(path[len("/mjpeg/"):].split(".")[0])
            else:
                self._send(404, "text/plain", b"not found")

        def _mjpeg(self, cam: str):
            self.send_response(200)
            self.send_header("content-type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("cache-control", "no-store")
            self.end_headers()
            last = None
            try:
                while True:
                    data = estate.snapshot(cam)
                    if data is not None and data is not last:
                        last = data
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(data)}\r\n\r\n".encode())
                        self.wfile.write(data)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.06)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

    return H


def _catalogue() -> dict[str, str]:
    out: dict[str, str] = {}
    ref = WORKER_ROOT.parent.parent / "data" / "reference" / "grid_cameras.json"
    if ref.exists():
        import json

        try:
            data = json.loads(ref.read_text(encoding="utf-8"))
            rows = data.values() if isinstance(data, dict) else data
            for r in rows:
                no = r.get("camera_no") or r.get("id") or r.get("camera_id")
                if no is None:
                    continue
                digits = "".join(ch for ch in str(no) if ch.isdigit())
                cid = f"cam{int(digits):02d}" if digits else str(no)
                out[cid] = str(r.get("site_name") or r.get("name") or r.get("location") or cid)
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", type=int, default=6, help="concurrent RTSP decoders (coverage rotation width)")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address; 0.0.0.0 in a container, so central-api can reach it over the compose network")
    ap.add_argument("--width", type=int, default=512, help="coverage JPEG width")
    ap.add_argument("--motion-width", type=int, default=640, help="motion JPEG width")
    ap.add_argument("--hold", type=float, default=6.0, help="seconds a coverage slot holds a camera before rotating")
    ap.add_argument("--motion-size", default="auto", help="cameras in the motion grid, or 'auto' to probe")
    ap.add_argument("--max-fps", type=float, default=12.0, help="cap published frames per second per camera")
    ap.add_argument("--cameras", nargs="*", default=None)
    ap.add_argument("--detect", action="store_true",
                    help="draw the object detector's boxes on every frame a tile is shown")
    ap.add_argument("--vms-url", default=None,
                    help="also serve this Traffic VMS's cameras (API key from TRAFFIC_VMS_API_KEY)")
    ap.add_argument("--anpr", nargs="*", default=[],
                    help="cameras to run the full ANPR engine on live (grid ids or VMS codes); needs the ANPR models")
    args = ap.parse_args()

    sys.path.insert(0, str(WORKER_ROOT))
    load_env(WORKER_ROOT.parent.parent / ".env")
    if not os.getenv("SENTINEL_GRID_PASSWORD"):
        raise SystemExit("SENTINEL_GRID_PASSWORD is not set (repo .env)")

    from app import grid

    base = os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud")
    grid_cams, source = grid.catalogue_or_fallback(base)
    print(f"catalogue: {len(grid_cams)} camera(s) (source: {source})")
    # The catalogue is the contract: its ids AND its stream URLs.
    urls = {cid: cam.rtsp_url for cid, cam in grid_cams.items() if cam.rtsp_url}
    cams = args.cameras or list(grid_cams)
    catalogue = _catalogue()

    # Motion grid: the few cameras the box decodes at once, chosen by probe.
    # Prefer cameras known to deliver quickly so the grid is lively.
    preferred = [c for c in ("cam07", "cam10", "cam02", "cam05", "cam16", "cam13",
                             "cam14", "cam09", "cam11", "cam04") if c in cams]
    ordered = preferred + [c for c in cams if c not in preferred]
    # The motion grid uses every slot at once (mode switch frees them from
    # coverage), so its size is the pool - but never more than the box can
    # actually sustain, which the probe measures.
    if args.motion_size == "auto":
        print("probing decode capacity for the motion grid...")
        n = probe_capacity(ordered, candidates=(args.pool, max(2, args.pool - 2)), urls=urls)
    else:
        n = max(1, int(args.motion_size))
    motion = ordered[:min(n, args.pool, len(ordered))]
    print(f"motion grid size: {len(motion)} ({', '.join(motion)})")

    estate = Estate(cams, pool=args.pool, focus=motion, width=args.width,
                    hold_s=args.hold, max_fps=args.max_fps, urls=urls)
    estate.start()
    if args.detect:
        from app.detectors import UltralyticsYoloDetector

        # On the CPU, so the tile boxes never queue behind (or collide with) the
        # ANPR engines on the GPU.
        estate.detector = UltralyticsYoloDetector(confidence_threshold=0.35, device="cpu")
        estate.detector.load()
        threading.Thread(target=estate.annotate_forever, daemon=True).start()
        print("live detection: on (YOLO, boxes drawn in the background on every watched feed)")
    if args.vms_url:
        estate.add_vms(args.vms_url, os.getenv("TRAFFIC_VMS_API_KEY", "traffic-demo-key"))
    if args.anpr:
        estate.add_anpr(args.anpr, args.vms_url, os.getenv("TRAFFIC_VMS_API_KEY", "traffic-demo-key"))
        print(f"live ANPR on: {', '.join(args.anpr)}")
    print(f"snapshot wall: {len(cams)} cameras, pool {estate.pool}, motion grid {len(motion)}, "
          f"on http://{args.host}:{args.port}  (coverage / and motion /motion)")

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer((args.host, args.port), build_handler(estate, motion, catalogue))
    srv.daemon_threads = True
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
