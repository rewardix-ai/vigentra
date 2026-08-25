"""Sentinel edge analytics worker.

Runs *inside* the department environment, next to the video it is authorized to
process. It:

  1. signs in to the central API with an `ai_operator` account,
  2. opens an authorized video session for one camera (or reads a local demo
     clip directly),
  3. samples every Nth frame,
  4. routes each frame through the quality classifier,
  5. runs the detector,
  6. POSTs detection METADATA to the central API.

Raw frames never leave this process. What crosses the wire is a class name, a
confidence, a box, a timestamp and provenance.

Usage:

    python -m app.worker --camera SENTINEL-TRAFFIC-AHM-0001 --max-frames 60
    python -m app.worker --clip /app/videos/traffic_01.mp4 --camera ... --dry-run

Environment: see docs/yolo-setup.md.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from .detectors import Detection, DetectorError, build_detector
from .frame_quality import FrameQuality, FrameQualityRouter
from .plates import (
    PLATE_BEARING_CLASSES,
    DisabledPlateReader,
    PlateReadUnavailable,
    build_plate_reader,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    # stdout, not the default stderr: for this tool the log *is* the output,
    # and PowerShell paints anything on stderr red and wraps it in an
    # ErrorRecord - so a healthy run looks like a crash.
    stream=sys.stdout,
)
logger = logging.getLogger("sentinel.edge.worker")

CENTRAL_API_URL = os.getenv("CENTRAL_API_URL", "http://central-api:8000")
EDGE_USERNAME = os.getenv("EDGE_USERNAME", "ai.operator")
EDGE_PASSWORD = os.getenv("EDGE_PASSWORD", "AiOps@2026")
FRAME_SAMPLE_INTERVAL = int(os.getenv("YOLO_FRAME_SAMPLE_INTERVAL", "5"))
BATCH_SIZE = int(os.getenv("EDGE_BATCH_SIZE", "50"))


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------

def iter_clip_frames(path: str, sample_interval: int) -> Iterator[tuple[int, Any]]:
    """Yield (frame_index, frame) from a local clip, sampling every Nth frame.

    Sampling matters: a 25 fps feed produces 90,000 frames an hour, and running
    every one of them buys almost nothing for traffic counting while costing
    25x the compute.
    """
    try:
        import cv2
    except ImportError as exc:
        raise DetectorError(
            "OpenCV is required to read video clips. Install the analytics "
            "extras (services/edge-worker/requirements-yolo.txt), or run with "
            "--synthetic to exercise the pipeline without decoding video."
        ) from exc

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise DetectorError(f"Could not open video source '{path}'.")

    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % max(1, sample_interval) == 0:
                yield index, frame
            index += 1
    finally:
        capture.release()


def iter_synthetic_frames(count: int, sample_interval: int) -> Iterator[tuple[int, Any]]:
    """Frames without any CV stack, for wiring tests and offline demos.

    Produces alternating bright/dark/mid frames so the quality router has
    something real to classify.
    """
    try:
        import numpy as np

        def make(brightness: int):
            frame = np.full((540, 960, 3), brightness, dtype="uint8")
            # A little structure so the blur measure is not degenerate.
            frame[100:200, 100:400] = min(255, brightness + 60)
            frame[300:420, 500:800] = max(0, brightness - 50)
            return frame
    except ImportError:
        def make(brightness: int):
            return [[brightness] * 32 for _ in range(18)]

    palette = [128, 30, 220, 128]
    for index in range(count):
        if index % max(1, sample_interval) == 0:
            yield index, make(palette[(index // max(1, sample_interval)) % len(palette)])


def iter_session_frames(
    client: "CentralClient", session: dict, sample_interval: int
) -> Iterator[tuple[int, Any]]:
    """Decode the feed behind an authorised session.

    This is the live path. It goes through the broker like any other viewer -
    same permission checks, same audit entry, same opaque URL - so an edge
    worker can never reach footage a human operator could not.
    """
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.close()
    try:
        client.fetch_stream(session["stream_url"], handle.name)
        yield from iter_clip_frames(handle.name, sample_interval)
    finally:
        try:
            os.unlink(handle.name)
        except OSError:  # pragma: no cover - best effort
            pass


# ---------------------------------------------------------------------------
# Central API client
# ---------------------------------------------------------------------------

class CentralClient:
    """Thin authenticated client for the ingest endpoints."""

    def __init__(self, base_url: str = CENTRAL_API_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=20.0)
        self._token: str | None = None

    def sign_in(self, username: str, password: str) -> dict:
        response = self._client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        logger.info(
            "signed in as %s (role=%s)", body["user"]["username"], body["user"]["role"]
        )
        return body["user"]

    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise DetectorError("Not signed in to the central API.")
        return {"Authorization": f"Bearer {self._token}"}

    def open_video_session(self, camera_id: str, reason: str) -> dict:
        """Open an authorized session.

        The worker is subject to exactly the same authorisation as a human
        operator: an `ai_operator` outside the camera's department or city is
        refused, and the refusal is audited.
        """
        response = self._client.post(
            "/api/v1/video-sessions",
            headers=self._headers(),
            json={"camera_id": camera_id, "mode": "live", "reason": reason},
        )
        if response.status_code == 403:
            raise DetectorError(
                f"Video access denied for '{camera_id}': "
                f"{response.json().get('detail', {}).get('message')}"
            )
        response.raise_for_status()
        return response.json()

    def close_video_session(self, session_id: str) -> None:
        try:
            self._client.delete(
                f"/api/v1/video-sessions/{session_id}", headers=self._headers()
            )
        except Exception as exc:  # pragma: no cover - best effort on shutdown
            logger.warning("could not revoke session %s: %s", session_id, exc)

    def list_cameras(self) -> list[dict]:
        """Every camera this worker's account may read.

        Used by --all-cameras so a site does not have to hard-code IDs that
        the registry already knows.
        """
        response = self._client.get("/api/v1/cameras", headers=self._headers())
        response.raise_for_status()
        body = response.json()
        return body.get("items", body) if isinstance(body, dict) else body

    def fetch_stream(self, stream_url: str, destination: str) -> None:
        """Pull the brokered feed to a local file for decoding.

        The stream is authenticated, and OpenCV cannot attach a bearer token to
        its own HTTP fetch, so the worker downloads through its authenticated
        client and hands OpenCV a local path. For a real RTSP/HLS camera this
        becomes a direct capture and the rest of the loop is unchanged.
        """
        with self._client.stream("GET", stream_url, headers=self._headers()) as response:
            response.raise_for_status()
            with open(destination, "wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1 << 16):
                    handle.write(chunk)

    def ingest(self, detections: list[dict]) -> dict:
        response = self._client.post(
            "/api/v1/detections/ingest",
            headers=self._headers(),
            json={"detections": detections},
        )
        response.raise_for_status()
        return response.json()

    def report_frame_quality(self, camera_id: str, assessment: dict) -> None:
        try:
            self._client.post(
                "/api/v1/detections/frame-quality",
                headers=self._headers(),
                json={"camera_id": camera_id, **assessment},
            )
        except Exception as exc:
            logger.debug("frame-quality report failed (non-fatal): %s", exc)

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(
    *,
    camera_id: str,
    clip: str | None,
    max_frames: int,
    sample_interval: int,
    dry_run: bool,
    synthetic: bool,
    source_mode: str,
    detector: Any = None,
) -> int:
    # Loading weights costs seconds and hundreds of megabytes. A supervisor
    # covering several cameras builds the detector once and passes it in;
    # a one-shot run builds its own.
    built_here = detector is None
    detector = detector or build_detector()
    router = FrameQualityRouter()

    # ANPR is off unless ANPR_ENABLE is set. A DisabledPlateReader is returned
    # otherwise, so the loop below needs no special case.
    plate_reader = build_plate_reader()
    plates_read = 0

    # The supervisor already logged this once for the whole process; repeating
    # it per camera per cycle would bury the actual results.
    if built_here:
        logger.info("detector: %s", detector.describe())

    client: CentralClient | None = None
    session: dict | None = None
    if not dry_run:
        client = CentralClient()
        client.sign_in(EDGE_USERNAME, EDGE_PASSWORD)
        if not clip and not synthetic:
            session = client.open_video_session(
                camera_id, reason="Authorized edge analytics inference"
            )
            logger.info(
                "opened session %s (expires in %ss)",
                session["session_id"], session["expires_in_seconds"],
            )

    # Precedence is explicit: --synthetic beats everything (it exists to run
    # without a CV stack), then a local clip, then the brokered live session.
    # Falling through to synthetic frames while holding a real session is what
    # made "live" detections meaningless before.
    if synthetic:
        frames = iter_synthetic_frames(max_frames * max(1, sample_interval), sample_interval)
    elif clip:
        frames = iter_clip_frames(clip, sample_interval)
    elif session and client:
        frames = iter_session_frames(client, session, sample_interval)
    else:
        raise DetectorError(
            "No frame source: pass --clip, or allow a live session, or use "
            "--synthetic to exercise the pipeline without video."
        )

    pending: list[dict] = []
    processed = skipped = produced = 0
    started = time.perf_counter()

    try:
        for frame_index, frame in frames:
            if processed >= max_frames:
                break
            processed += 1

            frame_to_detect, assessment = router.route(frame)

            if assessment.quality is not FrameQuality.NORMAL and client:
                client.report_frame_quality(camera_id, assessment.as_dict())

            if assessment.inference_skipped:
                # Too degraded to mean anything. Recorded, not guessed at.
                skipped += 1
                logger.info(
                    "frame %s skipped: %s (laplacian variance %.1f)",
                    frame_index, assessment.quality.value,
                    assessment.laplacian_variance or 0.0,
                )
                continue

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            detections: list[Detection] = detector.detect(frame_to_detect)

            for position, detection in enumerate(detections):
                detection.frame_quality = assessment.quality.value

                # Only vehicles, and only on the frame the detector actually
                # saw. A person is never cropped or read.
                plate = None
                if detection.class_name in PLATE_BEARING_CLASSES:
                    try:
                        plate = plate_reader.read(frame_to_detect, detection.bbox_xyxy)
                    except PlateReadUnavailable as exc:
                        # Say it once, then carry on producing detections. ANPR
                        # failing is not a reason to stop counting vehicles.
                        logger.error("ANPR unavailable, continuing without plates: %s", exc)
                        plate_reader = DisabledPlateReader()
                    except Exception as exc:  # pragma: no cover - engine fault
                        logger.warning("plate read failed on one vehicle: %s", exc)
                if plate is not None:
                    plates_read += 1
                pending.append(
                    detection.to_payload(
                        camera_id,
                        timestamp,
                        position,
                        source_mode=source_mode,
                        is_demo_data=source_mode != "authorized_edge",
                        plate_text=plate.text if plate else None,
                        plate_confidence=plate.confidence if plate else None,
                        plate_bbox_xyxy=plate.bbox_xyxy if plate else None,
                        plate_reader=plate.reader_version if plate else None,
                        provenance={
                            "frame_index": frame_index,
                            "frame_quality": assessment.quality.value,
                            "enhancement_applied": assessment.enhancement_applied,
                            "sample_interval": sample_interval,
                            "worker": "sentinel-edge-worker",
                        },
                    )
                )
            produced += len(detections)

            if client and len(pending) >= BATCH_SIZE:
                result = client.ingest(pending)
                logger.info("ingested batch: %s", result)
                pending.clear()

        if client and pending:
            result = client.ingest(pending)
            logger.info("ingested final batch: %s", result)

    except DetectorError as exc:
        logger.error("%s", exc)
        return 2
    finally:
        if client and session:
            client.close_video_session(session["session_id"])
        if client:
            client.close()

    elapsed = time.perf_counter() - started
    logger.info(
        "done: %d frames processed, %d skipped, %d detections, %d plates in %.1fs (%.1f fps)",
        processed, skipped, produced, plates_read, elapsed,
        processed / elapsed if elapsed else 0.0,
    )
    if dry_run:
        logger.info("dry run - nothing was sent to the central API")
    return 0



# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

def resolve_cameras(
    requested: list[str] | None, all_cameras: bool, client: CentralClient
) -> list[str]:
    """Which cameras this worker is responsible for.

    `--all-cameras` asks the registry rather than a config file, so a camera
    commissioned this morning is picked up on the next cycle without anyone
    editing a deployment. It is still filtered by what this worker's own
    account may watch - discovery is not an escalation.
    """
    if requested:
        return list(dict.fromkeys(requested))
    if not all_cameras:
        return []

    watchable = {"live_and_playback", "live_only"}
    found = [
        camera["camera_id"]
        for camera in client.list_cameras()
        if camera.get("video_access") in watchable
    ]
    logger.info("discovered %d camera(s) this worker may watch", len(found))
    return found


def supervise(
    *,
    cameras: list[str],
    all_cameras: bool,
    clip: str | None,
    max_frames: int,
    sample_interval: int,
    dry_run: bool,
    synthetic: bool,
    source_mode: str,
    forever: bool,
    cycle_seconds: int,
) -> int:
    """Run a pass over every camera, optionally for ever.

    Cameras are processed sequentially and share one loaded model. Loading
    weights is the expensive part (seconds, and hundreds of MB); inference is
    not, so one process comfortably covers the handful of cameras at a site.

    Scaling past that is horizontal, and deliberately so: run one worker per
    site or per device. The central API is stateless for ingest, detection IDs
    are deterministic, and duplicates collapse - so two workers overlapping on
    a camera is wasteful, not corrupting.
    """
    discovery_client: CentralClient | None = None
    if all_cameras and not cameras:
        if dry_run:
            logger.error("--all-cameras needs the registry; not available with --dry-run")
            return 2
        discovery_client = CentralClient()
        discovery_client.sign_in(EDGE_USERNAME, EDGE_PASSWORD)

    # One model for the whole process, reused across every camera and cycle.
    detector = build_detector()
    logger.info("detector: %s", detector.describe())

    cycle = 0
    stop = False

    def _handle_signal(signum, _frame):  # pragma: no cover - signal path
        nonlocal stop
        stop = True
        logger.info("signal %s received; finishing this camera then stopping", signum)

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is not None:
            try:
                signal.signal(handler, _handle_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass

    try:
        while not stop:
            cycle += 1
            targets = cameras
            if all_cameras and discovery_client is not None:
                # Re-resolve every cycle so new cameras join and withdrawn ones
                # drop out without a restart.
                targets = resolve_cameras(None, True, discovery_client)

            if not targets:
                logger.warning("no cameras to process")
                if not forever:
                    return 1

            worst = 0
            for camera_id in targets:
                if stop:
                    break
                logger.info("cycle %d: camera %s", cycle, camera_id)
                try:
                    code = run(
                        camera_id=camera_id,
                        clip=clip,
                        max_frames=max_frames,
                        sample_interval=sample_interval,
                        dry_run=dry_run,
                        synthetic=synthetic,
                        source_mode=source_mode,
                        detector=detector,
                    )
                except Exception as exc:
                    # One camera failing must not take the site offline. A
                    # suspended camera, a revoked grant or a dead NVR is an
                    # ordinary event on a long-running worker.
                    logger.error("camera %s failed this cycle: %s", camera_id, exc)
                    code = 1
                worst = max(worst, code)

            if not forever:
                return worst

            if not stop:
                logger.info("cycle %d complete; sleeping %ds", cycle, cycle_seconds)
                for _ in range(cycle_seconds):
                    if stop:
                        break
                    time.sleep(1)
    finally:
        if discovery_client is not None:
            discovery_client.close()

    logger.info("stopped after %d cycle(s)", cycle)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera",
        action="append",
        default=None,
        help=(
            "Canonical camera ID, e.g. SENTINEL-TRAFFIC-AHM-0001. "
            "Repeat for several cameras on one worker."
        ),
    )
    parser.add_argument(
        "--all-cameras",
        action="store_true",
        help="Process every camera this worker's account may watch, re-checked each cycle",
    )
    parser.add_argument(
        "--forever",
        action="store_true",
        help="Keep cycling until interrupted, instead of one pass",
    )
    parser.add_argument(
        "--cycle-seconds",
        type=int,
        default=int(os.getenv("EDGE_CYCLE_SECONDS", "60")),
        help="Pause between cycles when --forever is set",
    )
    parser.add_argument("--clip", help="Local video file to process instead of a live session")
    parser.add_argument("--max-frames", type=int, default=40)
    parser.add_argument("--sample-interval", type=int, default=FRAME_SAMPLE_INTERVAL)
    parser.add_argument(
        "--dry-run", action="store_true", help="Run inference but send nothing centrally"
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Generate frames in-process (no video decoding, no CV stack needed)",
    )
    parser.add_argument(
        "--source-mode",
        default=os.getenv("EDGE_SOURCE_MODE", "demo_local"),
        choices=["authorized_edge", "demo_local", "mock"],
        help="Recorded on every detection so its provenance is unambiguous",
    )
    args = parser.parse_args(argv)

    if not args.camera and not args.all_cameras:
        parser.error("give --camera at least once, or --all-cameras")

    return supervise(
        cameras=args.camera or [],
        all_cameras=args.all_cameras,
        clip=args.clip,
        max_frames=args.max_frames,
        sample_interval=args.sample_interval,
        dry_run=args.dry_run,
        synthetic=args.synthetic,
        source_mode=args.source_mode,
        forever=args.forever,
        cycle_seconds=args.cycle_seconds,
    )


if __name__ == "__main__":
    sys.exit(main())
