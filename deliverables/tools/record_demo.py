"""Record the two submission demos, beat by beat as docs/demo-script.md lists them.

    # 1. a separate Chrome, so the recording never touches your own profile
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9333 \
        --user-data-dir=/tmp/vigentra-rec --disable-backgrounding-occluded-windows \
        --disable-renderer-backgrounding http://localhost:3000/login
    # 2. sign in there yourself as joint.control (this script never types or reads a password)
    # 3. record
    python3 record_demo.py <outdir> own      # demo A: own feed (the Delhi clip)
    python3 record_demo.py <outdir> grid     # demo B: the Government grid
    # 4. cut:  python edit_demo.py <outdir> <out.mp4>

Demo A needs the ANPR reader running on the Delhi camera while you record, so that the watched
vehicle is read after it is added (docker compose --profile anpr-live, see docker-compose.yml).
The live wall asks for your password once; type it when the caption says so.

Stdlib only. The page is laid out at 1440x810 and captured at 1920x1080, which is the zoom the
15 Sep recording used. Frames go to <outdir>/frames/, timestamps to frames.jsonl, scene marks
to marks.jsonl.
"""
import base64, json, math, os, random, socket, struct, sys, threading, time, urllib.request

PORT = 9333
W, H = 1440, 810          # CSS pixels the page is laid out in
CAP_W, CAP_H = 1920, 1080  # pixels captured
DELHI = "VIGENTRA-TRAFFIC-AHM-0001"
CAM06 = "VIGENTRA-TRAFFIC-BHA-CAM06"
WATCH_PLATE = os.environ.get("WATCH_PLATE", "HR26CC2083")   # the Delhi clip shows it on every run
GRID_PLATE = os.environ.get("GRID_PLATE", "")               # a plate CAM06 has read, for demo B's route
VMS_FEEDS = os.environ.get("VMS_FEEDS", "our Delhi clip and 19 public traffic-signal cameras")

OUT = os.path.abspath(sys.argv[1])
DEMO = sys.argv[2] if len(sys.argv) > 2 else "own"
ONLY = sys.argv[3:]
os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)
LOG = open(os.path.join(OUT, "log.txt"), "a")


def log(*parts):
    line = time.strftime("%H:%M:%S ") + " ".join(str(p) for p in parts)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


class WebSocket:
    """Just enough RFC 6455 for one DevTools connection: text frames, no extensions."""

    def __init__(self, url):
        hostport, path = url[len("ws://"):].split("/", 1)
        host, port = hostport.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed")
            buf += chunk
        head, _, rest = bytes(buf).partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))
        self.buf = bytearray(rest)
        self.wlock = threading.Lock()

    def _send(self, opcode, payload):
        n = len(payload)
        head = bytearray([0x80 | opcode])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack(">Q", n)
        mask = os.urandom(4)
        head += mask
        body = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        with self.wlock:
            self.sock.sendall(bytes(head) + body)

    def send(self, text):
        self._send(0x1, text.encode())

    def _take(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._take(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._take(8))[0]
            mask = self._take(4) if b2 & 0x80 else None
            payload = self._take(n)
            if mask:
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
            op = b1 & 0x0F
            if op == 0x8:
                raise ConnectionError("closed by browser")
            if op == 0x9:
                self._send(0xA, payload)
                continue
            if op == 0xA:
                continue
            parts.append(payload)
            if b1 & 0x80:
                return b"".join(parts).decode()


class DevTools:
    def __init__(self, ws_url, on_frame):
        self.ws = WebSocket(ws_url)
        self.on_frame = on_frame
        self.ids = 0
        self.replies = {}
        self.fired = set()
        self.cv = threading.Condition()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            msg = json.loads(self.ws.recv())
            if "id" in msg:
                with self.cv:
                    if msg["id"] in self.fired:
                        self.fired.discard(msg["id"])
                    else:
                        self.replies[msg["id"]] = msg
                        self.cv.notify_all()
            elif msg.get("method") == "Page.screencastFrame":
                self.on_frame(self, msg["params"])

    def _next(self):
        with self.cv:
            self.ids += 1
            return self.ids

    def call(self, method, params=None, timeout=30):
        i = self._next()
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        with self.cv:
            while i not in self.replies:
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError(method)
                self.cv.wait(left)
            msg = self.replies.pop(i)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error'].get('message')}")
        return msg.get("result", {})

    def fire(self, method, params=None):
        i = self._next()
        with self.cv:
            self.fired.add(i)
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))


