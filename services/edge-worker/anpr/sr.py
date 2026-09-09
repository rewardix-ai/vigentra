"""Plate super-resolution: the enhancement layer between detection and OCR.

The generic ESPCN weights the pipeline ships were trained on photographs.
This network is trained on pairs from the uniform dataset: a plate crop the
way the camera delivered it - blurred, compressed, 8-90 px wide, at night,
behind headlight glare - and the same region of the sharp composite at four
times the size. It learns what plate strokes look like under this footage's
degradation, which is the only thing it is ever asked to restore.

Architecture: a small residual CNN (FSRCNN-like feature extraction, four
residual blocks, pixel-shuffle x4). ~0.4M parameters, so a 64 px-wide crop
is upscaled in a few milliseconds on CPU and the card stays free for
detection.

The trainer (tools/train_plate_sr.py) and the enhancer (anpr/enhance.py)
share this module so inference never drifts from training.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

SCALE = 4


@dataclass(frozen=True)
class SrSpec:
    scale: int = SCALE
    features: int = 48
    blocks: int = 4


def build_model(spec: SrSpec | None = None):
    import torch
    from torch import nn

    spec = spec or SrSpec()

    class Residual(nn.Module):
        def __init__(self, c: int) -> None:
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv2d(c, c, 3, padding=1), nn.PReLU(c),
                nn.Conv2d(c, c, 3, padding=1),
            )

        def forward(self, x):
            return x + self.body(x)

    class PlateSR(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            f = spec.features
            self.head = nn.Sequential(nn.Conv2d(3, f, 5, padding=2), nn.PReLU(f))
            self.body = nn.Sequential(*[Residual(f) for _ in range(spec.blocks)])
            self.tail = nn.Sequential(
                nn.Conv2d(f, 3 * spec.scale * spec.scale, 3, padding=1),
                nn.PixelShuffle(spec.scale),
            )
            self.scale = spec.scale

        def forward(self, x):
            """[B, 3, h, w] in [0, 1] -> [B, 3, h*s, w*s]; residual over bicubic."""
            base = torch.nn.functional.interpolate(x, scale_factor=self.scale,
                                                   mode="bicubic", align_corners=False)
            return (base + self.tail(self.body(self.head(x)))).clamp(0.0, 1.0)

    return PlateSR()


# ---------------------------------------------------------------------------
# Multi-frame
# ---------------------------------------------------------------------------

#: Frames fused per plate. Fewer are repeated to fill; more are trimmed to
#: the sharpest.
MF_FRAMES = 5


@dataclass(frozen=True)
class MfSpec:
    scale: int = SCALE
    frames: int = MF_FRAMES
    features: int = 64
    blocks: int = 6


def build_mf_model(spec: MfSpec | None = None):
    """The multi-frame upscaler.

    Early fusion: the K aligned frames are stacked on the channel axis so the
    first convolution already sees every frame's version of each pixel, and
    the residual body learns which frame to trust where. A blurred frame
    contributes its low frequencies, a sharp one its strokes, and sub-pixel
    shifts between frames add the detail no single frame carries. The output
    is a correction over the bicubic upscale of the frames' median, so with
    nothing learned it is a denoised bicubic.
    """
    import torch
    from torch import nn

    spec = spec or MfSpec()

    class Residual(nn.Module):
        def __init__(self, c: int) -> None:
            super().__init__()
            self.body = nn.Sequential(nn.Conv2d(c, c, 3, padding=1), nn.PReLU(c),
                                      nn.Conv2d(c, c, 3, padding=1))

        def forward(self, x):
            return x + self.body(x)

    class PlateMFSR(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            f = spec.features
            self.head = nn.Sequential(nn.Conv2d(3 * spec.frames, f, 5, padding=2), nn.PReLU(f))
            self.body = nn.Sequential(*[Residual(f) for _ in range(spec.blocks)])
            self.tail = nn.Sequential(nn.Conv2d(f, 3 * spec.scale * spec.scale, 3, padding=1),
                                      nn.PixelShuffle(spec.scale))
            self.scale, self.frames = spec.scale, spec.frames

        def forward(self, x):
            """[B, K*3, h, w] in [0, 1] -> [B, 3, h*s, w*s]."""
            B, C, h, w = x.shape
            stack = x.view(B, self.frames, 3, h, w)
            anchor = stack.median(dim=1).values
            base = torch.nn.functional.interpolate(anchor, scale_factor=self.scale,
                                                   mode="bicubic", align_corners=False)
            return (base + self.tail(self.body(self.head(x)))).clamp(0.0, 1.0)

    return PlateMFSR()


def align_frames(frames: list[np.ndarray], size: tuple[int, int] | None = None) -> list[np.ndarray]:
    """Resize every frame to one grid and register it to the sharpest by
    translation (ECC). Frames that will not converge are kept unshifted rather
    than dropped: a mis-registered frame costs less than a missing one when
    the network has learned to weigh frames.
    """
    if not frames:
        return []
    if size is None:
        # The sharpest frame (highest Laplacian variance) sets the grid,
        # capped at MF_MAX_WIDTH: wider crops carry nothing fusion adds.
        def sharp(f):
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f
            return cv2.Laplacian(g, cv2.CV_32F).var()
        ref = max(frames, key=sharp)
        w, h = ref.shape[1], ref.shape[0]
        if w > MF_MAX_WIDTH:
            h = max(4, int(round(h * MF_MAX_WIDTH / w)))
            w = MF_MAX_WIDTH
        size = (w, h)
    W, H = size
    out = []
    ref_gray = None
    for f in frames:
        if f.ndim == 2:
            f = cv2.cvtColor(f, cv2.COLOR_GRAY2BGR)
        r = cv2.resize(f, (W, H), interpolation=cv2.INTER_CUBIC if f.shape[1] < W else cv2.INTER_AREA)
        g = cv2.cvtColor(r, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        if ref_gray is None:
            ref_gray = g
            out.append(r)
            continue
        warp = np.eye(2, 3, dtype=np.float32)
        try:
            cv2.findTransformECC(ref_gray, g, warp, cv2.MOTION_TRANSLATION,
                                 (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 1e-4), None, 3)
            r = cv2.warpAffine(r, warp, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REPLICATE)
        except cv2.error:
            pass
        out.append(r)
    return out


def stack_frames(frames: list[np.ndarray], k: int):
    """K aligned BGR frames -> [1, K*3, H, W] tensor; repeats or trims to K."""
    import torch
    if not frames:
        raise ValueError("no frames")
    frames = list(frames)
    while len(frames) < k:
        frames.append(frames[len(frames) % max(1, len(frames))])
    frames = frames[:k]
    arr = np.concatenate([f.astype(np.float32) / 255.0 for f in frames], axis=2)  # H, W, K*3
    return torch.from_numpy(arr).permute(2, 0, 1)[None]


class MultiFrameUpscaler:
    """Loads plate_mfsr.pt and fuses a track's crops into one upscaled plate."""

    def __init__(self, weights: str, device: str = "cpu") -> None:
        import torch
        ck = torch.load(weights, map_location="cpu", weights_only=False)
        self.spec = MfSpec(**ck.get("spec", {}))
        self.model = build_mf_model(self.spec)
        self.model.load_state_dict(ck["model"])
        self.model.eval()
        self.device = torch.device(device)
        self.model.to(self.device)
        self.scale, self.frames = self.spec.scale, self.spec.frames
        self.name = f"plate_mfsr_x{self.scale}_k{self.frames}"

    def upscale(self, frames: list[np.ndarray]) -> np.ndarray | None:
        import torch
        frames = [f for f in frames if f is not None and f.size]
        if not frames:
            return None
        aligned = align_frames(frames)
        with torch.no_grad():
            out = self.model(stack_frames(aligned, self.frames).to(self.device))
        return to_image(out)


