"""CRNN + CTC reader (LITE profile). Restricted alphabet A-Z0-9 + blank.
Emits per-timestep probability matrices; decoding happens in beam_grammar.

The same module defines the training-time network (torch) and the runtime
wrapper that loads either a .pt checkpoint or an exported .onnx.
Input size is configurable (default 32x128; 64x256 gives ~2x pixels per glyph
for the digit-vs-digit confusions measured on real plates). The height must be
a power of two >= 32; the network pools height down to 1 and width down to
W/4, so T = W/4 timesteps. Checkpoints store their input size; ONNX exports
carry it in the input shape, so the runtime wrapper adapts automatically.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from anpr.plate_grammar import ALPHABET

IMG_H, IMG_W = 32, 128          # defaults (v1/v2 checkpoints)
N_CLASSES = len(ALPHABET) + 1   # + blank (last)


def build_model(img_h: int = IMG_H, hidden: int = 128, width: int = 1):
    import torch
    import torch.nn as nn

    class CRNN(nn.Module):
        def __init__(self, n_classes: int = N_CLASSES):
            super().__init__()
            def block(i, o, pool):
                layers = [nn.Conv2d(i, o, 3, 1, 1), nn.BatchNorm2d(o), nn.ReLU(inplace=True)]
                if pool:
                    layers.append(nn.MaxPool2d(pool))
                return layers
            c = [32, 64, 128, 128, 256]
            c = [int(x * width) for x in c]
            layers = []
            layers += block(1, c[0], (2, 2))          # H/2,  W/2
            layers += block(c[0], c[1], (2, 2))       # H/4,  W/4
            layers += block(c[1], c[2], (2, 1))       # H/8
            layers += block(c[2], c[3], (2, 1))       # H/16
            layers += block(c[3], c[4], (2, 1))       # H/32
            h = img_h // 32
            ch = c[4]
            while h > 1:                              # extra height-only stages for taller inputs
                layers += block(ch, ch, (2, 1))
                h //= 2
            self.cnn = nn.Sequential(*layers)
            self.rnn = nn.LSTM(ch, hidden, num_layers=2, bidirectional=True, batch_first=True, dropout=0.1)
            self.fc = nn.Linear(hidden * 2, n_classes)

        def forward(self, x):            # x: [B,1,H,W]
            f = self.cnn(x)              # [B,C,1,W/4]
            f = f.squeeze(2).permute(0, 2, 1)  # [B,T,C]
            f, _ = self.rnn(f)
            return self.fc(f)            # [B,T,n_classes] logits

    return CRNN()


def preprocess(gray: np.ndarray, img_h: int = IMG_H, img_w: int = IMG_W) -> np.ndarray:
    g = cv2.resize(gray, (img_w, img_h), interpolation=cv2.INTER_AREA if gray.shape[1] > img_w else cv2.INTER_CUBIC)
    x = g.astype(np.float32) / 255.0
    x = (x - 0.5) / 0.5
    return x[None, None]


class CRNNReader:
    name = "crnn"

    def __init__(self, weights: str | Path = "models/reader_crnn.onnx", device: str = "auto"):
        self.weights = Path(weights)
        self.sess = None
        self.model = None
        self.ok = False
        self.img_h, self.img_w = IMG_H, IMG_W
        # how this reader was trained on two-row plates: "split" (v1-v3b: each row is its own
        # sample, runtime reads rows separately) or "side_by_side" (v4+: rows joined into one
        # line, see anpr/read/rows.side_by_side). Stored in the checkpoint / ONNX metadata.
        self.two_row_mode = "split"
        if not self.weights.exists():
            return
        if self.weights.suffix == ".onnx":
            import onnxruntime as ort
            from anpr.enhance.denoise import _providers
            self.sess = ort.InferenceSession(str(self.weights), providers=_providers())
            inp = self.sess.get_inputs()[0]
            self._in = inp.name
            shp = inp.shape
            if isinstance(shp[2], int) and isinstance(shp[3], int):
                self.img_h, self.img_w = int(shp[2]), int(shp[3])
            self.two_row_mode = self.sess.get_modelmeta().custom_metadata_map.get("two_row_mode", "split")
        else:
            import torch
            sd = torch.load(str(self.weights), map_location="cpu")
            self.img_h = int(sd.get("img_h", IMG_H)) if isinstance(sd, dict) else IMG_H
            self.img_w = int(sd.get("img_w", IMG_W)) if isinstance(sd, dict) else IMG_W
            self.two_row_mode = sd.get("two_row_mode", "split") if isinstance(sd, dict) else "split"
            self.model = build_model(self.img_h, int(sd.get("hidden", 128)), float(sd.get("width", 1)))
            self.model.load_state_dict(sd.get("model", sd))
            self.model.eval()
            from anpr.detect.vehicle import _resolve_device
            dev = _resolve_device(device)
            self.dev = "cuda" if dev == "0" else "cpu"
            self.model.to(self.dev)
        self.ok = True

    def probs(self, gray_batch: list[np.ndarray]) -> list[np.ndarray]:
        """Return list of [T, C] softmax matrices."""
        if not self.ok:
            return [np.full((1, N_CLASSES), 1.0 / N_CLASSES, np.float32) for _ in gray_batch]
        x = np.concatenate([preprocess(g, self.img_h, self.img_w) for g in gray_batch], 0)
        if self.sess is not None:
            logits = self.sess.run(None, {self._in: x})[0]
        else:
            import torch
            with torch.no_grad():
                logits = self.model(torch.from_numpy(x).to(self.dev)).cpu().numpy()
        logits = logits - logits.max(-1, keepdims=True)
        p = np.exp(logits)
        p /= p.sum(-1, keepdims=True)
        return [pi for pi in p]

    def __call__(self, gray: np.ndarray) -> np.ndarray:
        return self.probs([gray])[0]
