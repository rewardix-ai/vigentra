"""Read plates on every grid camera with the Mac's GPU until a deadline, and keep the readers alive.

Runs one continuous ANPR reader process per camera group (`app.worker --forever` with long passes),
on the host so the engine can use the Apple GPU (Docker on macOS has none). Each reader signs in to
central-api with its department's AI account, the same account the Docker workers use: traffic.ai for
Traffic Police cameras, municipal.ai for Municipal Corporation cameras. Credentials and grid settings
come from docker-compose.yml and .env at run time and are never printed. Captures are RTSP, as in
Docker, so no reader signs in to the grid's HTTPS gateway.

Every reading goes to central-api like any other worker's, so watchlist matching, search and the trace
see it, and the camera_plates trigger copies it into the camera's own table (scripts/camera_plates.sql).

    /Users/uchit/Downloads/ANPR/.venv/bin/python scripts/collect_plates.py --until "2026-10-11 23:00" \\
        --restore-docker vigentra-anpr-live-1 vigentra-edge-worker-1

What it does on its own:
- a reader that exits is restarted after 60 s, and after 10 min if it keeps failing (no retry storms
  against the gateway or the audit log);
- the grid refuses our login in recurring windows (RTSP 401, often 15-20 min). When the readers start
  hitting 401s, all of them are frozen (SIGSTOP) and one test login is made every few minutes (3, then 6,
  12, at most 15) until the grid accepts again; then they resume (SIGCONT);
- at the deadline (IST) it stops every reader and starts the Docker containers named by --restore-docker;
- it holds a macOS caffeinate assertion while it runs, so an idle Mac on power does not sleep;
- once an hour it appends each camera table's row count to progress.log.
Logs: ~/Library/Logs/vigentra-readers/ (survives a reboot; the readers themselves do not).
"""
import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

REPO = Path(__file__).resolve().parent.parent
EDGE = REPO / "services" / "edge-worker"
IST = ZoneInfo("Asia/Kolkata")
PG = ["docker", "exec", "-i", "vigentra-postgres-1", "psql", "-U", "sentinel", "-d", "sentinel", "-At"]
REFUSED = "DESCRIBE failed: 401"
PROBE = ("import os, cv2\nfrom app import grid\ngrid._force_tcp_transport()\n"
         "cap = cv2.VideoCapture(grid.fallback_catalogue(os.environ.get('SENTINEL_GRID_BASE_URL', ''))['cam06'].rtsp_url, cv2.CAP_FFMPEG)\n"
         "ok, _ = cap.read() if cap.isOpened() else (False, None)\nprint('ACCEPTED' if ok else 'REFUSED')\ncap.release()")

# Reading time follows yield: the cameras whose plates are legible share a reader between few cameras,
# the wide overview cameras share one between many. Each group must sit in one department.
GROUPS = [
    ["cam06", "cam05", "cam16"],
    ["cam01", "cam02", "cam04", "cam07", "cam12", "cam13", "cam14", "cam15"],
    ["cam03", "cam17", "cam18", "cam19", "cam20", "cam21", "cam22"],
    ["cam08", "cam09", "cam10", "cam11", "cam23", "cam24", "cam25", "cam26", "cam27", "cam28", "cam29", "cam30"],
]


