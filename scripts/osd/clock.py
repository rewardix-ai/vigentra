"""Sample every grid camera's on-screen clock (the time burned into the recording) and store how far
it runs from our own clock, so each plate reading can carry the time *in the video*.

The grid serves recordings, each looping on its own date and start time (cam06: 17 June from 18:00,
cam16: 13 June from 14:49, ...), and the server restarts them all together. Within one run of the
server a camera's video time = our time + a fixed offset; the offset changes when the server
restarts. So the offset is sampled per camera (two frames 3 s apart, both clocks read and required
to agree) into camera_plates.video_clock, and every reading gets read_at_video from the latest
sample of its camera (scripts/camera_plates.sql). collect_plates.py samples on start, after every
refusal window (a likely restart) before the readers resume, and once an hour.

    /Users/uchit/Downloads/ANPR/.venv/bin/python scripts/osd/clock.py [cam01 cam02 ...]

The clock is read with Apple's Vision framework (ocr.swift, compiled on first use; nothing is
downloaded). Frames are read in memory and never written to disk.
"""
import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "services" / "edge-worker"))
import collect_plates as cp  # noqa: E402

OCR_SRC = Path(__file__).with_name("ocr.swift")
OCR_BIN = Path.home() / "Library" / "Caches" / "vigentra" / "osd_ocr"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DATES = [(re.compile(r"(20\d{2})[-/. ](\d{1,2})[-/. ](\d{1,2})"), "ymd"), (re.compile(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](20\d{2})"), "dmy")]
TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)?", re.I)


def ocr_binary() -> Path:
    if not OCR_BIN.exists() or OCR_BIN.stat().st_mtime < OCR_SRC.stat().st_mtime:
        OCR_BIN.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["swiftc", "-O", str(OCR_SRC), "-o", str(OCR_BIN)], check=True, capture_output=True)
    return OCR_BIN


def parse(text: str, last_date=None):
    """The first date and time in the OCR text, as a naive camera-local datetime, or None. When only
    the time is legible, the date of the camera's last good sample is used."""
    text = re.sub(r"\s*([-/.:])\s*", r"\1", text)   # OCR puts stray spaces around separators
    t = TIME.search(text)
    if not t:
        return None
    for rx, order in DATES:
        d = rx.search(text)
        if d:
            a, b, c = (int(x) for x in d.groups())
            y, m, day = (a, b, c) if order == "ymd" else (c, b, a)
            break
    else:
        if last_date is None:
            return None
        y, m, day = last_date.year, last_date.month, last_date.day
    h, mi, s = int(t.group(1)), int(t.group(2)), int(t.group(3))
    if t.group(4):
        h = h % 12 + (12 if t.group(4).upper() == "PM" else 0)
    try:
        return dt.datetime(y, m, day, h, mi, s)
    except ValueError:
        return None


def read_clock(url: str, ocr: Path, last_date=None):
    """Two frames ~3 s apart from one session; returns (wall_ist, video_time) or None if they disagree."""
    import cv2
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return None
    shots, start = [], time.time()
    with tempfile.TemporaryDirectory() as tmp:
        while time.time() - start < 15 and len(shots) < 2:
            ok, frame = cap.read()
            if not ok:
                continue
            elapsed = time.time() - start
            # the gateway first replays a buffered burst faster than real time: drain it before reading
            if (not shots and elapsed > 4.0) or (shots and elapsed - shots[0][0] > 3.0):
                if frame.std() < 12:          # grey smear before the first keyframe: no clock to read
                    continue
                path = Path(tmp) / f"{len(shots)}.png"
                # the clock sits in a corner: OCR the top and bottom strips, enlarged, not the whole frame
                h = frame.shape[0]
                strips = cv2.vconcat([frame[: h * 18 // 100], frame[h * 82 // 100:]])
                cv2.imwrite(str(path), cv2.resize(strips, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))  # temporary
                shots.append((elapsed, dt.datetime.now(IST).replace(tzinfo=None), path))
        cap.release()
        if len(shots) < 2:
            return None
        out = subprocess.run([str(ocr)] + [str(p) for _, _, p in shots], capture_output=True, text=True).stdout
        texts = {str(p): " ".join(line.split("\t", 1)[1] for line in out.splitlines() if line.startswith(str(p))) for _, _, p in shots}
    (e1, w1, p1), (e2, w2, p2) = shots
    v1, v2 = parse(texts[str(p1)], last_date), parse(texts[str(p2)], last_date)
    if not v1 or not v2:
        return None
    # the two readings must advance like the wall clock (camera clocks tick in whole seconds)
    if abs((v2 - v1).total_seconds() - (w2 - w1).total_seconds()) > 3:
        return None
    return w2, v2


def sql(statement: str) -> str:
    return subprocess.run(cp.PG + ["-c", statement], capture_output=True, text=True).stdout.strip()


def sample(cams=None) -> dict:
    import yaml
    # the account the supervisor chose (the one the reader is NOT using) must survive the service env,
    # which names the main account: one stream per camera per account, so reading clocks on the
    # reader's own account got nothing (8 Oct: 1 sample in 1.5 h while the reader ran on account 1)
    chosen = {k: os.environ[k] for k in ("SENTINEL_GRID_EMAIL", "SENTINEL_GRID_PASSWORD") if os.environ.get(k)}
    os.environ.update(cp.service_env(yaml.safe_load((REPO / "docker-compose.yml").read_text()), "edge-worker", cp.dotenv()))
    os.environ.update(chosen)
    from app import grid
    grid._force_tcp_transport()
    ocr = ocr_binary()
    catalogue = grid.fallback_catalogue(os.environ.get("SENTINEL_GRID_BASE_URL", ""))
    results = {}
    for cid in cams or sorted(catalogue):
        got = None
        last = sql(f"SELECT video_time FROM camera_plates.video_clock WHERE camera = '{cid}' ORDER BY id DESC LIMIT 1")
        last_date = dt.datetime.fromisoformat(last).date() if last else None
        for _ in range(3):   # a smeared frame or a missed digit: try again before giving up
            got = read_clock(catalogue[cid].rtsp_url, ocr, last_date)
            if got:
                break
            time.sleep(2)
        if got:
            wall, video = got
            offset = round((video - wall).total_seconds())
            sql(f"INSERT INTO camera_plates.video_clock (camera, wall_ist, video_time, offset_seconds) VALUES ('{cid}', '{wall:%Y-%m-%d %H:%M:%S}', '{video:%Y-%m-%d %H:%M:%S}', {offset})")
            results[cid] = video
        else:
            results[cid] = None
        time.sleep(1)
    sql("SELECT camera_plates.refresh_video_times()")
    return results


if __name__ == "__main__":
    res = sample(sys.argv[1:] or None)
    for cid, video in res.items():
        print(cid, video or "no clock read")
