"""The plate reader: a small CRNN trained on Indian plates, worst cases first.

The generic OCR engines read printed text; a number plate is not printed
text. It is 6-11 characters from a 36-symbol alphabet, embossed on a
reflective strip, seen through a junction camera's blur, compression and
night gain at 30-120 px wide. A recogniser trained on exactly that - and on
nothing else - does not have to spend capacity on Devanagari, punctuation or
paragraph layout, and can be taught the failure modes that matter here by
manufacturing them (tools/synthesize_plates.py).

This module holds the pieces the trainer and the OCR engine share: the
alphabet, the input geometry, the network, the preprocessing and the CTC
decode. Keep them here so the engine can never drift from what was trained.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

#: CTC alphabet. Index 0 is the blank; plates never contain anything else.
CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
BLANK = 0
NUM_CLASSES = len(CHARSET) + 1

#: Network input, grayscale. 32 px tall keeps a 40 px-wide real plate from
#: being upsampled past what it holds; 192 wide fits an 11-character plate at
#: ~17 px per glyph with room for the loose boxes a detector hands over.
INPUT_H = 32
INPUT_W = 192
#: Horizontal downsampling of the convolutional stack: 192 -> 48 timesteps,
#: at least four per character for the longest legal plate.
TIME_STEPS = INPUT_W // 4


@dataclass(frozen=True)
class ReaderSpec:
    """What a checkpoint was trained with; stored beside the weights."""
    charset: str = CHARSET
    input_h: int = INPUT_H
    input_w: int = INPUT_W
    hidden: int = 256


def preprocess(bgr_or_gray: np.ndarray) -> np.ndarray:
    """One crop -> float32 [1, INPUT_H, INPUT_W] in [0, 1], aspect preserved.

    The plate is scaled to the input height and right-padded with the image's
    own median (not black), so a padded edge does not read as a dark glyph.
    Wider-than-input crops are squeezed rather than cut: a plate that lost its
    last digit is a different plate.
    """
    img = bgr_or_gray
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = img.shape[:2]
    if h == 0 or w == 0:
        return np.full((1, INPUT_H, INPUT_W), 0.5, np.float32)
    new_w = max(8, int(round(w * INPUT_H / float(h))))
    if new_w > INPUT_W:
        new_w = INPUT_W
    interp = cv2.INTER_AREA if h > INPUT_H else cv2.INTER_CUBIC
    resized = cv2.resize(img, (new_w, INPUT_H), interpolation=interp)
    canvas = np.full((INPUT_H, INPUT_W), int(np.median(resized)), np.uint8)
    canvas[:, :new_w] = resized
    return (canvas.astype(np.float32) / 255.0)[None]


def build_model(spec: ReaderSpec | None = None):
    """The CRNN. Imported lazily so the OCR module does not need torch at import."""
    import torch
    from torch import nn

    spec = spec or ReaderSpec()

    class CRNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()

            def block(cin: int, cout: int, pool: tuple[int, int] | None) -> nn.Sequential:
                layers: list[nn.Module] = [
                    nn.Conv2d(cin, cout, 3, padding=1, bias=False),
                    nn.BatchNorm2d(cout),
                    nn.ReLU(inplace=True),
                ]
                if pool:
                    layers.append(nn.MaxPool2d(pool))
                return nn.Sequential(*layers)

            # H: 32 -> 16 -> 8 -> 4 -> 2 -> 1 ; W: 192 -> 96 -> 48 -> 48 -> 48 -> 48
            self.features = nn.Sequential(
                block(1, 32, (2, 2)),
                block(32, 64, (2, 2)),
                block(64, 128, None),
                block(128, 128, (2, 1)),
                block(128, 256, None),
                block(256, 256, (2, 1)),
                block(256, 256, (2, 1)),
            )
            self.rnn = nn.LSTM(256, spec.hidden, num_layers=2, bidirectional=True,
                               batch_first=True, dropout=0.2)
            self.head = nn.Linear(spec.hidden * 2, len(spec.charset) + 1)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            """[B, 1, H, W] -> log-probabilities [B, T, C]."""
            f = self.features(x)              # [B, 256, 1, T]
            f = f.squeeze(2).permute(0, 2, 1)  # [B, T, 256]
            out, _ = self.rnn(f)
            return torch.log_softmax(self.head(out), dim=-1)

    return CRNN()


def greedy_decode(log_probs: np.ndarray, charset: str = CHARSET) -> tuple[str, float]:
    """Collapse one [T, C] log-probability matrix to (text, confidence).

    Confidence is the geometric mean of the emitted characters' probabilities
    - the per-character certainty an operator would want, not the sequence
    likelihood, which shrinks with plate length for no fault of the read.
    """
    if log_probs.ndim != 2 or log_probs.shape[0] == 0:
        return "", 0.0
    best = log_probs.argmax(axis=1)
    conf = log_probs.max(axis=1)
    chars: list[str] = []
    logs: list[float] = []
    prev = BLANK
    for t, k in enumerate(best):
        if k != BLANK and k != prev:
            chars.append(charset[k - 1])
            logs.append(float(conf[t]))
        prev = k
    if not chars:
        return "", 0.0
    return "".join(chars), float(np.exp(np.mean(logs)))


def fuse_log_probs(matrices: list[np.ndarray]) -> np.ndarray | None:
    """Sum aligned [T, C] log-probability matrices across a track.

    Summing log-probabilities multiplies the per-frame distributions: a
    character one frame is sure about survives the frames that had no
    opinion. The result is renormalised per timestep so the decoder's
    confidence stays on the single-frame scale.
    """
    if not matrices:
        return None
    shapes = {m.shape for m in matrices}
    if len(shapes) != 1:
        return None
    total = np.sum(matrices, axis=0)
    total -= total.max(axis=1, keepdims=True)
    total -= np.log(np.exp(total).sum(axis=1, keepdims=True))
    return total.astype(np.float32)
