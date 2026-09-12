"""Claude as a plate reader (teacher for distillation, optional runtime verifier).

One call = one plate. The model gets the best raw crop and the fused crop, upscaled,
and must answer with strict JSON. Everything it returns is validated against the
Indian plate grammar before it is used; anything not marked "certain" is dropped.

Credentials: ANTHROPIC_API_KEY (or an `ant auth login` profile). Model: claude-opus-5.
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from anpr.plate_grammar import normalise, score_string

MODEL = "claude-opus-5"
SYSTEM = (
    "You are an expert at reading Indian vehicle number plates from CCTV crops. "
    "You see one plate: the raw crop and a multi-frame fused version of the same plate. "
    "Read the registration exactly as printed (state code, RTO digits, series letters, number; "
    "Bharat series like 22BH1234AA also exists). Two-row plates read top row then bottom row. "
    "Ignore anything that is not the registration (IND, state names, dealer text, timestamps, signs). "
    "If any character is not clearly readable, do not guess it. "
    "Answer ONLY with JSON: {\"plate\": \"<A-Z0-9 only, no spaces>\" or null, "
    "\"certainty\": \"certain\" | \"probable\" | \"unreadable\", "
    "\"is_plate\": true | false, \"rows\": 1 | 2, \"note\": \"<short>\"}"
)
_JSON = re.compile(r"\{.*\}", re.S)


def _png_b64(img: np.ndarray, scale_to_w: int = 512) -> str:
    h, w = img.shape[:2]
    if w < scale_to_w:
        s = scale_to_w / max(w, 1)
        img = cv2.resize(img, (int(w * s), max(1, int(h * s))), interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".png", img)
    return base64.standard_b64encode(buf.tobytes()).decode("ascii")


@dataclass
class TeacherRead:
    plate: Optional[str]
    certainty: str
    is_plate: bool
    rows: int
    note: str
    raw: str
    valid: bool
    input_tokens: int = 0
    output_tokens: int = 0


class ClaudeReader:
    def __init__(self, model: str = MODEL, max_retries: int = 3, effort: str = "medium"):
        import anthropic  # imported here so the pipeline never needs the SDK unless this is used
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=max_retries)
        self.model = model
        self.effort = effort
        self.n_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def build_content(self, best_bgr: np.ndarray, fused: Optional[np.ndarray]) -> list[dict]:
        content = [{"type": "text", "text": "Raw crop:"},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _png_b64(best_bgr)}}]
        if fused is not None:
            content += [{"type": "text", "text": "Multi-frame fused crop of the same plate:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _png_b64(fused)}}]
        content.append({"type": "text", "text": "Read the plate. JSON only."})
        return content

    def read(self, best_bgr: np.ndarray, fused: Optional[np.ndarray] = None) -> TeacherRead:
        content = self.build_content(best_bgr, fused)
        resp = self.client.messages.create(
            model=self.model, max_tokens=256,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": content}],
        )
        self.n_calls += 1
        self.input_tokens += resp.usage.input_tokens + (resp.usage.cache_read_input_tokens or 0)
        self.output_tokens += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            return TeacherRead(None, "unreadable", False, 1, "refusal", "", False)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return parse_answer(text, resp.usage.input_tokens, resp.usage.output_tokens)


def parse_answer(text: str, in_tok: int = 0, out_tok: int = 0) -> TeacherRead:
    m = _JSON.search(text or "")
    if not m:
        return TeacherRead(None, "unreadable", False, 1, "unparsable", text, False, in_tok, out_tok)
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return TeacherRead(None, "unreadable", False, 1, "unparsable", text, False, in_tok, out_tok)
    plate = d.get("plate")
    plate = normalise(str(plate)) if plate else None
    valid = bool(plate) and score_string(plate).valid
    return TeacherRead(plate if valid else (plate or None), str(d.get("certainty", "unreadable")), bool(d.get("is_plate", bool(plate))),
                       int(d.get("rows", 1) or 1), str(d.get("note", ""))[:120], text, valid, in_tok, out_tok)


def estimate_cost_usd(n_crops: int, tokens_per_call: int = 900, out_per_call: int = 60,
                      price_in: float = 5.0, price_out: float = 25.0) -> float:
    """Rough budget: two ~512-px images (~2 x 350 tokens) + prompt per call on claude-opus-5."""
    return n_crops * (tokens_per_call * price_in + out_per_call * price_out) / 1e6
