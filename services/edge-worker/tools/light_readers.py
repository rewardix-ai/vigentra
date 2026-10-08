"""Light mode: read every grid camera at once, live, from one process on one Mac.

The event test needs all cameras watched continuously for hours: a designated vehicle can pass any of
them at any time, and a stream cannot be rewound. One full reader per camera does not fit on one Mac
(one 720p camera is about one M1's worth of the full engine), so this trades per-frame cost for
coverage:

- one process, one thread per camera, each running the worker's own pass (`app.worker.run`): the same
  video-session request, capture rules, engine, uploads and incidents as every other reader;
- one copy of each model's weights for the whole process (each camera keeps its own tracker), and one
  lock around the engine, because PyTorch's Apple-GPU backend crashes when called from several threads;
- vehicles detected at 640 px with YOLO11n: a vehicle near enough to carry a readable plate is still
  well over 30 px wide at that size, and the plate itself is read from the full-resolution frame;
- all three readers, with one shared copy of the PaddleOCR text reader for every camera (it keeps no
  state, and a copy per camera would not fit): it is the reader that recovers the hardest plates, the
  night ones included;
- attention: the GPU goes first to cameras that just showed a vehicle near enough to carry a readable
  plate (at least 96 px wide, the engine's own plate-search floor), so that vehicle gets frame after frame
  while it is in view; a camera with a vehicle approaching (48 px) is checked every second, and every
  other camera at least every 6 s (COLD_SECONDS).
  A vehicle needs several agreeing frames to be confirmed, and most cameras most of the time show nothing
  near, so this puts the frames where plates can actually be read;
- each camera's pace adapts on its own (`AdaptiveSampler`): busy GPU, wider frame stride.

Traffic Police cameras sign in as traffic.ai, Municipal Corporation cameras as municipal.ai, as the
access model requires. Run it under scripts/collect_plates.py --mode light, which supplies the
environment (credentials from .env, never printed) and handles the grid's refusal windows.

    python tools/light_readers.py cam01 cam02 ...      (grid ids; default: every grid camera)
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent.parent
NEAR_PX = 96          # a vehicle this wide (frame px) can carry a readable plate: its camera turns hot
HOT_SECONDS = 4.0     # a camera stays hot this long after its last near vehicle
WARM_PX = 48          # a vehicle this wide is approaching: its camera is checked every WARM_SECONDS
WARM_SECONDS = 1.0
COLD_SECONDS = 6.0    # a quiet camera waits at most this long for its next frame (when the GPU allows).
                      # A quiet frame costs ~45 ms of engine time: thirty cameras every 3 s took half the GPU.


class Attention:
    """One GPU, many cameras: hot cameras first, quiet cameras once their wait passes COLD_SECONDS,
    first come first served within each."""

    def __init__(self) -> None:
        self.cv = threading.Condition()
        self.busy = False
        self.waiting: list[tuple[float, str]] = []   # (arrival, camera)
        self.hot_until: dict[str, float] = {}
        self.warm_until: dict[str, float] = {}
        self.last: dict[str, float] = {}

    def _rank(self, entry, now):
        arrival, cam = entry
        waited = now - self.last.get(cam, 0)
        urgent = (self.hot_until.get(cam, 0) > now
                  or (self.warm_until.get(cam, 0) > now and waited > WARM_SECONDS)
                  or waited > COLD_SECONDS)
        return (0 if urgent else 1, arrival)

    def acquire(self, cam: str) -> None:
        with self.cv:
            entry = (time.monotonic(), cam)
            self.waiting.append(entry)
            while self.busy or min(self.waiting, key=lambda e: self._rank(e, time.monotonic())) != entry:
                self.cv.wait(0.05)
            self.waiting.remove(entry)
            self.busy = True

    def release(self, cam: str, near: bool | None, approaching: bool = False) -> None:
        with self.cv:
            now = time.monotonic()
            self.last[cam] = now
            if near:
                self.hot_until[cam] = now + HOT_SECONDS
            if approaching:
                self.warm_until[cam] = now + HOT_SECONDS
            self.busy = False
            self.cv.notify_all()

    def hot(self) -> list[str]:
        now = time.monotonic()
        return [c for c, t in self.hot_until.items() if t > now]
sys.path.insert(0, str(HERE))
log = logging.getLogger("vigentra.edge.light")


def light_config() -> Path:
    """The engine's config with the light settings, written beside the real one at start-up."""
    src = Path(os.environ.get("ANPR_CONFIG_DIR", HERE / "config"))
    dst = Path.home() / "Library" / "Caches" / "vigentra" / "light-config"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    path = dst / "thresholds.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["detector"]["vehicle_imgsz"] = int(os.environ.get("LIGHT_VEHICLE_IMGSZ", "640"))
    # people too (same model, same pass): crowd gathering and person-in-traffic incidents need them;
    # the pipeline skips plate search for them (anpr/pipeline.py)
    if os.environ.get("LIGHT_DETECT_PEOPLE", "true").lower() == "true":
        cfg["detector"]["vehicle_classes"] = sorted(set(cfg["detector"]["vehicle_classes"]) | {0})
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return dst


