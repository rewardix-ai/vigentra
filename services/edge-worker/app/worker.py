"""Vigentra edge analytics worker.

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

    python -m app.worker --camera VIGENTRA-TRAFFIC-AHM-0001 --max-frames 60
    python -m app.worker --clip /app/videos/traffic_01.mp4 --camera ... --dry-run

Environment: see docs/yolo-setup.md.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from . import grid
from .anpr_engine import EngineCache, build_engine
from .detectors import Detection, DetectorError, build_detector

try:
    from anpr.incidents import IncidentDetector
except Exception:  # pragma: no cover - analytics extras absent
    IncidentDetector = None  # type: ignore[assignment,misc]

try:
    from anpr.sampling import AdaptiveSampler
except Exception:  # pragma: no cover - analytics extras absent
    AdaptiveSampler = None  # type: ignore[assignment,misc]
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
logger = logging.getLogger("vigentra.edge.worker")

CENTRAL_API_URL = os.getenv("CENTRAL_API_URL", "http://central-api:8000")
EDGE_USERNAME = os.getenv("EDGE_USERNAME", "traffic.ai")
EDGE_PASSWORD = os.getenv("EDGE_PASSWORD", "AiOps@2026")
FRAME_SAMPLE_INTERVAL = int(os.getenv("YOLO_FRAME_SAMPLE_INTERVAL", "5"))
BATCH_SIZE = int(os.getenv("EDGE_BATCH_SIZE", "50"))

#: The sandbox camera grid. Frames are pulled from it DIRECTLY rather than
#: through the broker, because the broker's job is authorising a human viewer
#: and the grid is a live-only feed with no archive: there is nothing to
#: download, and `iter_session_frames` below can only work on a source that
#: answers range requests. See docs/sentinel-grid.md.
#: live.corp8.cloud was decommissioned; the grid serves from here now.
GRID_BASE_URL = os.getenv("SENTINEL_GRID_BASE_URL", "https://cctv.corp8.cloud")
GRID_ENABLED = os.getenv("SENTINEL_GRID_ENABLED", "true").lower() != "false"
#: Canonical camera IDs carry the grid's own id in their external ID.
GRID_EXTERNAL_PREFIX = "GRID-"


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


def iter_grid_frames(
    camera: "grid.GridCamera", sample_interval: int, max_frames: int
) -> Iterator[tuple[int, Any, float, bool]]:
    """Decode a live grid camera, yielding (index, frame, pts_seconds, cut).

    Every rule from the integrator's guide is already enforced inside
    `grid.ReconnectingCapture` - TCP transport, PTS timing, backoff, join-time
    decoder tolerance, loop discontinuity. This function's only job is sampling
    and unit conversion.

    Sampling is applied to DELIVERED frames, not to a nominal frame rate,
    because the grid's declared fps routinely disagrees with what actually
    arrives and computing a stride from it would sample unevenly.
    """
    with grid.open_capture(camera) as capture:
        emitted = 0
        for frame in capture.frames():
            if frame.index % max(1, sample_interval) != 0 and not frame.discontinuity:
                continue
            # PTS is milliseconds from the start of the stream; the pipeline
            # wants seconds. Still a stream-relative clock, so the worker adds
            # the wall-clock instant separately when it builds the payload.
            yield frame.index, frame.image, frame.pts_ms / 1000.0, frame.discontinuity
            emitted += 1
            if emitted >= max_frames:
                return


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


_GRID_CATALOGUE: dict[str, "grid.GridCamera"] | None = None


def grid_camera_for(camera_id: str, external_id: str | None) -> "grid.GridCamera | None":
    """The grid camera behind a canonical registry ID, or None.

    Resolution goes through the catalogue rather than through a URL built from
    the camera id, because the catalogue is the contract and the URL pattern is
    not - ids and the set of cameras change. Fetched once per process and held,
    since `grid.fetch_catalogue` is a network call and the worker asks this
    question once per camera per cycle.
    """
    global _GRID_CATALOGUE
    if not GRID_ENABLED:
        return None

    # Upper-cased only to test the prefix. The id itself keeps its own case:
    # the grid's ids were numeric when this was written, so folding case was
    # free; they are `cam04` now and folding it looked for CAM04 in a
    # catalogue holding cam04, missed, and silently fell back to fetching the
    # HLS manifest into a temp file - where ffmpeg tried to resolve relative
    # segment names against a local path and could open nothing.
    reference = (external_id or "").strip()
    if not reference.upper().startswith(GRID_EXTERNAL_PREFIX):
        return None

    if not _GRID_CATALOGUE:
        try:
            _GRID_CATALOGUE = grid.fetch_catalogue(GRID_BASE_URL)
            logger.info("grid catalogue: %d camera(s)", len(_GRID_CATALOGUE))
        except Exception as exc:
            # A grid that is unreachable is a normal condition, not a crash:
            # the worker still has whatever local and departmental cameras it
            # was given.
            #
            # The failure is NOT cached. Caching an empty catalogue meant one
            # slow response - on a gateway that takes tens of seconds on a cold
            # connection - disabled grid capture for the entire life of a
            # process meant to run for ever, sending every camera down the
            # broker path instead, where a live-only source has nothing to
            # serve and answers 404. Retrying on the next camera costs one
            # request; the alternative costs the whole run.
            logger.warning(
                "grid catalogue unavailable (%s); skipping grid cameras this pass", exc
            )
            _GRID_CATALOGUE = {}
            return None

    raw = reference[len(GRID_EXTERNAL_PREFIX):]
    # Try the id as given first, then the old numeric form with its padding
    # stripped, so a registry holding ids from either scheme still resolves.
    for key in (raw, raw.lstrip("0") or "0", raw.lower(), raw.upper()):
        camera = _GRID_CATALOGUE.get(key)
        if camera is not None:
            return camera
    return None


# ---------------------------------------------------------------------------
# Central API client
# ---------------------------------------------------------------------------

class CentralClient:
    """Thin authenticated client for the ingest endpoints."""

    def __init__(self, base_url: str = CENTRAL_API_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=20.0)
        self._token: str | None = None
        # Opening a session re-presents the password, so it has to be held for
        # the life of the client rather than discarded after sign-in. Kept
        # private and never logged - see open_video_session.
        self._password: str | None = None

    def sign_in(self, username: str, password: str) -> dict:
        response = self._client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._password = password
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

        That includes re-presenting the password. `VideoSessionCreate` requires
        it on every session precisely so a bearer token on its own cannot open
        a camera, and the worker is not exempt: omitting it made every live
        session 422 and left the worker able to run only from `--clip`.
        """
        if not self._password:
            raise DetectorError("Not signed in to the central API.")
        response = self._client.post(
            "/api/v1/video-sessions",
            headers=self._headers(),
            json={
                "camera_id": camera_id,
                "mode": "live",
                "reason": reason,
                "password": self._password,
            },
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

    def ingest_incidents(self, incidents: list[dict]) -> dict:
        """Submit incident CANDIDATES for the review queue.

        Kept separate from detection ingest on purpose: a detection is a fact
        (a box existed), an incident is a pattern that a human must confirm, so
        they land in different stores with different retention and review.
        """
        response = self._client.post(
            "/api/v1/incidents/ingest",
            headers=self._headers(),
            json={"incidents": incidents},
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

def _plain(frames: Iterator[tuple[int, Any]]) -> Iterator[tuple[int, Any, float, bool]]:
    """Give a non-grid source the same four-part shape as the live one.

    Clips and synthetic frames have no presentation timestamp, so the capture
    instant falls back to wall-clock. That is acceptable for a local
    demonstration and NOT acceptable on a live feed, which is why the grid path
    carries real PTS rather than reusing this.
    """
    for index, frame in frames:
        yield index, frame, time.time(), False


def _sighting_payload(
    sighting,
    *,
    camera_id: str,
    timestamp_iso: str,
    source_mode: str,
    reader_version: str,
    provenance: dict,
) -> dict:
    """Turn a voted plate into an ingest row.

    The detection ID is derived from the camera, the track and the plate text,
    NOT from the frame. A consensus reading is an assertion about a vehicle
    across a pass, so replaying the same pass must collapse to one row - and a
    revised reading for the same track deliberately produces a different id, so
    the revision is visible rather than overwriting the first answer.
    """
    seed = f"{camera_id}|{sighting.track_id}|{sighting.text}|{int(sighting.captured_at)}"
    detection_id = f"det_{hashlib.sha1(seed.encode()).hexdigest()[:20]}"
    return {
        "detection_id": detection_id,
        "camera_id": camera_id,
        "timestamp_utc": timestamp_iso,
        "class_name": "car",
        "class_id": 2,
        "confidence": round(float(sighting.confidence), 4),
        "bbox_xyxy": sighting.vehicle_bbox or sighting.plate_bbox or [0.0, 0.0, 1.0, 1.0],
        "model_name": "vigentra-anpr-consensus",
        "model_version": reader_version.split("/", 1)[-1],
        "source_mode": source_mode,
        "is_demo_data": source_mode != "authorized_edge",
        "plate_text": sighting.text,
        "plate_confidence": round(float(sighting.score), 4),
        "plate_bbox_xyxy": sighting.plate_bbox or None,
        "plate_reader": reader_version,
        "provenance": {
            **provenance,
            # Read by the central API to record how many frames voted for this
            # plate. One frame is a guess; twelve frames agreeing is a reading,
            # and an operator is entitled to know which they are looking at.
            "plate_observations": sighting.observations,
            "plate_confirmed": sighting.confirmed,
            "plate_state": sighting.state,
            "plate_format": sighting.plate_format,
            "track_id": sighting.track_id,
            "restoration": sighting.method,
            "captured_at_pts": round(sighting.captured_at, 3),
        },
    }


from dataclasses import dataclass as _dataclass


@_dataclass
class _IncidentTrack:
    """The minimal track view the incident detector reads.

    The detector is deliberately agnostic about where its tracks come from - it
    needs an id, a box and a label and nothing else - so Vigentra's vehicle
    detections are adapted to this rather than the detector being coupled to
    them.
    """
    track_id: int
    box: tuple
    label: str


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
    external_camera_id: str | None = None,
    anpr_cache: Any = None,
    incidents_by_camera: dict[str, Any] | None = None,
) -> int:
    # Loading weights costs seconds and hundreds of megabytes. A supervisor
    # covering several cameras builds the detector once and passes it in;
    # a one-shot run builds its own.
    built_here = detector is None
    detector = detector or build_detector()
    router = FrameQualityRouter()

    # ANPR is off unless ANPR_ENABLE is set.
    #
    # Two readers, and the choice is not a preference. The consensus engine is
    # stateful per camera and votes a plate across every frame a vehicle
    # appears in, so it is built HERE, per run, and never shared between
    # cameras. The single-frame reader is the fallback for when the analytics
    # extras are missing - it is worse, and it says so in docs/anpr.md, but a
    # worker that reads no plates at all is worse still.
    #
    # A supervisor passes a cache so the engine - and everything the camera has
    # learned about itself: its overlays, its plate votes - survives from one
    # cycle to the next. A one-shot run builds its own and throws it away,
    # which is right for a single pass.
    anpr = anpr_cache.get(camera_id) if anpr_cache is not None else build_engine()
    plate_reader = build_plate_reader() if anpr is None else None
    plates_read = 0

    # Incident detection rides on the same tracker the ANPR engine already
    # runs - no extra model, no extra inference. It watches the vehicle boxes
    # move and raises CANDIDATES (never findings) for a human to look at:
    # wrong-way, stopped-in-lane, sudden-stop, collision. Off when tracking is
    # off, because it has nothing to watch without stable track ids.
    # Kept per camera across cycles for the same reason the engine is: this
    # detector learns the junction's prevailing direction of travel before it
    # can call anything wrong-way, and a 25-frame pass does not teach it that
    # twice over if the first pass is discarded.
    incidents = None
    if anpr is not None and IncidentDetector is not None:
        if incidents_by_camera is None:
            incidents = IncidentDetector(camera_id)
        else:
            incidents = incidents_by_camera.get(camera_id)
            if incidents is None:
                incidents = incidents_by_camera[camera_id] = IncidentDetector(camera_id)
    incident_batch: list[dict] = []

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
    # A grid camera is live-only with no archive, so it is captured directly
    # through the guide-compliant capture rather than downloaded. The session
    # above is still opened and still audited - the authorisation decision is
    # unchanged, only the transport differs.
    grid_camera = None if (synthetic or clip) else grid_camera_for(camera_id, external_camera_id)

    # Adaptive sampling on every real video source - live grid and local clip
    # alike. A fixed stride spends its frames evenly and so spends most of them
    # on empty road; the sampler spends them when a vehicle is close enough to
    # carry a plate wide enough to read. Measured on identical footage and an
    # identical budget, that was 6 plate boxes against 42.
    #
    # Synthetic frames are excluded because there is nothing in them to bunch
    # around, and a burst there would only distort the wiring test.
    sampler = (
        AdaptiveSampler(stride=max(1, sample_interval))
        if AdaptiveSampler is not None and not synthetic
        else None
    )

    if synthetic:
        frames = _plain(
            iter_synthetic_frames(max_frames * max(1, sample_interval), sample_interval)
        )
    elif clip:
        # Decode every frame and let the sampler choose, exactly as on the
        # grid path: decoding is the cheap half, and the stride's cost is the
        # plate it was not looking at.
        frames = _plain(iter_clip_frames(clip, 1 if sampler is not None else sample_interval))
    elif grid_camera is not None:
        logger.info("live capture: %s", grid_camera.described)
        # Look at every frame across the SAME footage window the fixed stride
        # would have spanned, and let the sampler decide where to spend the
        # expensive passes. Decoding is cheap; missing the second a plate is
        # large is not.
        if sampler is not None:
            frames = iter_grid_frames(grid_camera, 1, max_frames * max(1, sample_interval))
        else:
            frames = iter_grid_frames(grid_camera, sample_interval, max_frames)
    elif session and client:
        frames = _plain(iter_session_frames(client, session, sample_interval))
    else:
        raise DetectorError(
            "No frame source: pass --clip, or allow a live session, or use "
            "--synthetic to exercise the pipeline without video."
        )

    pending: list[dict] = []
    processed = skipped = produced = 0
    started = time.perf_counter()

    try:
        for frame_index, frame, pts_seconds, discontinuity in frames:
            if processed >= max_frames:
                break
            # Cheap rejection: a frame the sampler does not want costs nothing
            # beyond the decode that already happened.
            if sampler is not None and not discontinuity and not sampler.should_process(frame_index):
                continue
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
            plate_sightings = []

            if anpr is not None:
                # One pass produces both the vehicles in this frame and any
                # plates that settled on it. Most frames yield vehicles and no
                # plates - a plate is only reported once its track has voted.
                try:
                    detections, plate_sightings = anpr.process(
                        frame_to_detect,
                        captured_at=pts_seconds,
                        discontinuity=discontinuity,
                    )
                except Exception as exc:  # pragma: no cover - engine fault
                    logger.error("ANPR engine failed, falling back to plain detection: %s", exc)
                    anpr = None
                    plate_reader = build_plate_reader()
                    detections = detector.detect(frame_to_detect)
            else:
                detections = detector.detect(frame_to_detect)

            # A vehicle wide enough to be carrying a readable plate buys a
            # burst of dense frames - the plate is growing and the best crop is
            # a moment away.
            if sampler is not None:
                sampler.note(
                    d.bbox_xyxy[2] - d.bbox_xyxy[0]
                    for d in detections
                    if d.class_name in PLATE_BEARING_CLASSES
                )

            base_provenance = {
                "frame_index": frame_index,
                "frame_quality": assessment.quality.value,
                "enhancement_applied": assessment.enhancement_applied,
                "sample_interval": sample_interval,
                "worker": "vigentra-edge-worker",
                # Stream-relative capture time, kept alongside the wall-clock
                # timestamp. The guide is explicit that PTS is the only
                # trustworthy clock on these feeds, so it travels with the row.
                "pts_seconds": round(pts_seconds, 3),
            }

            for position, detection in enumerate(detections):
                detection.frame_quality = assessment.quality.value

                # The single-frame reader is only used when the consensus
                # engine is unavailable. Only vehicles, and only on the frame
                # the detector actually saw - a person is never cropped.
                plate = None
                if plate_reader is not None and detection.class_name in PLATE_BEARING_CLASSES:
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
                        provenance=dict(base_provenance),
                    )
                )
            produced += len(detections)

            # A voted plate is its own detection row rather than a field on one
            # of the boxes above. It has to be: the reading settled across many
            # frames, so there is no single box in THIS frame that it belongs
            # to, and attaching it to an arbitrary one would misrepresent where
            # it came from.
            for sighting in plate_sightings:
                plates_read += 1
                pending.append(
                    _sighting_payload(
                        sighting,
                        camera_id=camera_id,
                        timestamp_iso=timestamp,
                        source_mode=source_mode,
                        reader_version=f"{anpr.name}/{anpr.version}",
                        provenance=base_provenance,
                    )
                )
                logger.info(
                    "plate %s (score %.2f, %d frames%s) on track %d",
                    sighting.text, sighting.score, sighting.observations,
                    "" if sighting.confirmed else ", unconfirmed", sighting.track_id,
                )

            # Incident detection from the same boxes. `detections` carry the
            # tracker's id in `extra`; only tracked vehicles can be judged for
            # motion, so untracked ones are skipped rather than guessed at.
            if incidents is not None:
                if discontinuity:
                    incidents.reset()
                views = [
                    _IncidentTrack(
                        track_id=int(d.extra["track_id"]),
                        box=tuple(d.bbox_xyxy),
                        label=d.class_name,
                    )
                    for d in detections
                    if d.extra.get("track_id") is not None
                ]
                for inc in incidents.update(views, pts_seconds):
                    incident_batch.append(inc.to_dict())
                    logger.info(
                        "incident %s (%s) on camera %s: %s",
                        inc.kind, inc.severity, camera_id, inc.reason,
                    )
                if client and incident_batch:
                    try:
                        client.ingest_incidents(incident_batch)
                    except Exception as exc:  # pragma: no cover - non-fatal
                        logger.warning("incident ingest failed: %s", exc)
                    incident_batch.clear()

            if client and len(pending) >= BATCH_SIZE:
                result = client.ingest(pending)
                logger.info("ingested batch: %s", result)
                pending.clear()

        if client and pending:
            result = client.ingest(pending)
            logger.info("ingested final batch: %s", result)

        if client and incident_batch:
            try:
                client.ingest_incidents(incident_batch)
            except Exception as exc:  # pragma: no cover - non-fatal
                logger.warning("final incident ingest failed: %s", exc)
            incident_batch.clear()

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
    if sampler is not None:
        d = sampler.describe()
        logger.info(
            "sampling: looked at %d frames, processed %d, %d burst(s) on a "
            "close vehicle (stride %d)",
            d["looked"], d["processed"], d["bursts"], d["stride"],
        )
    if dry_run:
        logger.info("dry run - nothing was sent to the central API")
    return 0



# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

def resolve_cameras(
    requested: list[str] | None, all_cameras: bool, client: CentralClient
) -> list[tuple[str, str | None]]:
    """Which cameras this worker is responsible for, as (canonical, external).

    `--all-cameras` asks the registry rather than a config file, so a camera
    commissioned this morning is picked up on the next cycle without anyone
    editing a deployment. It is still filtered by what this worker's own
    account may watch - discovery is not an escalation.

    The external ID travels with the canonical one because it is what says
    whether a camera is on the live grid, and therefore whether frames come
    from a direct capture or through the broker. An explicitly named camera has
    no external ID to hand, so it is resolved from the registry too.
    """
    watchable = {"live_and_playback", "live_only"}

    if requested:
        wanted = list(dict.fromkeys(requested))
        try:
            known = {
                camera["camera_id"]: camera.get("external_camera_id")
                for camera in client.list_cameras()
            }
        except Exception as exc:
            # Naming a camera explicitly must keep working when the registry
            # listing does not; it just loses the grid fast path.
            logger.warning("could not resolve external IDs (%s); assuming non-grid", exc)
            known = {}
        return [(camera_id, known.get(camera_id)) for camera_id in wanted]

    if not all_cameras:
        return []

    found = [
        (camera["camera_id"], camera.get("external_camera_id"))
        for camera in client.list_cameras()
        if camera.get("video_access") in watchable
    ]
    logger.info("discovered %d camera(s) this worker may watch", len(found))
    return found


