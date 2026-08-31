"""Turn a clean plate crop into what a CCTV camera would have delivered.

Training a recogniser for degraded footage needs degraded footage, and there is
far more clean plate data in the world than dirty. This module manufactures the
dirty kind - but it has to manufacture the *right* kind, and that is where the
usual approach fails.

The ICPR 2026 LRLPR organisers put it directly: most prior work degrades images
by bicubic downsampling, and such simplified degradation models fail to capture
the artifacts present in real operational scenarios. A bicubically shrunk plate
is smooth and clean; a real one has been through an H.264 encoder at 500 kbps,
which does something else entirely - it quantises 8x8 blocks, rings around the
high-contrast edges that plate glyphs are made of, and smears detail across
macroblocks in a way no resampling filter reproduces. A model trained on the
smooth version learns to undo blur and then meets blocking for the first time
in production.

So the chain here ends with a real encode. `codec_pass` runs the frames through
libx264 at an explicit, starved bitrate and reads them back - the same
operation the camera and the streaming gateway each perform.

Measured against the grid this was written for: cameras deliver 1920x1080 at
433 kbps to 2.1 Mbps, which is 0.009-0.05 bits per pixel, and plates arrive
13-17 px tall. The defaults here are set to land in that range.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class DegradeConfig:
    """How hard to hit the crop. Defaults match the Sentinel grid's feeds."""

    #: Gaussian defocus sigma, in pixels of the source crop.
    blur_sigma: tuple[float, float] = (0.6, 2.4)
    #: Motion blur kernel length; 0 disables. Vehicles move, cameras do not.
    motion_len: tuple[int, int] = (0, 9)
    #: Additive sensor noise, in grey levels.
    noise_sigma: tuple[float, float] = (3.0, 16.0)
    #: How far down the plate is scaled before being scaled back up. This is
    #: the "distance" knob: 0.12 puts a 140 px plate at 17 px, which is what
    #: the wide junction cameras actually deliver.
    scale: tuple[float, float] = (0.10, 0.45)
    #: JPEG quality for the still-compression pass.
    jpeg_quality: tuple[int, int] = (18, 60)
    #: Run a real video encode. The expensive, necessary step.
    codec: bool = True
    #: Frames pushed through the encoder. More frames let the codec settle
    #: into the low-bitrate behaviour rather than spending its budget on the
    #: first keyframe.
    codec_frames: int = 6
    #: Target bits per second for the encode.
    codec_bitrate: tuple[int, int] = (60_000, 400_000)
    #: Perspective warp, as a fraction of crop size. Plates are rarely square
    #: to the camera.
    perspective: float = 0.06
    #: Brightness/contrast jitter.
    brightness: tuple[float, float] = (-28.0, 28.0)
    contrast: tuple[float, float] = (0.78, 1.22)
    #: Occlusion bands, the winning team's cheapest robustness trick: vertical
    #: and horizontal masking during training. Bumpers, mud, garlands, and the
    #: number-plate frames people bolt over their own registration.
    occlusion_bands: tuple[int, int] = (0, 2)
    occlusion_width: tuple[float, float] = (0.05, 0.18)


