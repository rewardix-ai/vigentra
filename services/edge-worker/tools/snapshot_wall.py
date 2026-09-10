"""A live wall that works on this network.

The grid's HLS CDN delivers a 6-second segment in 15-80 seconds and 403s under
concurrency, so no browser player keeps up: every tile goes black. RTSP on the
public gateway is real-time, but a browser cannot speak RTSP and the box holds
only about six concurrent decoders. So this service decodes over RTSP
server-side with a bounded pool that cycles the whole estate, keeps the latest
frame of every camera as a small JPEG, and serves those to the browser - which
shows all thirty at once, each refreshing as its slot comes round. Every codec
works (ffmpeg decodes HEVC too), no CDN, memory bounded.

    python tools/snapshot_wall.py --pool 6 --port 9100

Consume-only, per the guide: RTSP is forced over TCP, captures are released
between cameras so no more than --pool streams are open at once, and nothing is
written to the grid. Credentials come from the repo .env and never reach the
browser or a log line.
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent


def load_env(path: Path) -> None:
    import os

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class Estate:
    """The latest JPEG of every camera, refreshed by a pool of decoders."""

    def __init__(self, cameras: list[str], pool: int, width: int, burst_s: float):
        self.cameras = cameras
        self.width = width
        self.burst_s = burst_s
        self.frames: dict[str, bytes] = {}
        self.updated: dict[str, float] = {}
        self.lock = threading.Lock()
        self.pool = min(pool, len(cameras))

    def start(self) -> None:
        # Each worker owns a disjoint slice of the estate and cycles it, so at
        # most `pool` captures are ever open.
        slices: list[list[str]] = [[] for _ in range(self.pool)]
        for i, cam in enumerate(self.cameras):
            slices[i % self.pool].append(cam)
        for chunk in slices:
            threading.Thread(target=self._worker, args=(chunk,), daemon=True).start()

    def _worker(self, chunk: list[str]) -> None:
        from app import grid

        while True:
            for cam in chunk:
                url = grid.with_credentials(
                    f"rtsp://{grid.RTSP_HOST}:{grid.RTSP_PORT}/stream/{cam}"
                )
                self._burst(cam, url)

    def _burst(self, cam: str, url: str) -> None:
        """Open the camera, keep its latest frame fresh for burst_s, release."""
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            deadline = time.time() + self.burst_s
            got = False
            # Allow a slow first keyframe, but do not hang a slot for ever.
            hard_stop = time.time() + max(20.0, self.burst_s + 12.0)
            while time.time() < deadline or (not got and time.time() < hard_stop):
                ok, frame = cap.read()
                if not ok:
                    if time.time() > hard_stop:
                        break
                    continue
                got = True
                h, w = frame.shape[:2]
                if w > self.width:
                    frame = cv2.resize(frame, (self.width, int(h * self.width / w)),
                                       interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    with self.lock:
                        self.frames[cam] = buf.tobytes()
                        self.updated[cam] = time.time()
        finally:
            cap.release()


WALL = """<!doctype html><html><head><meta charset=utf-8><title>Vigentra live wall</title>
<style>
  html,body{margin:0;background:#0b0d12;height:100%}
  #g{display:grid;gap:2px;padding:2px;height:100vh;box-sizing:border-box}
  .t{position:relative;background:#000;overflow:hidden;min-height:0}
  .t img{width:100%;height:100%;object-fit:cover;display:block}
  .t .l{position:absolute;left:0;bottom:0;right:0;background:#000a;color:#e8eaed;
        font:600 11px system-ui;padding:2px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .t .d{position:absolute;left:5px;top:5px;width:7px;height:7px;border-radius:50%;background:#f33;box-shadow:0 0 6px #f33}
  .t.stale .d{background:#fa3;box-shadow:none}
  .hd{position:fixed;right:6px;top:6px;z-index:9;background:#000b;color:#cbd2e0;font:12px system-ui;padding:3px 8px;border-radius:4px}
</style></head><body>
<div class=hd id=hd>live wall</div>
<div id=g></div>
<script>
const cams = __CAMS__;
const cols = Math.ceil(Math.sqrt(cams.length * 16/9 * window.innerHeight/window.innerWidth)) || 6;
const g = document.getElementById('g');
g.style.gridTemplateColumns = `repeat(${Math.max(1,Math.round(Math.sqrt(cams.length* (window.innerWidth/window.innerHeight) / (16/9))))||6}, 1fr)`;
const tiles = {};
for (const c of cams){
  const d = document.createElement('div'); d.className='t';
  d.innerHTML = `<div class=d></div><img alt="${c.id}"><div class=l>${c.name||c.id}</div>`;
  g.appendChild(d); tiles[c.id] = d;
}
let live = 0;
function tick(){
  const now = Date.now();
  for (const c of cams){
    const d = tiles[c.id]; const img = d.querySelector('img');
    img.src = `/snap/${c.id}.jpg?t=${now}`;
  }
}
async function status(){
  try{
    const s = await (await fetch('/status')).json();
    let n=0;
    for (const c of cams){
      const d = tiles[c.id]; const age = s[c.id];
      if (age==null){ d.classList.add('stale'); continue; }
      if (age > 30){ d.classList.add('stale'); } else { d.classList.remove('stale'); n++; }
    }
    document.getElementById('hd').textContent = `${n}/${cams.length} refreshed in the last 30s`;
  }catch(e){}
}
tick(); setInterval(tick, 2500); setInterval(status, 3000); status();
</script></body></html>"""


def build_handler(estate: Estate, catalogue: dict[str, str]):
    cams_json = "[" + ",".join(
        '{"id":"%s","name":"%s"}' % (c, catalogue.get(c, c).replace('"', "'")[:40])
        for c in estate.cameras
    ) + "]"

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            if u.path in ("/", ""):
                body = WALL.replace("__CAMS__", cams_json).encode()
                self._send(200, "text/html", body)
            elif u.path == "/status":
                import json

                now = time.time()
                with estate.lock:
                    ages = {c: round(now - t, 1) for c, t in estate.updated.items()}
                self._send(200, "application/json", json.dumps(ages).encode())
            elif u.path.startswith("/snap/"):
                cam = u.path[len("/snap/"):].split(".")[0]
                with estate.lock:
                    data = estate.frames.get(cam)
                if data is None:
                    self._send(503, "text/plain", b"warming up")
                else:
                    self._send(200, "image/jpeg", data, cache=True)
            else:
                self._send(404, "text/plain", b"not found")

        def _send(self, code, ct, body, cache=False):
            self.send_response(code)
            self.send_header("content-type", ct)
            self.send_header("content-length", str(len(body)))
            if cache:
                self.send_header("cache-control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

    return H


def main() -> int:
    import sys

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", type=int, default=6, help="concurrent RTSP decoders")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--width", type=int, default=512, help="JPEG width")
    ap.add_argument("--burst", type=float, default=3.0, help="seconds of live frames per camera per pass")
    ap.add_argument("--cameras", nargs="*", default=None, help="grid ids; default cam01..cam30")
    args = ap.parse_args()

    sys.path.insert(0, str(WORKER_ROOT))
    load_env(WORKER_ROOT.parent.parent / ".env")
    import os

    if not os.getenv("SENTINEL_GRID_PASSWORD"):
        raise SystemExit("SENTINEL_GRID_PASSWORD is not set (repo .env)")

    cams = args.cameras or [f"cam{i:02d}" for i in range(1, 31)]
    # Names, if the reference file is present (nice labels; not required).
    catalogue: dict[str, str] = {}
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
                catalogue[cid] = str(r.get("site_name") or r.get("name") or r.get("location") or cid)
        except Exception:
            pass

    estate = Estate(cams, pool=args.pool, width=args.width, burst_s=args.burst)
    estate.start()
    print(f"snapshot wall: {len(cams)} cameras, pool {estate.pool}, on http://127.0.0.1:{args.port}")

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", args.port), build_handler(estate, catalogue))
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
