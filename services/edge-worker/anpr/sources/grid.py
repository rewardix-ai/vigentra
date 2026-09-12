"""Sentinel grid access, the way Vigentra's edge worker does it
(~/Vigentra/vigentra/services/edge-worker/app/grid.py).

The RTSP gateway authenticates every connection with the registered e-mail and
access password in the URL, the e-mail's "@" percent-encoded:

    rtsp://<email%40...>:<password>@103.250.160.189:8554/stream/<id>

Credentials come from SENTINEL_GRID_EMAIL / SENTINEL_GRID_PASSWORD (the same
variables Vigentra reads, so its .env can be passed with --env-file). They are
injected only into the URL handed to the decoder and redacted from every label,
log line, report and output file (safe_url / redact).
"""
from __future__ import annotations

import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional

GRID_KEYS = ("SENTINEL_GRID_EMAIL", "SENTINEL_GRID_PASSWORD", "SENTINEL_GRID_RTSP_HOST",
             "SENTINEL_GRID_RTSP_PORT", "SENTINEL_GRID_BASE_URL")
_CAMERA_ID = re.compile(r"cam\d{2}")


def load_env_file(path: str | Path) -> list[str]:
    """Read the SENTINEL_GRID_* lines of a dotenv file into os.environ; every other key
    is ignored and a value already set in the environment wins. Returns the key names
    loaded, never the values."""
    loaded = []
    for raw in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if key not in GRID_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()   # compose-style inline comment
        if value and not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded


def has_credentials() -> bool:
    return bool(os.getenv("SENTINEL_GRID_EMAIL", "").strip() and os.getenv("SENTINEL_GRID_PASSWORD", "").strip())


def rtsp_base() -> str:
    host = os.getenv("SENTINEL_GRID_RTSP_HOST", "103.250.160.189").strip()
    port = os.getenv("SENTINEL_GRID_RTSP_PORT", "8554").strip()
    return f"rtsp://{host}:{port}/stream/"


def is_camera_id(source: str) -> bool:
    """`cam06` etc.: a grid camera id rather than a file or URL."""
    return bool(_CAMERA_ID.fullmatch(source.strip().lower())) and not Path(source).exists()


def camera_url(cam_id: str) -> str:
    """The documented RTSP URL of a grid camera (no credentials; see with_credentials)."""
    return rtsp_base() + cam_id.strip().lower()


