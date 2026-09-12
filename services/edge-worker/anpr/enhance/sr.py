"""Stage B step 7: single-image super-resolution, gated (spec 7.3).

Only a plate-specific model (models/plate_sr_x*.onnx, trained on the
in-domain degradation model from tools/synth_plates.py) is allowed to run.
Generic photo SR is not wired in on purpose. If no in-domain model exists the
module returns a Lanczos upscale and reports `learned=False`, so the reader
ensemble treats it as a resample, not as new evidence.

SR output never drives a CONFIRMED read alone: pipeline requires agreement
with the non-SR fused read (see anpr/fuse/rover.py)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class SRResult:
    image: np.ndarray
    scale: int
    learned: bool
    model: str


class SuperResolver:
    def __init__(self, onnx_path: str | Path | None = "models/plate_sr_x4.onnx", scale: int = 4):
        self.scale = scale
        self.sess = None
        self.name = "lanczos"
        if onnx_path and Path(onnx_path).exists():
            import onnxruntime as ort
            from anpr.enhance.denoise import _providers
            self.sess = ort.InferenceSession(str(onnx_path), providers=_providers())
            self.name = Path(onnx_path).name
            self._in = self.sess.get_inputs()[0].name

    def __call__(self, gray: np.ndarray) -> SRResult:
        if self.sess is None:
            up = cv2.resize(gray, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_LANCZOS4)
            return SRResult(up, self.scale, False, self.name)
        x = gray.astype(np.float32)[None, None] / 255.0
        y = self.sess.run(None, {self._in: x})[0][0, 0]
        return SRResult(np.clip(y * 255.0, 0, 255).astype(np.uint8), self.scale, True, self.name)