def _sign_in_patiently(client: CentralClient, *, forever: bool) -> None:
    """Sign in, waiting for the API rather than exiting when it is not there.

    A worker started beside the stack it reports to will often win the race,
    and one running continuously outlives any number of API deployments. Both
    used to end the process on the first refused connection, which turns a
    thirty-second restart into analytics that stay off until somebody notices.

    A one-shot run still fails fast: there, an unreachable API means the
    command cannot do what was asked, and saying so immediately is right.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            client.sign_in(EDGE_USERNAME, EDGE_PASSWORD)
            return
        except Exception as exc:
            if not forever:
                raise
            delay = min(60, 2 ** min(attempt, 6))
            logger.warning(
                "central API not ready (%s); retrying sign-in in %ds", exc, delay
            )
            time.sleep(delay)


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
    # The registry is consulted whenever there is one to consult - for
    # discovery, and to learn each named camera's external ID so a grid camera
    # takes the live path instead of the broker's.
    if not dry_run:
        discovery_client = CentralClient()
        _sign_in_patiently(discovery_client, forever=forever)
    elif all_cameras:
        logger.error("--all-cameras needs the registry; not available with --dry-run")
        return 2

    targets: list[tuple[str, str | None]] = (
        resolve_cameras(cameras, False, discovery_client)
        if cameras and discovery_client is not None
        else [(camera_id, None) for camera_id in cameras]
    )

    # One model for the whole process, reused across every camera and cycle.
    detector = build_detector()
    logger.info("detector: %s", detector.describe())

    # ANPR engines and incident detectors are per camera, not per process, and
    # both now survive between cycles: see EngineCache for why a camera that
    # forgets itself every 25 frames never learns anything.
    anpr_cache = EngineCache()
    incidents_by_camera: dict[str, Any] = {}

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
            if all_cameras and discovery_client is not None:
                # Re-resolve every cycle so new cameras join and withdrawn ones
                # drop out without a restart.
                #
                # Tolerated rather than fatal: this call is the first thing a
                # cycle does, so an API restart landing here used to kill a
                # worker meant to run for ever - and a worker that exits when
                # the registry is redeployed is not continuous analytics, it is
                # analytics until the next deployment. Last cycle's camera list
                # is a good enough approximation of this one's.
                try:
                    targets = resolve_cameras(None, True, discovery_client)
                except Exception as exc:
                    if not forever:
                        raise
                    logger.warning(
                        "camera list unavailable this cycle (%s); reusing the "
                        "previous %d camera(s)",
                        exc,
                        len(targets),
                    )

            if not targets:
                logger.warning("no cameras to process")
                if not forever:
                    return 1

            worst = 0
            for camera_id, external_id in targets:
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
                        external_camera_id=external_id,
                        anpr_cache=anpr_cache,
                        incidents_by_camera=incidents_by_camera,
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
                logger.info(
                    "cycle %d complete; sleeping %ds; anpr engines %s",
                    cycle, cycle_seconds, anpr_cache.describe(),
                )
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
            "Canonical camera ID, e.g. VIGENTRA-TRAFFIC-AHM-0001. "
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
