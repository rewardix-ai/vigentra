"""Awiros-ANPR-OCR as a plate reader: PP-OCRv5 server recogniser (SVTR_HGNet, PPHGNetV2_B4,
37.3M params) fine-tuned on 558,767 Indian plates incl. two-row ones; Apache-2.0,
https://huggingface.co/Awiros/anpr-ocr (vendor-reported 98.42 % overall / 96.91 % two-row on
their own held-out set). It reads the WHOLE crop - a two-row plate included - in one pass.

Files: models/awiros/{model.safetensors, en_dict.txt} (SHA-256 prefix f9f1264e0c115a23) and the
PaddleOCR model code in third_party/PaddleOCR (git clone --depth 1). CPU inference via
PaddlePaddle. Construction follows the release's test.py.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from anpr.plate_grammar import normalise

ROOT = Path(__file__).resolve().parent.parent.parent
IMAGE_SHAPE = (3, 48, 320)
_ARCH = {
    "model_type": "rec", "algorithm": "SVTR_HGNet", "Transform": None,
    "Backbone": {"name": "PPHGNetV2_B4", "text_rec": True},
    "Head": {"name": "MultiHead",
             "out_channels_list": {"CTCLabelDecode": 64, "NRTRLabelDecode": 67},
             "head_list": [{"CTCHead": {"Neck": {"name": "svtr", "dims": 120, "depth": 2, "hidden_dims": 120,
                                                  "kernel_size": [1, 3], "use_guide": True},
                                         "Head": {"fc_decay": 1e-05}}},
                           {"NRTRHead": {"nrtr_dim": 384, "max_text_length": 25}}]},
}


def _prep(img: np.ndarray) -> np.ndarray:
    """Height 48, aspect kept, width <= 320, right-padded with zeros (the release's preprocessing)."""
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    _, h, w = IMAGE_SHAPE
    nw = max(1, min(int(img.shape[1] * h / max(img.shape[0], 1)), w))
    r = cv2.resize(img, (nw, h), interpolation=cv2.INTER_AREA if img.shape[0] > h else cv2.INTER_CUBIC)
    canvas = np.zeros((h, w, 3), np.uint8)
    canvas[:, :nw] = r
    return ((canvas.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)


class AwirosReader:
    name = "awiros"

    def __init__(self, weights: str | Path = ROOT / "models/awiros/model.safetensors",
                 dict_path: str | Path = ROOT / "models/awiros/en_dict.txt",
                 paddleocr_dir: str | Path = ROOT / "third_party/PaddleOCR", device: str = "cpu"):
        self.ok = False
        self.err: Optional[str] = None
        if not (Path(weights).exists() and Path(dict_path).exists() and (Path(paddleocr_dir) / "ppocr").is_dir()):
            self.err = "missing weights, dictionary or PaddleOCR code"
            return
        if str(paddleocr_dir) not in sys.path:
            sys.path.insert(0, str(paddleocr_dir))
        import paddle
        from ppocr.modeling.architectures import build_model
        from ppocr.postprocess import build_post_process
        from safetensors.numpy import load_file
        self._paddle = paddle
        paddle.set_device(device)
        self.model = build_model(copy.deepcopy(_ARCH))
        self.model.eval()
        self.model.set_state_dict({k: paddle.to_tensor(v) for k, v in load_file(str(weights)).items()})
        self.post = build_post_process({"name": "CTCLabelDecode", "character_dict_path": str(dict_path),
                                        "use_space_char": True})
        self.ok = True

    def read_batch(self, imgs: list[np.ndarray], bs: int = 32) -> list[tuple[str, float]]:
        """[(plate text normalised to A-Z0-9, confidence)] for BGR or gray crops."""
        out: list[tuple[str, float]] = []
        for i in range(0, len(imgs), bs):
            x = self._paddle.to_tensor(np.stack([_prep(im) for im in imgs[i:i + bs]]))
            with self._paddle.no_grad():
                preds = self.model(x)
            p = preds.get("ctc", next(iter(preds.values()))) if isinstance(preds, dict) else \
                preds[0] if isinstance(preds, (list, tuple)) else preds
            for t, c in self.post(p.numpy()):
                out.append((normalise(t), float(c)))
        return out

    def read(self, img: np.ndarray) -> tuple[str, float]:
        return self.read_batch([img])[0]
