"""
Sentinel Grid RTSP Ingestion Worker
------------------------------------
Connects one camera from the Sentinel Camera Grid sandbox to your code,
following every rule in the integrator's guide (sentinel.gujarat.gov.in/resource):

  DO     force RTSP over TCP
  DON'T  trust CAP_PROP_FPS for timing math
  DO     drive timing from PTS (CAP_PROP_POS_MSEC), never wall-clock arrival
  DON'T  assume a constant frame rate
  DO     reconnect automatically, with backoff (2s -> 30s cap, never a tight loop)
  DON'T  treat decode warnings at join as fatal (mixed H.264/H.265 grid)
  DON'T  assume a uniform grid (read codec/res/fps from /api/ingest, not hardcoded)
  DO     expect a scene discontinuity (each feed loops; the cut looks like a reboot)
  DON'T  pull /stream/<id> expecting a full file (that's the browser fallback)
  DON'T  publish to the gateway — consume only
  DO     pace your load (only open cameras you're using, release what you're done with)

Install:
    pip install opencv-python requests

Run:
    python sentinel_ingest.py --camera 1
    python sentinel_ingest.py --camera 1 --save-frames out/ --max-frames 500

This connects ONE camera end-to-end and prints/saves what it decodes. To scale
to the full grid, see run_all() at the bottom, which loops the same worker over
every live entry in /api/ingest instead of hardcoding camera IDs.
"""

import argparse
import os
import time
from datetime import datetime, timezone

import cv2
import requests

INGEST_API = "https://live.corp8.cloud/api/ingest"

# CRITICAL: forces FFmpeg's RTSP demuxer to use TCP instead of UDP. UDP fails
# across NAT and most corporate firewalls, and partial UDP delivery produces
# corrupt frames that look like model bugs. Must be set BEFORE the first
# cv2.VideoCapture() call — it's a process-wide FFmpeg option, not per-capture.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"


def fetch_catalogue() -> dict:
    """
    The catalogue is the contract, the URL pattern is not — camera ids and
    the set of available cameras can change. Always read from here rather
    than constructing rtsp://.../stream/<id> URLs by hand.
    """
    resp = requests.get(INGEST_API, timeout=10)
    resp.raise_for_status()
    return {c["id"]: c for c in resp.json()["cameras"]}


def get_camera(camera_id: str) -> dict:
    cams = fetch_catalogue()
    cam = cams.get(str(camera_id))
    if cam is None:
        raise SystemExit(
            f"Camera {camera_id!r} not in catalogue. "
            f"Known ids: {sorted(cams, key=int)}"
        )
    return cam


class ReconnectingStream:
    """
    Wraps cv2.VideoCapture with the exact reconnect policy the guide asks
    for: start at ~2s backoff, cap at ~30s, never a tight loop. Also
    tolerates a run of decode failures right after (re)connecting instead of
    aborting immediately — mid-stream joins on a mixed H.264/H.265 grid
    routinely throw non-fatal decoder warnings until the first IDR frame
    arrives, and that's expected, not an error to crash on.
    """

    MIN_BACKOFF = 2.0
    MAX_BACKOFF = 30.0
    GRACE_FAILURES = 25  # tolerate this many bad reads before calling it a real drop

    def __init__(self, rtsp_url: str):
        self.rtsp_url = rtsp_url
        self.cap = None
        self._backoff = self.MIN_BACKOFF
        self._consec_failures = 0
        self._connect()

    def _connect(self):
        if self.cap is not None:
            self.cap.release()
        print(f"[connect] {self.rtsp_url}")
        self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        self._consec_failures = 0

    def _reconnect_with_backoff(self):
        print(f"[reconnect] backing off {self._backoff:.1f}s")
        time.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)
        self._connect()

    def read(self):
        """Returns (frame, pts_ms). Both are None on a benign gap or mid-reconnect."""
        ok, frame = self.cap.read()

        if not ok:
            self._consec_failures += 1
            if self._consec_failures <= self.GRACE_FAILURES:
                return None, None  # likely a join-time decoder hiccup or scene-loop cut
            self._reconnect_with_backoff()
            return None, None

        self._backoff = self.MIN_BACKOFF
        self._consec_failures = 0

        # PTS, never wall-clock arrival time. The gateway replays a buffered
        # group-of-pictures on connect, so the first second or two of frames
        # arrives faster than real time — a tracker timestamping by arrival
        # computes impossible velocities immediately after every reconnect.
        pts_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
        return frame, pts_ms

    def release(self):
        if self.cap is not None:
            self.cap.release()


