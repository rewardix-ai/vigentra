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
