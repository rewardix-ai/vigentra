"""PARSeq reader wrapper (FULL profile). Loads an ONNX export produced by
training/colab_reader_parseq.ipynb (input [B,3,32,128] normalised to [-1,1],
output [B,L,C] with C = 36 + EOS as the last column, decoded slot-wise).
If the artifact is absent the reader reports ok=False and the ensemble skips
it, so the LITE path still works."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from anpr.plate_grammar import ALPHABET

IMG_H, IMG_W = 32, 128


class PARSeqReader:
    name = "parseq"
    slot_style = True   # emits one distribution per output slot (not CTC)

    def __init__(self, weights: str | Path = "models/reader_parseq.onnx"):
        self.weights = Path(weights)
        self.ok = False
        self.sess = None
        if self.weights.exists():
            import onnxruntime as ort
            from anpr.enhance.denoise import _providers
            self.sess = ort.InferenceSession(str(self.weights), providers=_providers())
            self._in = self.sess.get_inputs()[0].name
            self.ok = True

    def _pre(self, gray: np.ndarray) -> np.ndarray:
        g = cv2.resize(gray, (IMG_W, IMG_H), interpolation=cv2.INTER_CUBIC)
        x = (g.astype(np.float32) / 255.0 - 0.5) / 0.5
        return np.repeat(x[None, None], 3, axis=1)

    def probs(self, gray_batch: list[np.ndarray]) -> list[np.ndarray]:
        if not self.ok:
            return []
        x = np.concatenate([self._pre(g) for g in gray_batch], 0)
        logits = self.sess.run(None, {self._in: x})[0]
        logits = logits - logits.max(-1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(-1, keepdims=True)
        return [pi for pi in p]

    def __call__(self, gray: np.ndarray) -> np.ndarray:
        r = self.probs([gray])
        return r[0] if r else np.zeros((0, len(ALPHABET) + 1), np.float32)