def with_credentials(url: str) -> str:
    """Inject the grid credentials into an rtsp:// URL that lacks a userinfo part. A URL
    that already carries one, a non-RTSP URL, or no configured credentials: unchanged."""
    email = os.getenv("SENTINEL_GRID_EMAIL", "").strip()
    password = os.getenv("SENTINEL_GRID_PASSWORD", "").strip()
    if not (email and password and url):
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ("rtsp", "rtsps") or parsed.username:
        return url
    userinfo = f"{urllib.parse.quote(email, safe='')}:{urllib.parse.quote(password, safe='')}@"
    return urllib.parse.urlunsplit((parsed.scheme, userinfo + parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def safe_url(url: str) -> str:
    """The URL with any userinfo replaced by ***:***, for logs, reports and outputs."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    if not parsed.username:
        return url
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, f"***:***@{host}", parsed.path, parsed.query, parsed.fragment))


def redact(text: str) -> str:
    """Remove the configured e-mail and password (raw and percent-encoded) from free text,
    e.g. ffmpeg's stderr."""
    for key in ("SENTINEL_GRID_PASSWORD", "SENTINEL_GRID_EMAIL"):
        value = os.getenv(key, "").strip()
        if value:
            for form in {value, urllib.parse.quote(value, safe="")}:
                text = text.replace(form, "***")
    return text


# ---------------------------------------------------------------------------
# Web gateway (HLS mirror): session sign-in + catalogue, ported from Vigentra's edge worker
# (services/edge-worker/app/grid.py: _opener, fetch_catalogue, fallback_catalogue).
# ---------------------------------------------------------------------------
USER_AGENT = "Mozilla/5.0"      # Cloudflare in front of the gateway 403s ffmpeg's / urllib's default UA
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36")   # the sign-in form is posted as a browser would post it
FALLBACK_IDS = tuple(f"cam{i:02d}" for i in range(1, 31))


def base_url() -> str:
    return os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud").strip().rstrip("/")


def hls_url(cam_id: str, base: Optional[str] = None) -> str:
    """The documented HLS pattern of a grid camera (Vigentra fallback_catalogue)."""
    return f"{(base or base_url()).rstrip('/')}/{cam_id.strip().lower()}/index.m3u8"


def hls_http_args(cookie: Optional[str], user_agent: str = BROWSER_UA, base: Optional[str] = None) -> list[str]:
    """ffmpeg HTTP options for the grid's HLS. The session is presented exactly as the browser-like
    sign-in created it (same User-Agent, Referer / Origin of the Control Room): with the cookie but
    a bare "Mozilla/5.0" the gateway answered 403 on every stream (2026-09-11). The cookie also goes
    through -cookies, which the HLS demuxer re-sends on every playlist / key / segment request."""
    root = (base or base_url()).rstrip("/")
    args = ["-user_agent", user_agent, "-referer", root + "/"]
    if cookie:
        host = root.split("://", 1)[-1]
        jar = "".join(f"{kv.strip()}; path=/; domain={host}\n" for kv in cookie.split(";") if "=" in kv)
        args += ["-cookies", jar, "-headers", f"Cookie: {cookie}\r\nOrigin: {root}\r\n"]
    return args


def session_cookie(base: Optional[str] = None, timeout: float = 20.0) -> tuple[Optional[str], str]:
    """Sign in to the web gateway as Vigentra's edge worker does: POST the access password - and
    the registered e-mail, which the form now also takes - to /auth/login. A refused sign-in is
    answered with HTTP 200 and the sign-in page, so success is judged by the session cookie
    alone. Returns (Cookie header value or None, reason). One request; never retried here."""
    import http.cookiejar
    import urllib.request
    password = os.getenv("SENTINEL_GRID_PASSWORD", "").strip()
    if not password:
        return None, "SENTINEL_GRID_PASSWORD is not set"
    form = {"password": password}
    email = os.getenv("SENTINEL_GRID_EMAIL", "").strip()
    if email:
        form["email"] = email
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    root = (base or base_url()).rstrip("/")
    login = root + "/auth/login"
    # submit the form the way a browser does: load the page first (any pre-session cookie the
    # app sets travels with the POST), then post with Origin / Referer - apps that guard their
    # form against cross-site posts answer a header-less POST with the sign-in page again
    browser = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    try:
        opener.open(urllib.request.Request(login, headers=browser), timeout=timeout).read()
    except Exception:                                          # noqa: BLE001 - the POST decides
        pass
    req = urllib.request.Request(login, data=urllib.parse.urlencode(form).encode(),
                                 headers={**browser, "Origin": root, "Referer": login,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = opener.open(req, timeout=timeout)
        body = resp.read()
    except Exception as exc:                                   # noqa: BLE001 - reported to the caller
        return None, redact(f"sign-in request failed: {type(exc).__name__}: {exc}")
    cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
    landed = urllib.parse.urlsplit(resp.geturl()).path
    if not cookie or (landed.rstrip("/") == "/auth/login" and b'name="password"' in body):
        return None, (f"the gateway answered with its sign-in page again (HTTP {resp.status}, cookies: "
                      f"{', '.join(c.name for c in jar) or 'none'}) - it did not accept this e-mail / password")
    return cookie, "ok"


def _session_file() -> Path:
    return Path(os.getenv("SENTINEL_GRID_SESSION_FILE", "~/.cache/anpr/grid_session")).expanduser()


def load_session(max_age_h: float = 12.0) -> Optional[str]:
    """The session cookie saved by the last successful sign-in, if it is younger than max_age_h.
    Reusing it avoids a sign-in per run: Cloudflare in front of the gateway started answering the
    sign-in POST with 403 after several sign-ins in one evening (2026-09-11)."""
    import time
    p = _session_file()
    try:
        if p.exists() and time.time() - p.stat().st_mtime < max_age_h * 3600:
            return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    return None


def save_session(cookie: str) -> None:
    """Keep the session cookie in a file only the user can read (it is a credential)."""
    p = _session_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cookie, encoding="utf-8")
    os.chmod(p, 0o600)


def grid_session(retry_waits: tuple[float, ...] = (20, 40), log=print) -> tuple[Optional[str], str]:
    """A working session: the saved cookie when the catalogue still accepts it, else ONE sign-in
    (retried only when the request itself was blocked, never after a refused login). A new cookie
    is saved for the next run."""
    import time
    cached = load_session()
    if cached:
        try:
            if fetch_catalogue(cached):
                return cached, "saved session"
        except Exception:                                      # noqa: BLE001 - expired: sign in again
            pass
    cookie, why = session_cookie()
    for wait in retry_waits:
        if cookie or not why.startswith("sign-in request failed"):
            break
        log(f"grid sign-in request blocked ({why}); retrying in {wait:.0f} s")
        time.sleep(wait)
        cookie, why = session_cookie()
    if cookie:
        save_session(cookie)
    return cookie, why


def parse_catalogue(payload, base: Optional[str] = None) -> dict[str, str]:
    """{camera id: HLS URL} from /cameras.json: the bare list the grid returns now or the older
    {"cameras": [...]} wrapper; a relative hls_live_url is resolved against the gateway."""
    base = (base or base_url()).rstrip("/")
    entries = payload.get("cameras", []) if isinstance(payload, dict) else payload
    out: dict[str, str] = {}
    for e in entries or []:
        cid = str((e or {}).get("id") or "").strip()
        if not cid:
            continue
        hls = str(e.get("hls_live_url") or "")
        out[cid] = hls_url(cid, base) if not hls else (base + hls if hls.startswith("/") else hls)
    return out


def fetch_catalogue(cookie: str, base: Optional[str] = None, timeout: float = 20.0) -> dict[str, str]:
    """Read /cameras.json with a session cookie. The gateway answers the sign-in page rather than
    a 401 when the session is missing, so a JSON error here means "not signed in"."""
    import json
    import urllib.request
    base = (base or base_url()).rstrip("/")
    req = urllib.request.Request(base + "/cameras.json", headers={"User-Agent": USER_AGENT, "Cookie": cookie})
    body = urllib.request.urlopen(req, timeout=timeout).read()
    try:
        return parse_catalogue(json.loads(body), base)
    except ValueError as exc:
        raise RuntimeError("/cameras.json did not return JSON - the session was not accepted") from exc


def ffmpeg_exe() -> Optional[str]:
    """ffmpeg on PATH, else the static build shipped with the imageio-ffmpeg wheel."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