#: Widest plate crop the multi-frame fusion works on. Crops are plates, and
#: a plate wider than this is already readable; fusing five 600 px crops
#: through the network costs seconds on CPU for nothing.
MF_MAX_WIDTH = 192


def best_device() -> str:
    """"cuda" when a card is present, else "cpu". The upscalers are small,
    but on CPU a 4x pass over a 140 px crop is hundreds of milliseconds and
    the multi-frame pass seconds; on the card both are milliseconds."""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:                                    # noqa: BLE001
        return "cpu"


def to_tensor(bgr: np.ndarray):
    import torch
    x = bgr.astype(np.float32) / 255.0
    return torch.from_numpy(x).permute(2, 0, 1)[None]


def to_image(t) -> np.ndarray:
    arr = t[0].detach().clamp(0, 1).permute(1, 2, 0).cpu().numpy()
    return (arr * 255.0 + 0.5).astype(np.uint8)


class PlateUpscaler:
    """Loads plate_sr.pt and upscales one crop. Mirrors SuperResolver's API."""

    def __init__(self, weights: str, device: str = "cpu") -> None:
        import torch
        ck = torch.load(weights, map_location="cpu", weights_only=False)
        self.spec = SrSpec(**ck.get("spec", {}))
        self.model = build_model(self.spec)
        self.model.load_state_dict(ck["model"])
        self.model.eval()
        self.device = torch.device(device)
        self.model.to(self.device)
        self.scale = self.spec.scale
        self.name = f"plate_sr_x{self.scale}"

    def upscale(self, bgr: np.ndarray) -> np.ndarray:
        import torch
        if bgr is None or bgr.size == 0:
            return bgr
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        with torch.no_grad():
            out = self.model(to_tensor(bgr).to(self.device))
        return to_image(out)
