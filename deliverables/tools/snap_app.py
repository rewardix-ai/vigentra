"""Screenshots of the Vigentra console for the keynote (deliverables/anpr-keynote/assets/img/app_*.jpg).

    # a separate headless Chrome, never your own profile
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --remote-debugging-port=9334 \
        --user-data-dir=/tmp/vigentra-snap --hide-scrollbars about:blank &
    python3 deliverables/tools/snap_app.py

Signs in to the local console as the seeded demo department admin (services/central-api/app/config.py;
the password is read there and never printed), lays the page out at 1440x810 and captures 1920x1080.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_argv, sys.argv = sys.argv, [sys.argv[0], "/tmp/vigentra-snap-unused", "own"]   # record_demo reads argv on import
from record_demo import DevTools  # noqa: E402  (stdlib CDP client)
sys.argv = _argv

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "deliverables/anpr-keynote/assets/img"
BASE = "http://localhost:3000"
W, H, SCALE = 1440, 810, 1920 / 1440
USER = "dept.admin"
TRACE_PLATE = "GJ39CB0189"

FILL = """(label, value) => {
  const lab = [...document.querySelectorAll('label')].find(l => l.textContent.includes(label));
  const inp = (lab && lab.htmlFor && document.getElementById(lab.htmlFor)) || (lab && lab.parentElement.querySelector('input,textarea'));
  if (!inp) return false;
  const proto = inp.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(inp, value);
  inp.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
}"""
CLICK = """(pattern) => {
  const re = new RegExp(pattern);
  const b = [...document.querySelectorAll('button')].find(x => re.test(x.textContent) && !x.disabled);
  if (b) b.click();
  return !!b;
}"""


def password() -> str:
    src = (REPO / "services/central-api/app/config.py").read_text()
    return re.search(r'"password":\s*"([^"]+)"', src[src.index(f'"username": "{USER}"'):]).group(1)


def main() -> int:
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9334/json"))
    page = next(t for t in tabs if t.get("type") == "page")
    dt = DevTools(page["webSocketDebuggerUrl"], lambda *a: None)
    dt.call("Page.enable")
    dt.call("Emulation.setDeviceMetricsOverride", {"width": W, "height": H, "deviceScaleFactor": SCALE, "mobile": False})

    def js(expr, *args):
        call = f"({expr})({', '.join(json.dumps(a) for a in args)})" if args else expr
        r = dt.call("Runtime.evaluate", {"expression": call, "awaitPromise": True, "returnByValue": True})
        return r.get("result", {}).get("value")

    def go(path, settle=4.0):
        dt.call("Page.navigate", {"url": BASE + path})
        time.sleep(settle)

    def snap(name, y=0):
        if y:
            js(f"window.scrollTo(0, {y})")
            time.sleep(0.8)
        png = base64.b64decode(dt.call("Page.captureScreenshot", {"format": "png"})["data"])
        tmp = OUT / f"{name}.png"
        tmp.write_bytes(png)
        print("captured", name)

    go("/login", 3)
    pw = password()
    ok = js(f"fetch('/api/auth/login', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{username: {json.dumps(USER)}, password: {json.dumps(pw)}}})}}).then(r => r.status)")
    print("signed in:", ok)

    go("/", 5); snap("app_new_overview")
    go("/registry?view=map", 7); snap("app_new_registry")
    go("/incidents", 6); snap("app_new_incidents", 330)
    if js(CLICK, "Confirm & issue challan"):
        time.sleep(1.5); snap("app_new_challan")
    go("/reports/gap-analysis", 6)
    y = js("(() => { const h = [...document.querySelectorAll('h2,h3,div')].find(e => e.textContent.trim() === 'Footage quality'); return h ? h.getBoundingClientRect().top + window.scrollY - 90 : 0; })()")
    snap("app_new_footage", int(y or 0))
    go("/alerts", 5); snap("app_new_alerts")
    go("/plates", 4)
    js(FILL, "Registration number", TRACE_PLATE)
    js(FILL, "Why are you tracing", "Keynote screenshot of the trace (rehearsal)")
    time.sleep(0.5)
    js(CLICK, "Search sightings")
    time.sleep(4)
    js(CLICK, "Trace movement")
    time.sleep(7)
    y = js("(() => { const m = document.querySelector('.leaflet-container'); return m ? m.getBoundingClientRect().top + window.scrollY - 140 : 0; })()")
    snap("app_new_trace", int(y or 0))
    go("/live", 4)
    js(FILL, "Why are you viewing", "Keynote screenshot of the live wall")
    js(FILL, "Confirm your password", pw)
    time.sleep(0.5)
    js(CLICK, "^\\s*Start")
    time.sleep(14); snap("app_new_live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