def _rint(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(rng.uniform(lo, hi))


def motion_blur(img: np.ndarray, length: int, angle: float) -> np.ndarray:
    """Directional blur, for a vehicle crossing the frame during exposure."""
    if length < 3:
        return img
    kernel = np.zeros((length, length), np.float32)
    kernel[length // 2, :] = 1.0
    matrix = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle, 1.0)
    kernel = cv2.warpAffine(kernel, matrix, (length, length))
    total = kernel.sum()
    if total <= 0:
        return img
    return cv2.filter2D(img, -1, kernel / total)


def perspective_warp(img: np.ndarray, amount: float,
                     rng: np.random.Generator) -> np.ndarray:
    """Nudge the four corners, so the plate is not head-on."""
    if amount <= 0:
        return img
    h, w = img.shape[:2]
    jitter = lambda: np.float32([_rint(rng, -amount, amount) * w,      # noqa: E731
                                 _rint(rng, -amount, amount) * h])
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([point + jitter() for point in src])
    return cv2.warpPerspective(
        img, cv2.getPerspectiveTransform(src, dst), (w, h),
        borderMode=cv2.BORDER_REPLICATE)


def _ffmpeg_exe() -> str | None:
    """The bundled ffmpeg, or None.

    OpenCV's VideoWriter was the obvious choice and turned out to be the wrong
    one: this machine's libopenh264 refused to load, the writer silently fell
    back through the fourcc list, and the "compressed" output differed from the
    source by a mean of 0.98 grey levels with blockiness of 1.089 against 1.085
    for the untouched image. In other words it produced the smooth, artifact
    free degradation the organisers specifically warn is misleading - while
    reporting success. imageio-ffmpeg ships a real binary and takes an explicit
    bitrate, so the encode is verifiable rather than hopeful.
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def codec_pass(img: np.ndarray, *, bitrate: int, frames: int = 6,
               fps: int = 25) -> np.ndarray:
    """Push the crop through a real H.264 encode at a real bitrate.

    This is the step that separates a useful synthetic sample from a
    misleading one. Resampling produces smooth loss; an encoder produces
    blocking, ringing around glyph edges, and chroma thrown away entirely -
    and those are the artifacts a plate recogniser has to survive.

    The frame is repeated so the encoder has an inter-frame budget to run out
    of, which is where low-bitrate behaviour shows up; the last frame is taken
    because the first is an I-frame and gets the generous share. Returns the
    input unchanged when no encoder is available, so a training run degrades
    in quality rather than dying.
    """
    exe = _ffmpeg_exe()
    h, w = img.shape[:2]
    w2, h2 = w - (w % 2), h - (h % 2)
    if exe is None or w2 < 8 or h2 < 8:
        return img
    src = np.ascontiguousarray(img[:h2, :w2])

    raw = out = None
    try:
        handle = tempfile.NamedTemporaryFile(suffix=".raw", delete=False)
        raw = handle.name
        handle.write(src.tobytes() * max(1, frames))
        handle.close()
        out = raw + ".mp4"
        subprocess.run(
            [exe, "-y", "-loglevel", "error",
             "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{w2}x{h2}", "-r", str(fps), "-i", raw,
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             # Constant bitrate with a tight buffer: the point is to starve
             # the encoder the way a 500 kbps street camera is starved.
             "-b:v", str(bitrate), "-minrate", str(bitrate),
             "-maxrate", str(bitrate), "-bufsize", str(max(1, bitrate // 2)),
             "-g", str(max(2, frames)), out],
            check=True, capture_output=True, timeout=60)

        capture = cv2.VideoCapture(out)
        last = None
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            last = frame
        capture.release()
        if last is not None and last.size:
            return cv2.resize(last, (w, h), interpolation=cv2.INTER_NEAREST)
    except Exception:  # noqa: BLE001 - never fail a training run on this
        pass
    finally:
        for path in (raw, out):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return img


def degrade(crop: np.ndarray, cfg: DegradeConfig | None = None,
            seed: int | None = None) -> np.ndarray:
    """One plausible CCTV rendering of *crop*.

    Order matters and follows the physical path: the scene is warped by
    viewing geometry and lit badly, the lens and motion blur it, the sensor
    adds noise, and only then is it compressed - twice, once as a still and
    once by the video encoder. Compressing before blurring would model a
    camera that encodes the world and then defocuses it.
    """
    cfg = cfg or DegradeConfig()
    rng = np.random.default_rng(seed)
    out = crop.copy()
    h, w = out.shape[:2]

    out = perspective_warp(out, cfg.perspective, rng)

    alpha = _rint(rng, *cfg.contrast)
    beta = _rint(rng, *cfg.brightness)
    out = cv2.convertScaleAbs(out, alpha=alpha, beta=beta)

    length = int(rng.integers(cfg.motion_len[0], cfg.motion_len[1] + 1))
    out = motion_blur(out, length, _rint(rng, -25.0, 25.0))

    sigma = _rint(rng, *cfg.blur_sigma)
    if sigma > 0.05:
        out = cv2.GaussianBlur(out, (0, 0), sigma)

    # Distance. INTER_AREA going down is what a real sensor does; the way back
    # up is the interpolation a downstream consumer would be stuck with.
    scale = _rint(rng, *cfg.scale)
    small = (max(4, int(w * scale)), max(4, int(h * scale)))
    out = cv2.resize(out, small, interpolation=cv2.INTER_AREA)

    noise = _rint(rng, *cfg.noise_sigma)
    if noise > 0.1:
        out = np.clip(out.astype(np.int16)
                      + rng.normal(0, noise, out.shape), 0, 255).astype(np.uint8)

    quality = int(rng.integers(cfg.jpeg_quality[0], cfg.jpeg_quality[1] + 1))
    ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        out = cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    if cfg.codec:
        out = codec_pass(out, bitrate=int(rng.integers(*cfg.codec_bitrate)),
                         frames=cfg.codec_frames)

    out = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)

    bands = int(rng.integers(cfg.occlusion_bands[0], cfg.occlusion_bands[1] + 1))
    for _ in range(bands):
        fraction = _rint(rng, *cfg.occlusion_width)
        if rng.random() < 0.5:
            thickness = max(1, int(h * fraction))
            top = int(rng.integers(0, max(1, h - thickness)))
            out[top:top + thickness, :] = int(rng.integers(0, 90))
        else:
            thickness = max(1, int(w * fraction))
            left = int(rng.integers(0, max(1, w - thickness)))
            out[:, left:left + thickness] = int(rng.integers(0, 90))

    return out


def track(crop: np.ndarray, frames: int = 5, cfg: DegradeConfig | None = None,
          seed: int | None = None) -> list[np.ndarray]:
    """A synthetic *track*: the same plate, degraded differently each frame.

    This is the unit that matters for fusion. Five independent degradations of
    one plate is exactly the structure the LRLPR tracks have, and the reason
    fusion works at all: the artifacts are uncorrelated between frames, so a
    character destroyed in one is often survivable in another. Generating five
    identical copies would train a model that fusion cannot help.
    """
    base = np.random.default_rng(seed).integers(0, 2**31 - 1)
    return [degrade(crop, cfg, seed=int(base) + index) for index in range(frames)]