def log(msg: str) -> None:
    print(f"{dt.datetime.now(IST):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def dotenv() -> dict:
    out = {}
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def service_env(compose: dict, name: str, dot: dict) -> dict:
    """A compose service's environment with ${VAR:-default} resolved from .env (never printed)."""
    raw = compose["services"][name].get("environment") or {}
    if isinstance(raw, list):
        raw = dict(item.split("=", 1) for item in raw)

    def resolve(value) -> str:
        return re.sub(r"\$\{(\w+)(?::?-([^}]*))?\}", lambda m: dot.get(m.group(1)) or os.environ.get(m.group(1)) or (m.group(2) or ""), str(value))

    return {k: resolve(v) for k, v in raw.items() if v is not None}


def cameras() -> dict:
    rows = subprocess.run(PG + ["-F", "|", "-c", "SELECT external_camera_id, camera_id, source_system FROM cameras WHERE external_camera_id LIKE 'GRID-%'"],
                          capture_output=True, text=True, check=True).stdout.split()
    return {ext.replace("GRID-", ""): (cid, src) for ext, cid, src in (r.split("|") for r in rows)}


def reader_env(base: dict, account: dict) -> dict:
    env = dict(os.environ)
    env.update(base)
    env.update({k: account[k] for k in ("EDGE_USERNAME", "EDGE_PASSWORD") if k in account})
    env.update({
        "CENTRAL_API_URL": "http://localhost:8000",
        "ANPR_ENABLE": "true", "ANPR_DEVICE": "mps", "YOLO_DEVICE": "mps",
        "ANPR_MODELS_DIR": str(EDGE / "models"), "ANPR_CONFIG_DIR": str(EDGE / "config"),
        # the plain detector only runs if the ANPR engine faults; make that a real model, never the mock
        "YOLO_ENABLE": "true", "YOLO_ALLOW_DOWNLOAD": "false",
        "YOLO_WEIGHTS_DIR": str(EDGE / "models"), "YOLO_MODEL_NAME": "yolo11n.pt",
        "EDGE_CONTINUOUS": "true", "EDGE_CONTINUOUS_PASS_FRAMES": "1500",
        # unconfirmed readings above the review floor are kept too, flagged confirmed=false
        "ANPR_EMIT_UNCONFIRMED": "true", "ANPR_REVIEW_SCORE": "0.35",
        "KMP_DUPLICATE_LIB_OK": "TRUE", "PYTHONUNBUFFERED": "1", "LOG_LEVEL": "INFO",
    })
    return env


def grid_accepts(env: dict) -> bool:
    """One RTSP open on one camera, in a child process: does the grid accept our login right now?"""
    try:
        out = subprocess.run([sys.executable, "-c", PROBE], cwd=EDGE, env=env, capture_output=True, text=True, timeout=90).stdout
    except subprocess.TimeoutExpired:
        return False
    return "ACCEPTED" in out


def signal_all(readers: list, sig: int) -> None:
    for r in readers:
        if r["proc"] is not None and r["proc"].poll() is None:
            os.kill(r["proc"].pid, sig)


def progress(logs: Path) -> None:
    sql = ("SELECT string_agg(table_name || '=' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from camera_plates.%I', table_name), false, true, '')))[1]::text, ' ' ORDER BY table_name) "
           "FROM information_schema.tables WHERE table_schema = 'camera_plates' AND table_name LIKE 'cam%'")
    out = subprocess.run(PG + ["-c", sql], capture_output=True, text=True).stdout.strip()
    with open(logs / "progress.log", "a") as f:
        f.write(f"{dt.datetime.now(IST):%Y-%m-%d %H:%M} {out}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", required=True, help='deadline in IST, e.g. "2026-10-11 23:00"')
    ap.add_argument("--logs", default=str(Path.home() / "Library" / "Logs" / "vigentra-readers"))
    ap.add_argument("--restore-docker", nargs="*", default=[], help="containers to start again at the deadline")
    args = ap.parse_args()
    deadline = dt.datetime.strptime(args.until, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
    logs = Path(args.logs).expanduser()
    logs.mkdir(parents=True, exist_ok=True)

    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    dot = dotenv()
    base = service_env(compose, "edge-worker", dot)
    accounts = {"traffic_vms": base, "municipal_vms": service_env(compose, "detector-municipal", dot)}
    cams = cameras()

    readers = []
    for i, group in enumerate(GROUPS, 1):
        depts = {cams[c][1] for c in group}
        if len(depts) != 1:
            raise SystemExit(f"group {i} mixes departments: {sorted(depts)}")
        cmd = [sys.executable, "-m", "app.worker", "--forever", "--sample-interval", "2", "--cycle-seconds", "120"]
        for c in group:
            cmd += ["--camera", cams[c][0]]
        readers.append({"name": f"reader{i}", "cameras": group, "cmd": cmd, "env": reader_env(base, accounts[depts.pop()]),
                        "proc": None, "next_start": 0.0, "starts": [], "log": logs / f"reader{i}.log"})

    caffeinate = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(f"collecting until {deadline:%d %b %H:%M} IST with {len(readers)} readers; logs in {logs}")
    last_progress = 0.0
    offsets = {r["name"]: (r["log"].stat().st_size if r["log"].exists() else 0) for r in readers}
    paused_until, backoff = None, 180
    probe_env = readers[0]["env"]
    while not stopping and dt.datetime.now(IST) < deadline:
        now = time.time()
        if paused_until is not None:
            if now >= paused_until:
                if grid_accepts(probe_env):
                    for r in readers:
                        offsets[r["name"]] = r["log"].stat().st_size if r["log"].exists() else 0
                    signal_all(readers, signal.SIGCONT)
                    log("grid accepting our login again: readers resumed")
                    paused_until, backoff = None, 180
                else:
                    paused_until = now + backoff
                    log(f"grid still refusing: next test login in {backoff // 60} min")
                    backoff = min(backoff * 2, 900)
            time.sleep(30)
            continue
        refusals = 0
        for r in readers:
            if r["log"].exists():
                with open(r["log"], "rb") as f:
                    f.seek(offsets[r["name"]])
                    refusals += f.read().decode(errors="ignore").count(REFUSED)
                    offsets[r["name"]] = f.tell()
        if refusals >= 4:
            signal_all(readers, signal.SIGSTOP)
            paused_until = now + backoff
            log(f"grid refusing our login ({refusals} x RTSP 401 in 30 s): readers frozen, test login in {backoff // 60} min")
            backoff = min(backoff * 2, 900)
            continue
        for r in readers:
            p = r["proc"]
            if p is not None and p.poll() is not None:
                log(f"{r['name']} exited with {p.returncode}")
                r["proc"] = None
                recent = [t for t in r["starts"] if now - t < 600]
                r["next_start"] = now + (600 if len(recent) >= 3 else 60)
            if r["proc"] is None and now >= r["next_start"]:
                r["proc"] = subprocess.Popen(r["cmd"], cwd=EDGE, env=r["env"], stdout=open(r["log"], "a"), stderr=subprocess.STDOUT)
                r["starts"].append(now)
                log(f"{r['name']} started (pid {r['proc'].pid}): {' '.join(r['cameras'])}")
        if now - last_progress >= 3600:
            progress(logs)
            last_progress = now
        (logs / "status.json").write_text(json.dumps({r["name"]: {"pid": r["proc"].pid if r["proc"] else None, "cameras": r["cameras"],
                                                                  "starts": len(r["starts"])} for r in readers}, indent=1))
        time.sleep(30)

    log("deadline reached" if not stopping else "stop requested")
    signal_all(readers, signal.SIGCONT)
    for r in readers:
        if r["proc"] and r["proc"].poll() is None:
            r["proc"].terminate()
    time.sleep(15)
    for r in readers:
        if r["proc"] and r["proc"].poll() is None:
            r["proc"].kill()
    progress(logs)
    if not stopping and args.restore_docker:
        subprocess.run(["docker", "start", *args.restore_docker])
        log(f"started again: {' '.join(args.restore_docker)}")
    caffeinate.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