class Frames:
    def __init__(self):
        self.n = len(os.listdir(os.path.join(OUT, "frames")))
        self.index = open(os.path.join(OUT, "frames.jsonl"), "a")
        self.lock = threading.Lock()

    def __call__(self, devtools, params):
        stamp = (params.get("metadata") or {}).get("timestamp") or time.time()
        with self.lock:
            name = f"{self.n:06d}.jpg"
            self.n += 1
        with open(os.path.join(OUT, "frames", name), "wb") as handle:
            handle.write(base64.b64decode(params["data"]))
        self.index.write(json.dumps({"f": name, "t": stamp}) + "\n")
        self.index.flush()
        devtools.fire("Page.screencastFrameAck", {"sessionId": params["sessionId"]})


MARKS = open(os.path.join(OUT, "marks.jsonl"), "a")


def mark(scene, what):
    MARKS.write(json.dumps({"scene": scene, "what": what, "t": time.time()}) + "\n")
    MARKS.flush()


# A visible pointer and a caption strip, drawn in the page so the screencast
# carries them. Appended to <html>, outside React's tree, so re-renders and
# client-side navigation leave them alone.
OVERLAY = r"""
(() => {
  if (window.__vg) return;
  window.__vg = true;
  const vis = (e) => { const r = e.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const text = (e) => ((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || '')).replace(/\s+/g, ' ').trim();
  const box = (e) => { const r = e.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, bottom: r.bottom, left: r.left, right: r.right, w: r.width, h: r.height}; };
  window.__vgFind = (sel, pattern, nth) => {
    const re = pattern ? new RegExp(pattern, 'i') : null;
    const hits = [...document.querySelectorAll(sel)].filter(e => vis(e) && (!re || re.test(text(e))));
    const e = hits[nth || 0];
    return e ? Object.assign(box(e), {count: hits.length}) : null;
  };
  window.__vgControl = (label) => {
    const want = label.toLowerCase();
    for (const l of document.querySelectorAll('label')) {
      if (!(l.innerText || '').toLowerCase().includes(want)) continue;
      const c = l.control || (l.htmlFor && document.getElementById(l.htmlFor)) || l.querySelector('input,textarea,select')
        || (l.parentElement && l.parentElement.querySelector('input,textarea,select'));
      if (c && vis(c)) return c;
    }
    return [...document.querySelectorAll('input,textarea,select')].find(e => vis(e)
      && ((e.getAttribute('placeholder') || '') + ' ' + (e.getAttribute('aria-label') || '')).toLowerCase().includes(want)) || null;
  };
  window.__vgField = (label) => { const c = window.__vgControl(label); return c ? box(c) : null; };
  window.__vgSelectAll = (label) => { const c = window.__vgControl(label); if (!c) return false; c.focus(); if (c.select) c.select(); return true; };
  window.__vgChoose = (label, pattern) => {
    const c = window.__vgControl(label); if (!c || c.tagName !== 'SELECT') return false;
    const re = new RegExp(pattern, 'i');
    const o = [...c.options].find(o => re.test(o.text) || re.test(o.value)); if (!o) return false;
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(c, o.value);
    c.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  };
  window.__vgFilled = (sel) => { const e = document.querySelector(sel); return !!(e && e.value && e.value.length > 0); };
  const install = () => {
    const root = document.documentElement;
    if (!root || !document.body) return setTimeout(install, 30);
    const cursor = document.createElement('div');
    cursor.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24"><path d="M5 2.5v17.2l4.3-4.1 2.9 6.6 2.9-1.3-2.9-6.5h6z" fill="#0b1220" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(cursor.style, {position: 'fixed', left: '-100px', top: '-100px', zIndex: 2147483647, pointerEvents: 'none',
      filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.45))', transform: 'translate(-5px,-2px)'});
    const ring = document.createElement('div');
    Object.assign(ring.style, {position: 'fixed', width: '44px', height: '44px', margin: '-22px 0 0 -22px', borderRadius: '50%',
      border: '3px solid rgba(59,130,246,.95)', background: 'rgba(59,130,246,.18)', zIndex: 2147483646, pointerEvents: 'none', opacity: 0});
    const cap = document.createElement('div');
    Object.assign(cap.style, {position: 'fixed', left: '50%', bottom: '26px', transform: 'translateX(-50%)', maxWidth: '1180px',
      padding: '14px 28px', borderRadius: '14px', background: 'rgba(8,15,30,.9)', color: '#fff',
      font: '600 25px/1.35 -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif', textAlign: 'center',
      zIndex: 2147483645, pointerEvents: 'none', opacity: 0, transition: 'opacity .35s ease', boxShadow: '0 10px 30px rgba(0,0,0,.35)'});
    root.append(cursor, ring, cap);
    addEventListener('mousemove', e => { cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', e => { ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
      ring.animate([{opacity: 1, transform: 'scale(.35)'}, {opacity: 0, transform: 'scale(1.5)'}], {duration: 520, easing: 'ease-out'}); }, true);
    window.__cap = (title, sub) => {
      if (!title) { cap.style.opacity = 0; return; }
      cap.textContent = '';
      const t = document.createElement('div'); t.textContent = title; cap.append(t);
      if (sub) { const s = document.createElement('div'); s.textContent = sub;
        Object.assign(s.style, {font: '400 20px/1.35 -apple-system, "Segoe UI", Inter, Helvetica, Arial, sans-serif', opacity: .88, marginTop: '4px'});
        cap.append(s); }
      cap.style.opacity = 1;
    };
  };
  install();
})();
"""

