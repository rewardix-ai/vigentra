"""Stage B step 5: denoise after fusion, before deblur.
Fast path: OpenCV non-local means (BM3D-like quality at plate sizes).
Learned path (optional): a DnCNN/FFDNet ONNX model if present."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


class Denoiser:
    def __init__(self, onnx_path: str | Path | None = None, h: float = 7.0):
        self.h = h
        self.sess = None
        if onnx_path and Path(onnx_path).exists():
            import onnxruntime as ort
            self.sess = ort.InferenceSession(str(onnx_path), providers=_providers())

    def __call__(self, gray: np.ndarray) -> np.ndarray:
        if self.sess is not None:
            x = gray.astype(np.float32)[None, None] / 255.0
            y = self.sess.run(None, {self.sess.get_inputs()[0].name: x})[0][0, 0]
            return np.clip(y * 255.0, 0, 255).astype(np.uint8)
        return cv2.fastNlMeansDenoising(gray, None, h=self.h, templateWindowSize=7, searchWindowSize=21)


def _providers() -> list[str]:
    """ORT provider order. OpenVINO EP is opt-in (ANPR_OPENVINO=1): the wheel
    lists it as available even when openvino.dll is missing, and the failed
    load prints an error on every session before falling back to CPU."""
    import os
    import onnxruntime as ort
    avail = ort.get_available_providers()
    order = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if os.environ.get("ANPR_OPENVINO") == "1":
        order.insert(0, "OpenVINOExecutionProvider")
    return [p for p in order if p in avail]