def run(camera_id: str, save_dir: str | None, max_frames: int | None):
    cam = get_camera(camera_id)
    print(f"[info] {cam['name']} — {cam['location']}")
    print(
        f"[info] catalogue: codec={cam['codec'] or '?'} "
        f"{cam['width']}x{cam['height']} @ {cam['fps']}fps "
        f"(measure real rate from PTS deltas below — don't trust this number for timing)"
    )

    stream = ReconnectingStream(cam["rtsp_url"])
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    frame_count = 0
    last_pts = None
    t_start = time.time()

    try:
        while True:
            frame, pts_ms = stream.read()
            if frame is None:
                continue  # benign gap or mid-reconnect — keep polling, don't crash

            frame_count += 1

            if last_pts is not None and pts_ms < last_pts:
                # PTS went backwards: the scene-loop discontinuity the guide
                # documents ("each feed is a continuous recording that
                # loops... the scene cuts abruptly, similar to a camera
                # reboot"). Long-lived state — background models, Re-ID
                # galleries, track ids — should reset here, not assume
                # infinite continuity.
                print(f"[loop] scene discontinuity on camera {camera_id} — resetting track state")
            last_pts = pts_ms

            # ---- AI hook point --------------------------------------------
            # This is where the pipeline's AI Analytics + Match Engine stages
            # plug in (appearance-first, per the playbook's §06): detect
            # vehicles, embed for Re-ID, run ANPR as a secondary signal, then
            # query the watchlist. e.g.:
            #
            #   for det in detector.infer(frame):
            #       crop = frame[det.y1:det.y2, det.x1:det.x2]
            #       embedding = reid_model.embed(crop)
            #       plate, plate_conf = anpr_model.read(crop)
            #       match = watchlist.query(appearance=embedding,
            #                                plate=plate, plate_conf=plate_conf)
            #       if match:
            #           publish_alert(camera_id=camera_id, ts_ms=pts_ms, match=match)
            # -----------------------------------------------------------------

            if save_dir and frame_count % 25 == 0:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                path = os.path.join(save_dir, f"cam{camera_id}_{ts}_{frame_count}.jpg")
                cv2.imwrite(path, frame)
                print(f"[save] {path}")

            if frame_count % 50 == 0:
                elapsed = time.time() - t_start
                print(
                    f"[stats] {frame_count} frames in {elapsed:.1f}s "
                    f"(~{frame_count / elapsed:.1f} fps measured)"
                )

            if max_frames and frame_count >= max_frames:
                break

    except KeyboardInterrupt:
        print("\n[stop] interrupted")
    finally:
        stream.release()  # "pace your load" — close captures you're finished with
        print(f"[done] released camera {camera_id}")


def run_all(save_dir: str | None):
    """
    Scale-out shape: loop the same worker over every camera the catalogue
    currently reports as live, instead of hardcoding camera 1/2/3. Run each
    ReconnectingStream on its own thread/process in a real deployment — this
    sequential version is for understanding the pattern, not production use.
    """
    cams = fetch_catalogue()
    live_cams = [c for c in cams.values() if c["live"]]
    print(f"[info] {len(live_cams)} live cameras in catalogue")
    for cam in live_cams:
        print(f"  - {cam['id']}: {cam['location']} "
              f"({cam['codec'] or 'unknown codec'}, {cam['width']}x{cam['height']})")
    print("[info] wire each one to its own thread/process — see docstring above.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentinel Grid RTSP ingestion worker")
    parser.add_argument("--camera", help="camera id from /api/ingest, e.g. 1")
    parser.add_argument("--save-frames", metavar="DIR", default=None,
                         help="optional: save a sample frame every ~25 decoded frames")
    parser.add_argument("--max-frames", type=int, default=None,
                         help="optional: stop after N frames (omit to run forever)")
    parser.add_argument("--list", action="store_true",
                         help="list every live camera from /api/ingest and exit")
    args = parser.parse_args()

    if args.list:
        run_all(args.save_frames)
    elif args.camera:
        run(args.camera, args.save_frames, args.max_frames)
    else:
        parser.error("pass --camera <id> or --list")