def share_model_weights() -> None:
    """Every YOLO built with the same weights file reuses the first one's network; its predictor, and
    so its tracker state, stays its own."""
    import ultralytics

    base = ultralytics.YOLO
    networks: dict[str, object] = {}
    guard = threading.Lock()

    class SharedYOLO(base):  # type: ignore[misc, valid-type]
        def __init__(self, model="yolo11n.pt", task=None, verbose=False):
            super().__init__(model, task=task, verbose=verbose)
            with guard:
                key = str(Path(str(model)).resolve())
                if key in networks:
                    self.model = networks[key]
                else:
                    networks[key] = self.model

    ultralytics.YOLO = SharedYOLO


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s")
    os.environ["ANPR_CONFIG_DIR"] = str(light_config())
    os.environ.setdefault("ANPR_VEHICLE_WEIGHTS", "yolo11n.pt")
    # the plate detector fine-tuned on grid footage (research repo, data/det/grid_clean): on 53 near
    # vehicles the default found no plate on, it boxed the yellow plates of autos and trucks that the
    # default misses outright (8 Oct, cam06 GJ18X..., 0.80-0.83)
    os.environ.setdefault("ANPR_PLATE_WEIGHTS", "plate_det_grid_clean.pt")
    os.environ.setdefault("EDGE_CONTINUOUS", "true")
    # learned traffic directions survive the restart at every grid refusal window (anpr/incidents.py)
    os.environ.setdefault("INCIDENT_FLOW_DIR", str(Path.home() / "Library" / "Caches" / "vigentra" / "incident-flow"))
    os.environ.setdefault("EDGE_CONTINUOUS_PASS_FRAMES", "100000")
    share_model_weights()
    from anpr.read import awiros

    text_reader: dict = {}
    original_reader = awiros.AwirosReader

    def shared_reader(*a, **k):   # one PaddleOCR text reader for all cameras
        if "one" not in text_reader:
            text_reader["one"] = original_reader(*a, **k)
        return text_reader["one"]

    awiros.AwirosReader = shared_reader

    from app import anpr_engine, grid, worker
    from anpr.pipeline import ANPRPipeline

    # Why vehicles end without a plate: every closed track's outcome, tallied per minute in the log.
    # Status plus the reason's kind ("width_below_gate:14<22" counts as width_below_gate).
    outcomes: dict[str, int] = {}
    finalise = ANPRPipeline._finalise_track

    def counted(self, bank, _finalise=finalise):
        before = len(self.records)
        _finalise(self, bank)
        if len(self.records) > before:
            rec = self.records[-1]
            key = rec.get("status", "?") if rec.get("status") == "CONFIRMED" else \
                f"{rec.get('status', '?')}:{str(rec.get('reason') or '-').split(':')[0]}"
            outcomes[key] = outcomes.get(key, 0) + 1

    ANPRPipeline._finalise_track = counted

    # Diagnostics (LIGHT_DIAG_DIR): a big vehicle with no plate box is saved (one per camera per 20 s,
    # 80 at most) with what the plate detector proposed and why each proposal was dropped.
    diag_dir = os.environ.get("LIGHT_DIAG_DIR")   # opt-in: nothing from the grid is written to disk otherwise
    rejected: dict[str, int] = {}
    if diag_dir:
        import cv2
        from anpr.detect.plate import PlateDetector
        Path(diag_dir).mkdir(parents=True, exist_ok=True)
        detect_in_vehicle = PlateDetector.detect_in_vehicle
        last_saved: dict[str, float] = {}
        saved = [0]

        def watched(self, frame, box, vtype="car", frame_idx=-1, track_id="", _orig=detect_in_vehicle):
            n = len(self.rejection_log)
            out = _orig(self, frame, box, vtype, frame_idx, track_id)
            for r in self.rejection_log[n:]:
                for why in r.get("reasons", []):
                    k = why.split(":")[0]
                    rejected[k] = rejected.get(k, 0) + 1
            cam = threading.current_thread().name
            w = box[2] - box[0]
            if not out and w >= 180 and saved[0] < 80 and time.time() - last_saved.get(cam, 0) > 20:
                last_saved[cam] = time.time()
                saved[0] += 1
                x1, y1, x2, y2 = (int(max(0, v)) for v in box)
                props = [f"{r.get('src')}:{r.get('raw_conf', 0):.2f}x{r.get('geom_prior', 0):.2f}:{'|'.join(r.get('reasons', []))}"
                         for r in self.rejection_log[n:]]
                name = f"{cam}_{int(time.time())}_{vtype}_{int(w)}px"
                cv2.imwrite(f"{diag_dir}/{name}.jpg", frame[y1:y2, x1:x2])
                Path(f"{diag_dir}/{name}.txt").write_text("\n".join(props) or "no proposals")
            return out

        PlateDetector.detect_in_vehicle = watched

    # With thirty streams on one machine the gateway re-sends a few seconds of buffered video now and then:
    # PTS steps back ~6 s. The capture took any step back over 5 s for a scene cut and reset tracking,
    # losing every plate vote in progress (cam25: 20 resets in 15 min). A real loop lands near zero and is
    # still caught by LOOP_RESTART_MS; only steps back over 30 s count as a cut otherwise.
    grid.ReconnectingCapture.LOOP_TOLERANCE_MS = 30000.0

    gpu = Attention()

    # Training harvest (LIGHT_HARVEST_DIR, opt-in): frames with a vehicle near enough to carry a readable
    # plate, at most one per camera every HARVEST_SECONDS and HARVEST_MAX in all, each with the vehicles and
    # plate boxes the engine saw. For fine-tuning only (research repo); never searched for a vehicle.
    harvest_dir = os.environ.get("LIGHT_HARVEST_DIR")
    harvest = {"last": {}, "n": len(list(Path(harvest_dir).glob("*.jpg"))) if harvest_dir and Path(harvest_dir).exists() else 0}
    HARVEST_SECONDS, HARVEST_MAX, HARVEST_PX = 10.0, 8000, 120

    def keep_for_training(engine, frame, dets) -> None:
        import cv2
        import json
        cam = names.get(engine.camera_id, engine.camera_id)
        now = time.time()
        if (harvest["n"] >= HARVEST_MAX or now - harvest["last"].get(cam, 0) < HARVEST_SECONDS
                or not any(d.bbox_xyxy[2] - d.bbox_xyxy[0] >= HARVEST_PX for d in dets)):
            return
        harvest["last"][cam] = now
        harvest["n"] += 1
        Path(harvest_dir).mkdir(parents=True, exist_ok=True)
        stem = f"{harvest_dir}/{cam}_{int(now * 1000)}"
        cv2.imwrite(stem + ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        plates = getattr(getattr(engine, "_pipeline", None), "last_plates", None) or []
        Path(stem + ".json").write_text(json.dumps({
            "camera": cam, "wall": now,
            "vehicles": [{"cls": d.class_name, "conf": d.confidence, "box": d.bbox_xyxy,
                          "track": (d.extra or {}).get("track_id")} for d in dets],
            "plates": [{"track": str(t), "box": [round(float(v), 1) for v in b], "src": src, "conf": round(float(c), 3)}
                       for t, b, src, c in plates]}))
    frames: dict[str, int] = {}   # frames each camera got through the engine since the last stats line
    spent = {"hot": [0.0, 0], "cold": [0.0, 0]}   # engine seconds and frames, by whether a near vehicle was in view
    for name in ("process", "finish"):
        original = getattr(anpr_engine.AnprEngine, name)

        def locked(self, *a, _original=original, _count=(name == "process"), **k):
            gpu.acquire(self.camera_id)
            near, approaching, keep = None, False, None
            try:
                t0 = time.perf_counter()
                out = _original(self, *a, **k)
                if _count:
                    frames[self.camera_id] = frames.get(self.camera_id, 0) + 1
                    dets = out[0] if isinstance(out, tuple) else []
                    widths = [d.bbox_xyxy[2] - d.bbox_xyxy[0] for d in dets if d.class_name != "person"]
                    # hot only when a plate box was found: most near vehicles show no plate at all
                    # (side-on, headlight glare; 132 of 137 closed tracks on 8 Oct had none), and
                    # spending frames on them starved the vehicles whose plates were in view
                    near = bool(getattr(getattr(self, "_pipeline", None), "last_plates", None))
                    approaching = any(w >= WARM_PX for w in widths)
                    if harvest_dir and a:
                        keep = (a[0], dets)
                    bucket = spent["hot" if near else "cold"]
                    bucket[0] += time.perf_counter() - t0
                    bucket[1] += 1
                return out
            finally:
                gpu.release(self.camera_id, near, approaching)
                if keep is not None:   # written after the GPU is free
                    try:
                        keep_for_training(self, *keep)
                    except Exception as exc:
                        log.warning("harvest failed: %s", exc)

        setattr(anpr_engine.AnprEngine, name, locked)
    building = threading.Lock()
    # every camera keeps its engine (its tracks and plate votes) for the whole run; the default cache
    # holds 4 and evicted the rest, losing every vote in progress. Weights are shared, so 30 is affordable.
    cache = anpr_engine.EngineCache(capacity=64)
    cache_get = cache.get
    cache.get = lambda *a, **k: _with(building, cache_get, *a, **k)  # engines are built one at a time

    # each thread signs in as its camera's department
    accounts = {
        "traffic_vms": (os.environ.get("EDGE_USERNAME", "traffic.ai"), os.environ.get("EDGE_PASSWORD", "")),
        "municipal_vms": (os.environ.get("LIGHT_MUNICIPAL_USERNAME", "municipal.ai"), os.environ.get("LIGHT_MUNICIPAL_PASSWORD", "")),
    }
    local = threading.local()
    sign_in = worker.CentralClient.sign_in
    worker.CentralClient.sign_in = lambda self, _u, _p: sign_in(self, *accounts[getattr(local, "dept", "traffic_vms")])

    detector = worker.build_detector()
    detect = detector.detect

    def detect_locked(*a, **k):
        gpu.acquire("plain-detector")
        try:
            return detect(*a, **k)
        finally:
            gpu.release("plain-detector", None)

    detector.detect = detect_locked

    discovery = worker.CentralClient()
    discovery.sign_in(*accounts["traffic_vms"])
    registry = {c["external_camera_id"]: c for c in discovery.list_cameras() if str(c.get("external_camera_id", "")).startswith("GRID-")}
    wanted = [f"GRID-{c}" for c in sys.argv[1:]] or sorted(registry)
    names = {c["camera_id"]: ext.replace("GRID-", "") for ext, c in registry.items()}
    incidents: dict = {}
    stop = threading.Event()

    def camera_loop(ext: str) -> None:
        cam = registry[ext]
        local.dept = cam.get("source_system", "traffic_vms")
        backoff = 5
        while not stop.is_set():
            try:
                worker.run(camera_id=cam["camera_id"], clip=None, max_frames=0, sample_interval=2, dry_run=False,
                           synthetic=False, source_mode="authorized_edge", detector=detector,
                           external_camera_id=ext, anpr_cache=cache, incidents_by_camera=incidents)
                backoff = 5
            except Exception as exc:  # one camera failing never stops the others
                log.warning("%s pass failed: %s", ext, exc)
                backoff = min(backoff * 2, 120)
            stop.wait(backoff)

    threads = []
    for i, ext in enumerate(w for w in wanted if w in registry):
        t = threading.Thread(target=camera_loop, args=(ext,), name=ext, daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1.5)  # stagger the openings: thirty sessions at once is a burst the gateway notices
    log.info("light mode: %d cameras in one process", len(threads))
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(60)
            counts, total = dict(frames), sum(frames.values())
            frames.clear()
            idle = [names[c["camera_id"]] for e, c in registry.items() if e in wanted and c["camera_id"] not in counts]
            ms = {k: (round(1000 * v[0] / v[1]) if v[1] else 0) for k, v in spent.items()}
            spent.update(hot=[0.0, 0], cold=[0.0, 0])
            log.info("engine ms/frame: near %d, quiet %d", ms["hot"], ms["cold"])
            tally, outcomes_total = dict(outcomes), sum(outcomes.values())
            outcomes.clear()
            if rejected:
                log.info("plate proposals dropped: %s", " ".join(f"{k}={v}" for k, v in sorted(rejected.items(), key=lambda kv: -kv[1])))
                rejected.clear()
            log.info("tracks closed %d | %s", outcomes_total,
                     " ".join(f"{k}={v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1])) or "none")
            log.info("frames/min %d total | hot now: %s | %s | idle: %s", total, " ".join(sorted(names.get(c, c) for c in gpu.hot())) or "none",
                     " ".join(f"{names.get(k, k)}={v}" for k, v in sorted(counts.items(), key=lambda kv: names.get(kv[0], kv[0]))),
                     " ".join(idle) or "none")
    except KeyboardInterrupt:
        stop.set()
    return 0


def _with(lock, fn, *a, **k):
    with lock:
        return fn(*a, **k)


if __name__ == "__main__":
    raise SystemExit(main())
