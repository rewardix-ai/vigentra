"""Read plates on every grid camera with the Mac's GPU until a deadline, and keep the readers alive.

Runs continuous ANPR readers (one camera each, rotating) (`app.worker --forever` with long passes),
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
- the grid refuses our login while its server restarts (RTSP 401, ~20 min), after which every
  recording plays again from its start. The readers are stopped while refused, one test login is
  made a minute, and each restart opens a window: each reader takes the next camera in ROTATION from
  the first minute of the replay, and that camera's on-screen clock is sampled (scripts/osd/clock.py);
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

# The grid replays the same opening minutes of every recording after each server restart, so each
# window gives every reader one camera from the restart on, rotating through all 30. Cameras on the
# shared 13 June 21:00 recording get two turns per rotation: only cameras showing the same moment can
# share a vehicle, and their video times line up across windows.
SAME_EVENING = ["cam04", "cam13", "cam15", "cam01", "cam02", "cam05", "cam14", "cam07",
                "cam08", "cam09", "cam10", "cam11", "cam28", "cam29"]
OTHERS = ["cam06", "cam17", "cam18", "cam16", "cam30", "cam03", "cam12", "cam19", "cam20",
          "cam21", "cam22", "cam23", "cam24", "cam25", "cam26", "cam27"]
ROTATION = SAME_EVENING + OTHERS[:8] + SAME_EVENING + OTHERS[8:]
READERS = 3          # a fourth pushed a 16 GB Mac into swap with the stack running
PROBE_SECONDS = 20   # one test login every 20 s while refused: a restart is caught within 20 s


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
    envs = {"traffic_vms": reader_env(base, base), "municipal_vms": reader_env(base, service_env(compose, "detector-municipal", dot))}
    for env in envs.values():
        env["EDGE_CONTINUOUS_PASS_FRAMES"] = "1000000"   # one pass per window: read the whole replay
    cams = cameras()
    assert set(ROTATION) == set(cams), "the rotation must cover exactly the grid cameras"
    slots = [{"name": f"reader{i + 1}", "camera": None, "proc": None, "log": logs / f"reader{i + 1}.log", "restart_at": None} for i in range(READERS)]
    state = {"next": 0}

    def launch(slot, camera):
        cmd = [sys.executable, "-m", "app.worker", "--forever", "--sample-interval", "2", "--cycle-seconds", "120", "--camera", cams[camera][0]]
        slot["camera"], slot["restart_at"] = camera, None
        slot["proc"] = subprocess.Popen(cmd, cwd=EDGE, env=envs[cams[camera][1]], stdout=open(slot["log"], "a"), stderr=subprocess.STDOUT)

    def stop_all():
        for slot in slots:
            if slot["proc"] and slot["proc"].poll() is None:
                slot["proc"].terminate()
        time.sleep(10)
        for slot in slots:
            if slot["proc"] and slot["proc"].poll() is None:
                slot["proc"].kill()
            slot["proc"] = None

    def new_window(restarted: bool):
        chosen = []
        while len(chosen) < READERS:
            cam = ROTATION[state["next"] % len(ROTATION)]
            state["next"] += 1
            if cam not in chosen:
                chosen.append(cam)
        if restarted:
            subprocess.run(PG + ["-c", f"INSERT INTO camera_plates.grid_restarts VALUES ('{dt.datetime.now(IST):%Y-%m-%d %H:%M:%S}') ON CONFLICT DO NOTHING"], capture_output=True)
        # read the chosen cameras' clocks first: the grid gives one stream per camera, so a clock read
        # while a reader holds the camera gets nothing. In parallel, at most 30 s of the replay.
        clocks = [subprocess.Popen([sys.executable, str(REPO / "scripts" / "osd" / "clock.py"), cam], cwd=REPO,
                                   stdout=open(logs / "clock.log", "a"), stderr=subprocess.STDOUT) for cam in chosen]
        t0 = time.time()
        while time.time() - t0 < 30 and any(c.poll() is None for c in clocks):
            time.sleep(1)
        for c in clocks:
            if c.poll() is None:
                c.kill()
        for slot, cam in zip(slots, chosen):
            launch(slot, cam)
        log(("restart: " if restarted else "") + "reading " + " ".join(chosen))

    caffeinate = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(f"collecting until {deadline:%d %b %H:%M} IST, {READERS} readers rotating over {len(set(ROTATION))} cameras; logs in {logs}")
    probe_env = envs["traffic_vms"]
    refused = not grid_accepts(probe_env)
    if refused:
        log("grid refusing our login: waiting for the next restart")
    else:
        new_window(restarted=False)
    offsets = {}
    last_probe = last_progress = 0.0
    while not stopping and dt.datetime.now(IST) < deadline:
        now = time.time()
        if refused:
            if now - last_probe >= PROBE_SECONDS:
                last_probe = now
                if grid_accepts(probe_env):
                    refused = False
                    offsets = {s_["name"]: (s_["log"].stat().st_size if s_["log"].exists() else 0) for s_ in slots}
                    new_window(restarted=True)
        else:
            refusals = 0
            for slot in slots:
                if slot["log"].exists():
                    with open(slot["log"], "rb") as f:
                        f.seek(offsets.get(slot["name"], 0))
                        refusals += f.read().decode(errors="ignore").count(REFUSED)
                        offsets[slot["name"]] = f.tell()
            if refusals >= 4:
                log(f"grid refusing our login ({refusals} x RTSP 401): server restarting; readers stopped")
                stop_all()
                refused, last_probe = True, now
            else:
                for slot in slots:
                    p = slot["proc"]
                    if p is not None and p.poll() is not None and slot["restart_at"] is None:
                        log(f"{slot['name']} ({slot['camera']}) exited with {p.returncode}; restarting in 30 s")
                        slot["restart_at"] = now + 30
                    if slot["restart_at"] is not None and now >= slot["restart_at"]:
                        launch(slot, slot["camera"])
        if now - last_progress >= 3600:
            progress(logs)
            last_progress = now
        (logs / "status.json").write_text(json.dumps({"refused": refused, "readers": {s_["name"]: {"camera": s_["camera"], "pid": s_["proc"].pid if s_["proc"] else None} for s_ in slots}}, indent=1))
        time.sleep(15)

    log("deadline reached" if not stopping else "stop requested")
    stop_all()
    progress(logs)
    if not stopping and args.restore_docker:
        subprocess.run(["docker", "start", *args.restore_docker])
        log(f"started again: {' '.join(args.restore_docker)}")
    caffeinate.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
