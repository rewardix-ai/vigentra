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
                 hold_s: float, max_fps: float):
        self.cameras = cameras
        self.focus = focus
        self.width = width
        self.hold_s = hold_s
        self.min_dt = 1.0 / max_fps if max_fps > 0 else 0.0
        self.frames: dict[str, bytes] = {}
        self.updated: dict[str, float] = {}
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
        from app import grid

        return grid.with_credentials(f"rtsp://{grid.RTSP_HOST}:{grid.RTSP_PORT}/stream/{cam}")

    def _slot(self, index: int) -> None:
        rot = index
        while True:
            gen = self.generation
            if self.mode == "focus":
                if index < len(self.focus):
                    self._pump(self.focus[index], hold_s=1e12, gen=gen)
                else:
                    while self.generation == gen:
                        time.sleep(0.2)  # spare slot idles in focus mode
            else:
                cam = self.cameras[rot % len(self.cameras)]
                rot += self.pool
                self._pump(cam, hold_s=self.hold_s, gen=gen)

    def _pump(self, cam: str, hold_s: float, gen: int) -> None:
        """Decode `cam` until hold_s elapses or the mode changes, publishing
        every frame. A read timeout means a dead feed returns quickly rather
        than freezing the slot."""
        cap = _open(self._url(cam))
        try:
            deadline = time.time() + hold_s
            got_at = time.time()
            last_pub = 0.0
            while time.time() < deadline and self.generation == gen:
                ok, frame = cap.read()
                now = time.time()
                if not ok:
                    if now - got_at > 6.0:
                        return  # dead feed: free the slot
                    continue
                got_at = now
                if self.min_dt and now - last_pub < self.min_dt:
                    continue
                last_pub = now
                h, w = frame.shape[:2]
                if w > self.width:
                    frame = cv2.resize(frame, (self.width, int(h * self.width / w)),
                                       interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                if ok:
                    data = buf.tobytes()
                    with self.lock:
                        self.frames[cam] = data
                        self.updated[cam] = now
        finally:
            cap.release()

    def snapshot(self, cam: str) -> bytes | None:
        with self.lock:
            return self.frames.get(cam)

    def ages(self) -> dict[str, float]:
        now = time.time()
        with self.lock:
            return {c: round(now - t, 1) for c, t in self.updated.items()}


def probe_capacity(cameras: list[str], candidates=(6, 4), secs: float = 6.0,
                   target_fps: float = 6.0) -> int:
    """The largest camera count the box decodes concurrently at a healthy rate.

    Opens that many known feeds at once, reads for a few seconds and measures
    the median per-camera frame rate; the biggest candidate whose median clears
    target_fps wins. This is what sizes the motion grid, rather than a guess.
    """
    from app import grid

    def one(cam: str, out: dict) -> None:
        url = grid.with_credentials(f"rtsp://{grid.RTSP_HOST}:{grid.RTSP_PORT}/stream/{cam}")
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
    ap.add_argument("--width", type=int, default=512, help="coverage JPEG width")
    ap.add_argument("--motion-width", type=int, default=640, help="motion JPEG width")
    ap.add_argument("--hold", type=float, default=6.0, help="seconds a coverage slot holds a camera before rotating")
    ap.add_argument("--motion-size", default="auto", help="cameras in the motion grid, or 'auto' to probe")
    ap.add_argument("--max-fps", type=float, default=12.0, help="cap published frames per second per camera")
    ap.add_argument("--cameras", nargs="*", default=None)
    args = ap.parse_args()

    sys.path.insert(0, str(WORKER_ROOT))
    load_env(WORKER_ROOT.parent.parent / ".env")
    if not os.getenv("SENTINEL_GRID_PASSWORD"):
        raise SystemExit("SENTINEL_GRID_PASSWORD is not set (repo .env)")

    cams = args.cameras or [f"cam{i:02d}" for i in range(1, 31)]
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
        n = probe_capacity(ordered, candidates=(args.pool, max(2, args.pool - 2)))
    else:
        n = max(1, int(args.motion_size))
    motion = ordered[:min(n, args.pool, len(ordered))]
    print(f"motion grid size: {len(motion)} ({', '.join(motion)})")

    estate = Estate(cams, pool=args.pool, focus=motion, width=args.width,
                    hold_s=args.hold, max_fps=args.max_fps)
    estate.start()
    print(f"snapshot wall: {len(cams)} cameras, pool {estate.pool}, motion grid {len(motion)}, "
          f"on http://127.0.0.1:{args.port}  (coverage / and motion /motion)")

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", args.port), build_handler(estate, motion, catalogue))
    srv.daemon_threads = True
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