dt = None
hand = None


def js(expr, timeout=20):
    result = dt.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
    if result.get("exceptionDetails"):
        details = result["exceptionDetails"]
        raise RuntimeError((details.get("exception") or {}).get("description") or details.get("text"))
    return (result.get("result") or {}).get("value")


def cap(title=None, sub=None):
    js(f"window.__cap && window.__cap({json.dumps(title)}, {json.dumps(sub)})")


def wait_for(expr, timeout=15, every=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = js(expr)
            if value:
                return value
        except Exception:  # noqa: BLE001 - the page may be mid-navigation
            pass
        time.sleep(every)
    return None


def find(sel, pattern=None, nth=0, timeout=10):
    found = wait_for(f"window.__vgFind({json.dumps(sel)}, {json.dumps(pattern)}, {nth})", timeout)
    if not found:
        raise LookupError(f"{sel} /{pattern}/")
    return found


def field(label, timeout=10):
    found = wait_for(f"window.__vgField({json.dumps(label)})", timeout)
    if not found:
        raise LookupError(f"field {label!r}")
    return found


class Hand:
    """A mouse that travels on eased, slightly curved paths, as a hand does."""

    def __init__(self):
        self.x, self.y = W * 0.62, H * 0.52

    def _event(self, kind, x, y, **extra):
        dt.call("Input.dispatchMouseEvent", {"type": kind, "x": round(x, 1), "y": round(y, 1), **extra})

    def move(self, x, y, dur=None):
        x0, y0 = self.x, self.y
        dist = math.hypot(x - x0, y - y0)
        if dist < 2:
            return
        dur = dur or min(1.1, 0.3 + dist / 1700)
        steps = max(10, int(dur * 60))
        bend = random.uniform(-0.14, 0.14) * dist
        nx, ny = -(y - y0) / dist, (x - x0) / dist
        cx, cy = (x0 + x) / 2 + nx * bend, (y0 + y) / 2 + ny * bend
        start = time.time()
        for i in range(1, steps + 1):
            u = i / steps
            e = u * u * (3 - 2 * u)
            px = (1 - e) ** 2 * x0 + 2 * (1 - e) * e * cx + e * e * x
            py = (1 - e) ** 2 * y0 + 2 * (1 - e) * e * cy + e * e * y
            self._event("mouseMoved", px, py)
            lag = start + dur * u - time.time()
            if lag > 0:
                time.sleep(lag)
        self.x, self.y = x, y

    def click(self, x, y):
        self.move(x, y)
        time.sleep(random.uniform(0.14, 0.3))
        self._event("mousePressed", x, y, button="left", buttons=1, clickCount=1)
        time.sleep(random.uniform(0.05, 0.09))
        self._event("mouseReleased", x, y, button="left", buttons=0, clickCount=1)

    def wheel(self, dy, dur=None):
        steps = max(4, int(abs(dy) / 40))
        dur = dur or min(1.8, 0.35 + abs(dy) / 850)
        for _ in range(steps):
            self._event("mouseWheel", self.x, self.y, deltaX=0, deltaY=dy / steps)
            time.sleep(dur / steps)


CONTENT = (W * 0.6, H * 0.52)


def scroll(dy, dur=None):
    if hand.x < 300:  # over the sidebar: bring the hand to the page first
        hand.move(*CONTENT)
    hand.wheel(dy, dur)
    time.sleep(0.4)


def reveal(getter, top=110, bottom=150):
    for _ in range(10):
        box = getter()
        if box["top"] >= top and box["bottom"] <= H - bottom:
            return box
        scroll(max(-800, min(800, box["y"] - H * 0.45)))
    return getter()


def click_el(sel, pattern=None, nth=0, timeout=10):
    box = reveal(lambda: find(sel, pattern, nth, timeout))
    hand.click(box["x"], box["y"])


def hover_el(sel, pattern=None, nth=0, timeout=10):
    box = reveal(lambda: find(sel, pattern, nth, timeout))
    hand.move(box["x"], box["y"])


def nav(href):
    """Open a module from the sidebar, the way an operator would."""
    try:
        click_el(f'a[href="{href}"]', timeout=4)
    except LookupError:          # not in this account's sidebar: go by address instead
        js(f"location.assign({json.dumps(href)})")
    wait_for(f"location.pathname === {json.dumps(href)}", 10)
    time.sleep(1.3)


def key(name, code, vk):
    for kind in ("keyDown", "keyUp"):
        dt.call("Input.dispatchKeyEvent", {"type": kind, "key": name, "code": code, "windowsVirtualKeyCode": vk})


def type_into(label, text, clear=True, cps=15):
    box = reveal(lambda: field(label))
    hand.click(box["x"], box["y"])
    time.sleep(0.2)
    if clear:
        js(f"window.__vgSelectAll({json.dumps(label)})")
        if not text:
            key("Backspace", "Backspace", 8)
    for ch in text:
        dt.call("Input.insertText", {"text": ch})
        time.sleep(random.uniform(0.55, 1.4) / cps)
    time.sleep(0.3)


def choose(label, pattern):
    box = reveal(lambda: field(label))
    hand.move(box["x"], box["y"])
    time.sleep(0.35)
    if not js(f"window.__vgChoose({json.dumps(label)}, {json.dumps(pattern)})"):
        log("choose failed:", label, pattern)
    time.sleep(0.5)


def rows_ready(timeout=25):
    return wait_for("document.querySelectorAll('table tbody tr').length > 0", timeout)


def browse(steps=3, dy=560, pause=2.4):
    hand.move(*CONTENT)
    for _ in range(steps):
        scroll(dy)
        time.sleep(pause)
    scroll(-dy * steps, dur=1.6)
    time.sleep(1.0)


# --------------------------------------------------------------------------
# Scenes, in recording order. The live wall comes first because it is the
# one step that needs the operator at the keyboard (their password).
# --------------------------------------------------------------------------

def wall(reason, then_click=None):
    nav("/live")
    wait_for("document.body.innerText.includes('your account may watch')", 20)
    type_into("Why are you viewing these feeds", reason)
    if not js("window.__vgFilled('input[type=password]')"):
        log("waiting for the operator to type the password into the wall form ...")
        cap("The operator re-enters the password", "Step-up authentication before any feed opens")
        mark("wait", "start")
        if not wait_for("window.__vgFilled('input[type=password]')", 900, 1.0):
            raise RuntimeError("no password was entered")
        mark("wait", "end")
    click_el("button", r"^Start \d+ feed")
    time.sleep(8)
    click_el("button", r"^Fit all on screen")


def open_camera(search, camera_id):
    nav("/registry")
    type_into("Search", search)
    time.sleep(1.5)
    click_el(f'a[href$="{camera_id}"]')
    wait_for(f"location.pathname.endsWith({json.dumps(camera_id)})", 15)


def watch(plate, reason, case):
    nav("/watchlist")
    time.sleep(1.5)
    type_into("Registration number", plate)
    choose("Category", r"stolen")
    type_into("Case reference (optional)", case)
    type_into("Why is this vehicle being watched? (required)", reason)
    time.sleep(0.8)
    click_el("button", r"Add to watchlist")
    wait_for(f"document.querySelector('table') && document.querySelector('table').innerText.includes({json.dumps(plate)})", 15)
    time.sleep(2.5)


def alert_arrives(plate, timeout):
    nav("/alerts")
    seen = f"[...document.querySelectorAll('table tbody tr')].some(r => r.innerText.includes({json.dumps(plate)}))"
    mark("wait", "start")
    if not wait_for(seen, timeout, 1.0):
        raise RuntimeError(f"no alert for {plate} within {timeout}s - is the ANPR reader running on this camera?")
    mark("wait", "end")
    time.sleep(3.5)
    click_el("button", r"^\s*Acknowledge")
    time.sleep(3)


# ---- demo A: own feed -------------------------------------------------------------------

def a_onboard():
    nav("/installations")
    cap("1 · Onboarding a feed", "The Delhi camera arrived as an installation request from the Traffic Police VMS")
    time.sleep(3)
    browse(steps=1, dy=500, pause=2.0)
    open_camera("Delhi", DELHI)
    cap("1 · Onboarding a feed", "Registered, on the map, and playing through a brokered, audited session")
    time.sleep(3)
    browse(steps=3, dy=520, pause=2.2)


def a_detect():
    cap("2 · AI detection and analytics", "Live wall: vehicle detection drawn on every feed as it plays")
    wall("Own-feed demonstration for the Sentinel hackathon submission")
    cap("2 · AI detection and analytics", "Every tile is a live feed with the edge's detections on it")
    time.sleep(8)
    nav("/detections")
    cap("2 · AI detection and analytics", "Every vehicle the edge detects, with its plate where ANPR settled one")
    type_into("Camera ID", DELHI)
    click_el("button", r"Show detections")
    rows_ready()
    browse(steps=2, dy=600, pause=2.4)


def a_watch():
    cap("3 · Correlation with a watchlist", "A stolen vehicle is added, with a reason and a case reference. The entry is audited")
    watch(WATCH_PLATE, "Reported stolen; owner complaint at Vastrapur PS", "FIR 118/2026")
    cap("3 · Correlation with a watchlist", "Every plate read is matched against this list as it is ingested: exact, or one misread away")
    time.sleep(3)


def a_alert():
    cap("4 · Real-time alert", "The feed keeps playing. The moment the edge settles this plate, the alert is raised")
    alert_arrives(WATCH_PLATE, 1200)   # a 1080p pass on one laptop can take ten minutes to settle a plate
    cap("4 · Real-time alert", "Critical: an exact read of a stolen vehicle. Camera, time and place; acknowledged by the operator")
    time.sleep(3)


def a_route():
    nav("/plates")
    cap("The route, from the plate", "Every sighting of this registration across cameras, timestamped, on the map")
    type_into("Registration number", WATCH_PLATE)
    click_el("button", r"search|find|look ?up")
    rows_ready(20)
    type_into("Why are you tracing this vehicle", "Watchlist alert: stolen vehicle, FIR 118/2026")
    click_el("button", r"^trace")
    time.sleep(3.5)
    browse(steps=2, dy=600, pause=2.6)
    nav("/audit")
    cap("Audited", "The watchlist entry, the machine-raised alert, the acknowledgement and the trace are all here")
    time.sleep(3)
    browse(steps=1, dy=500, pause=2.5)


# ---- demo B: the Government grid -------------------------------------------------------

def b_onboard():
    nav("/registry")
    cap("1 · The Government feeds, onboarded", "30 of 30 Sentinel grid cameras, read from the grid's own catalogue, never hard-coded")
    time.sleep(2)
    type_into("Search", "GRID")
    time.sleep(2.5)
    browse(steps=2, dy=600, pause=2.0)
    click_el("button", r"^Map$")
    cap("1 · The Government feeds, onboarded", "Each with its department, coordinates and live status")
    time.sleep(5)


def b_view():
    cap("2 · Live viewing", "The grid over its real-time RTSP gateway, with detection drawn on every tile")
    wall("Government-feed demonstration for the Sentinel hackathon submission")
    time.sleep(10)
    open_camera("Madhuram", CAM06)
    cap("2 · Live viewing", "Grid CAM06, Madhuram Bypass Road: plates read live by the same engine")
    time.sleep(3)
    browse(steps=3, dy=520, pause=2.4)


def b_analytics():
    nav("/detections")
    cap("3 · Analytics output", "Vehicles, persons and objects, per camera, with the plate where one settled")
    type_into("Camera ID", CAM06)
    click_el("button", r"Show detections")
    rows_ready()
    browse(steps=2, dy=600, pause=2.4)
    nav("/incidents")
    cap("3 · Analytics output", "Beyond ANPR: wrong-way, stopped in lane, person in traffic, intrusion into a restricted zone")
    time.sleep(3)
    browse(steps=1, dy=500, pause=2.5)
    nav("/alerts")
    cap("3 · Analytics output", "Watchlist alerts, prioritised: critical, high, review")
    time.sleep(4)


def b_report():
    nav("/reports/anpr")
    cap("4 · Output report", "Plate, camera, place and timestamp: the report submitted with this recording")
    type_into("Camera ID", CAM06)
    click_el("button", r"^generate")
    rows_ready(30)
    browse(steps=2, dy=650, pause=2.6)
    if GRID_PLATE:
        nav("/plates")
        cap("The route, from the plate", "The hackathon-day test case: one registration, traced across the grid")
        type_into("Registration number", GRID_PLATE)
        click_el("button", r"search|find|look ?up")
        rows_ready(20)
        type_into("Why are you tracing this vehicle", "Demonstration of the designated-vehicle trace")
        click_el("button", r"^trace")
        time.sleep(3.5)
        browse(steps=2, dy=600, pause=2.6)


DEMOS = {
    "own": [("onboard", a_onboard), ("detect", a_detect), ("watch", a_watch), ("alert", a_alert), ("route", a_route)],
    "grid": [("onboard", b_onboard), ("view", b_view), ("analytics", b_analytics), ("report", b_report)],
}
SCENES = DEMOS[DEMO]

def main():
    global dt, hand
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    page = next(t for t in targets if t.get("type") == "page" and "localhost:3000" in t.get("url", ""))
    dt = DevTools(page["webSocketDebuggerUrl"], Frames())
    dt.call("Page.enable")
    dt.call("Page.addScriptToEvaluateOnNewDocument", {"source": OVERLAY})
    js(OVERLAY)
    dt.call("Emulation.setDeviceMetricsOverride", {"width": W, "height": H, "deviceScaleFactor": CAP_W / W,
                                                   "mobile": False, "screenWidth": W, "screenHeight": H})
    dt.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
    time.sleep(1.5)
    dt.call("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": CAP_W, "maxHeight": CAP_H, "everyNthFrame": 1})
    hand = Hand()
    hand.move(W * 0.6, H * 0.5, dur=0.4)
    for name, scene in SCENES:
        if ONLY and name not in ONLY:
            continue
        log("scene", name)
        mark(name, "start")
        try:
            scene()
        except Exception as exc:  # noqa: BLE001 - keep recording the rest
            log("scene", name, "failed:", repr(exc))
        try:
            cap(None)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.6)
        mark(name, "end")
    dt.call("Page.stopScreencast")
    time.sleep(0.8)
    log("done:", Frames.__name__, "frames in", os.path.join(OUT, "frames"))


if __name__ == "__main__":
    main()
