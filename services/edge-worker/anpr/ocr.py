"""OCR engines and the ensemble that combines them.

Every enhancement variant of a plate is read by every available engine, and
each reading is pushed through the grammar engine to become a scored
candidate.  Nothing here decides *the* answer - that is the consensus layer's
job.  The goal at this level is simply to generate as many independent,
honestly-scored hypotheses as cheaply as possible.

Engines are optional by design: a missing dependency logs a warning and the
pipeline carries on with whatever is installed, rather than refusing to run.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np

from . import plate_rules as pr
from .config import OcrConfig

log = logging.getLogger(__name__)

#: One raw reading: the text an engine produced and how sure it was.
Reading = tuple[str, float]


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def read_batch(self, images: Sequence[np.ndarray],
                   allow_fallback: bool = True) -> list[list[Reading]]:
        """Return, per input image, every text line the engine found."""
        ...


# --------------------------------------------------------------------------
# PaddleOCR
# --------------------------------------------------------------------------

class PaddleEngine:
    """PaddleOCR - the primary recogniser.

    Two paths, chosen per crop:

    * **recognition only** on the crop as given.  The plate detector already
      handed us a tight box, so running a text *detector* over it again is
      mostly wasted work.  This is the fast path and handles the vast
      majority of plates.
    * **detection + recognition**, used only when the fast path returns
      something implausible.  That is the signature of a stacked two-row
      plate (common on Indian two-wheelers and commercial vehicles), where
      recognition alone smears both rows into nonsense; the detector splits
      the rows so they can be read and re-joined.
    """

    name = "paddle"

    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self._rec = None          # fast path: recognition only
        self._ocr = None          # fallback: full det+rec pipeline
        self._api = ""            # "v3" | "v2"
        self._full_failed = False
        self._lock = threading.Lock()
        self._load_rec()
        self._load()

    def _load_rec(self) -> None:
        """Load the recognition-only predictor used for most crops."""
        try:
            from paddleocr import TextRecognition
        except ImportError:
            log.info("TextRecognition unavailable; using the full pipeline only")
            return
        try:
            self._rec = TextRecognition(model_name=self.cfg.paddle_rec_model)
            log.info("Paddle recogniser: %s", self.cfg.paddle_rec_model)
        except Exception as exc:                # noqa: BLE001 - fall back below
            log.warning("could not load recogniser %s: %s",
                        self.cfg.paddle_rec_model, exc)

    def _load(self) -> None:
        if not self.cfg.paddle_fallback_det and self._rec is not None:
            return
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            log.warning("PaddleOCR unavailable (%s); this engine is disabled", exc)
            return

        use_gpu = self.cfg.paddle_device not in ("cpu", "")
        # PaddleOCR 3.x renamed nearly every constructor argument and made the
        # document-level preprocessors default-on.  Those preprocessors are
        # actively harmful on a 64 px plate crop, so they are turned off.
        # enable_mkldnn=False is not a performance preference: PaddlePaddle's
        # oneDNN path raises ConvertPirAttribute2RuntimeAttribute on the
        # PP-OCRv4/v5/v6 detection models on Windows CPU, so the whole engine
        # is unusable with it on.
        kwargs = dict(
            lang=self.cfg.paddle_lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device="gpu" if use_gpu else "cpu",
        )
        if not use_gpu:
            kwargs["enable_mkldnn"] = False
        if self.cfg.paddle_fallback_det_model:
            kwargs["text_detection_model_name"] = self.cfg.paddle_fallback_det_model
        if self.cfg.paddle_fallback_rec_model:
            kwargs["text_recognition_model_name"] = self.cfg.paddle_fallback_rec_model
        try:
            self._ocr = PaddleOCR(**kwargs)
            self._api = "v3"
            log.info("PaddleOCR 3.x loaded on %s (mkldnn=%s)",
                     "gpu" if use_gpu else "cpu", kwargs.get("enable_mkldnn", True))
            return
        except (TypeError, ValueError) as exc:
            log.debug("PaddleOCR 3.x constructor rejected: %s", exc)

        try:
            self._ocr = PaddleOCR(lang=self.cfg.paddle_lang, use_angle_cls=False,
                                  show_log=False, use_gpu=use_gpu)
            self._api = "v2"
            log.info("PaddleOCR 2.x loaded on %s", "gpu" if use_gpu else "cpu")
        except Exception as exc:                # noqa: BLE001 - optional engine
            log.warning("PaddleOCR failed to initialise: %s", exc)
            self._ocr = None

    def available(self) -> bool:
        return self._rec is not None or self._ocr is not None

    def read_batch(self, images: Sequence[np.ndarray],
                   allow_fallback: bool = True) -> list[list[Reading]]:
        if not self.available() or not images:
            return [[] for _ in images]
        # Paddle's predictors are not thread-safe; serialise access.
        with self._lock:
            out: list[list[Reading]] = [[] for _ in images]
            needs_det: list[int] = []

            if self._rec is not None:
                # One batched call for every variant at once - the per-call
                # overhead dominates at this crop size, so batching matters
                # more than the model's own speed.
                for i, readings in enumerate(self._rec_batch(images)):
                    out[i] = readings
                    if (not any(pr.plausible(t) for t, _ in readings)
                            and _looks_stacked(images[i])):
                        needs_det.append(i)
            else:
                needs_det = list(range(len(images)))

            # Escalate at most once per plate.  The detector costs ~100 ms a
            # call, so running it on every variant of every unreadable crop
            # dominates the frame budget for no gain - if the fast path could
            # not read a *single-row* crop, detection will not rescue it.
            if (allow_fallback and needs_det and self._ocr is not None
                    and not self._full_failed):
                i = needs_det[0]
                out[i] = out[i] + self._read_full(images[i])
            return out

    def _rec_batch(self, images: Sequence[np.ndarray]) -> list[list[Reading]]:
        try:
            results = list(self._rec.predict(list(images)))
        except Exception as exc:                # noqa: BLE001 - never kill a frame
            log.debug("paddle recognition failed: %s", exc)
            return [[] for _ in images]

        out: list[list[Reading]] = []
        for page in results:
            data = page if isinstance(page, dict) else getattr(page, "json", {})
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            text = (data or {}).get("rec_text", "")
            score = float((data or {}).get("rec_score", 0.0))
            out.append([(str(text), score)] if text else [])
        # Guard against the predictor returning fewer rows than we sent.
        while len(out) < len(images):
            out.append([])
        return out

    def _read_full(self, img: np.ndarray) -> list[Reading]:
        try:
            if self._api == "v3":
                return self._parse_v3(self._ocr.predict(img))
            return self._parse_v2(self._ocr.ocr(img, cls=False))
        except Exception as exc:                # noqa: BLE001 - never kill a frame
            log.debug("paddle full-pipeline read failed: %s", exc)
            return []

    @staticmethod
    def _parse_v3(result) -> list[Reading]:
        """3.x returns one dict per image with parallel text/score lists."""
        out: list[Reading] = []
        for page in result or []:
            data = page if isinstance(page, dict) else getattr(page, "json", None)
            if isinstance(data, dict) and "res" in data:
                data = data["res"]
            if not isinstance(data, dict):
                continue
            texts = data.get("rec_texts") or []
            scores = data.get("rec_scores") or []
            polys = data.get("rec_polys") or data.get("dt_polys") or []
            out.extend(_order_lines(texts, scores, polys))
        return out

    @staticmethod
    def _parse_v2(result) -> list[Reading]:
        """2.x returns [[ [box, (text, score)], ... ]]."""
        out: list[Reading] = []
        for page in result or []:
            if not page:
                continue
            texts, scores, polys = [], [], []
            for line in page:
                try:
                    box, (text, score) = line[0], line[1]
                except (TypeError, ValueError, IndexError):
                    continue
                texts.append(text)
                scores.append(float(score))
                polys.append(box)
            out.extend(_order_lines(texts, scores, polys))
        return out


#: Height/width above which a plate crop is probably a stacked two-row plate.
#: A single-row Indian plate is roughly 4.5:1 (ratio ~0.22); a two-row plate
#: is roughly 2:1 (ratio ~0.5).
STACKED_ASPECT = 0.38


def _looks_stacked(img: np.ndarray) -> bool:
    """True when the crop's shape suggests two rows of characters."""
    if img is None or img.size == 0:
        return False
    h, w = img.shape[:2]
    return w > 0 and (h / w) > STACKED_ASPECT


def _order_lines(texts: Sequence[str], scores: Sequence[float],
                 polys: Sequence) -> list[Reading]:
    """Turn detected text lines into plate hypotheses.

    A plate crop yields either one line, or two when it is a stacked
    two-row plate.  We emit each line on its own *and* the top-to-bottom
    concatenation, because we cannot know in advance which the plate is -
    and the grammar engine will discard whichever does not parse.
    """
    if not texts:
        return []

    items = []
    for i, text in enumerate(texts):
        score = float(scores[i]) if i < len(scores) else 0.0
        y = x = 0.0
        if i < len(polys) and polys[i] is not None:
            try:
                pts = np.asarray(polys[i], dtype=np.float32).reshape(-1, 2)
                y, x = float(pts[:, 1].mean()), float(pts[:, 0].mean())
            except (ValueError, TypeError):
                pass
        items.append((y, x, str(text), score))

    items.sort(key=lambda t: (t[0], t[1]))       # top-to-bottom, left-to-right
    out: list[Reading] = [(t, s) for _, _, t, s in items]

    if len(items) > 1:
        joined = "".join(t for _, _, t, _ in items)
        mean = sum(s for *_, s in items) / len(items)
        out.append((joined, mean))
    return out


# --------------------------------------------------------------------------
# Tesseract (optional third opinion)
# --------------------------------------------------------------------------

class TesseractEngine:
    """Cheap, independent second opinion when the binary happens to exist."""

    name = "tesseract"
    #: single line of text, restricted to the plate alphabet
    CONFIG = ("--oem 1 --psm 7 "
              "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self._pt = None
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self._pt = pytesseract
            log.info("Tesseract engine available")
        except Exception as exc:                # noqa: BLE001 - optional engine
            log.info("Tesseract not available (%s); engine disabled", exc)

    def available(self) -> bool:
        return self._pt is not None

    def read_batch(self, images: Sequence[np.ndarray],
                   allow_fallback: bool = True) -> list[list[Reading]]:
        if not self.available():
            return [[] for _ in images]
        out = []
        for img in images:
            try:
                data = self._pt.image_to_data(
                    img, config=self.CONFIG,
                    output_type=self._pt.Output.DICT)
                words, confs = [], []
                for text, conf in zip(data["text"], data["conf"]):
                    text = (text or "").strip()
                    conf = float(conf)
                    if text and conf >= 0:
                        words.append(text)
                        confs.append(conf / 100.0)
                out.append([("".join(words), sum(confs) / len(confs))] if words else [])
            except Exception as exc:            # noqa: BLE001
                log.debug("tesseract read failed: %s", exc)
                out.append([])
        return out


# --------------------------------------------------------------------------
# Ensemble
# --------------------------------------------------------------------------

@dataclass
class OcrResult:
    """Everything read from one plate crop, across variants and engines."""
    candidates: list[pr.PlateCandidate]
    best: pr.PlateCandidate | None
    #: raw per-engine/variant readings, kept for the UI's debug view
    trace: list[dict]

    def as_dict(self) -> dict:
        return {
            "best": self.best.as_dict() if self.best else None,
            "candidates": [c.as_dict() for c in self.candidates[:8]],
            "trace": self.trace[:16],
        }


class RemoteEngine:
    """PaddleOCR running on the GPU, in its own process.

    CUDA torch and CUDA paddle cannot share a process: both ship their own
    ``cudnn64_9.dll`` at incompatible builds and whichever loads second fails
    with WinError 127, in either order.  Since detection needs torch on the
    GPU and recognition wants paddle on the GPU, the only way to have both is
    to keep them in separate processes.

    The worker lives in its own virtualenv (``ocr.worker_python``) and is
    spoken to over a pipe.  If it cannot start, this engine simply reports
    itself unavailable and the ensemble falls back to in-process CPU paddle.
    """

    name = "paddle-gpu"

    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self._proc = None
        self._lock = threading.Lock()
        self.info: dict = {}
        self._start()

    def _start(self) -> None:
        import subprocess
        from pathlib import Path

        python = Path(self.cfg.worker_python)
        if not python.is_absolute():
            python = Path(__file__).resolve().parent.parent / python
        script = Path(__file__).resolve().parent.parent / "scripts" / "ocr_worker.py"
        if not python.exists() or not script.exists():
            log.info("GPU OCR worker not configured (%s); using in-process OCR",
                     python)
            return

        try:
            self._proc = subprocess.Popen(
                [str(python), "-u", str(script), self.cfg.worker_device,
                 self.cfg.paddle_rec_model, self.cfg.paddle_fallback_det_model],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)
            hello = self._recv(timeout_hint="startup")
            if not hello or not hello.get("ready"):
                raise RuntimeError((hello or {}).get("error", "worker did not start"))
            self.info = hello
            log.info("GPU OCR worker ready: paddle %s on %s",
                     hello.get("paddle"), hello.get("device"))
        except Exception as exc:                # noqa: BLE001 - optional engine
            log.warning("GPU OCR worker unavailable (%s); using in-process OCR", exc)
            self._kill()

    # -- pipe protocol ---------------------------------------------------
    def _send(self, obj: dict) -> None:
        import pickle
        import struct
        payload = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        self._proc.stdin.write(struct.pack("<I", len(payload)))
        self._proc.stdin.write(payload)
        self._proc.stdin.flush()

    def _recv(self, timeout_hint: str = "") -> dict | None:
        import pickle
        import struct
        header = self._proc.stdout.read(4)
        if len(header) < 4:
            return None
        (size,) = struct.unpack("<I", header)
        buf = b""
        while len(buf) < size:
            chunk = self._proc.stdout.read(size - len(buf))
            if not chunk:
                return None
            buf += chunk
        return pickle.loads(buf)

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
        except Exception:                       # noqa: BLE001
            pass

    def available(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def read_batch(self, images: Sequence[np.ndarray],
                   allow_fallback: bool = True) -> list[list[Reading]]:
        if not self.available() or not images:
            return [[] for _ in images]
        with self._lock:
            try:
                self._send({"images": list(images),
                            "escalate": self._escalate_index(images, allow_fallback)})
                reply = self._recv()
            except Exception as exc:            # noqa: BLE001
                log.warning("GPU OCR worker died: %s", exc)
                self._kill()
                return [[] for _ in images]
        if reply is None:
            log.warning("GPU OCR worker closed the pipe")
            self._kill()
            return [[] for _ in images]
        readings = reply.get("readings") or []
        out = [[(str(t), float(s)) for t, s in r] for r in readings]
        while len(out) < len(images):
            out.append([])
        return out

    @staticmethod
    def _escalate_index(images: Sequence[np.ndarray],
                        allow_fallback: bool) -> int | None:
        """Which crop, if any, is worth the det+rec escalation.

        Same rule as the in-process engine: only a crop shaped like a stacked
        two-row plate, and only one per call.  Detection will not rescue a
        single-row crop the recogniser already failed on.
        """
        if not allow_fallback:
            return None
        for i, img in enumerate(images):
            if _looks_stacked(img):
                return i
        return None

    def close(self) -> None:
        if self.available():
            with contextlib.suppress(Exception):
                self._send({"stop": True})
        self._kill()


ENGINE_TYPES = {
    "paddle": PaddleEngine,
    "paddle-gpu": RemoteEngine,
    "tesseract": TesseractEngine,
}


class OcrEnsemble:
    """Runs every configured engine over every enhancement variant."""

    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self.engines: list[OcrEngine] = []
        for name in cfg.engines:
            factory = ENGINE_TYPES.get(name)
            if factory is None:
                log.warning("unknown OCR engine %r - skipped", name)
                continue
            engine = factory(cfg)
            if engine.available():
                self.engines.append(engine)
            else:
                log.warning("OCR engine %r is not usable - skipped", name)
        if not self.engines:
            log.error("no OCR engine available; plates will be detected but not read")

    @property
    def engine_names(self) -> list[str]:
        return [e.name for e in self.engines]

    def read(self, variants: Sequence[tuple[str, np.ndarray]],
             allow_fallback: bool = True) -> OcrResult:
        """Read one plate through all variants and engines.

        *allow_fallback* lets the caller forbid the expensive detection
        escalation for crops it knows are not worth it.
        """
        if not variants or not self.engines:
            return OcrResult([], None, [])

        names = [n for n, _ in variants]
        images = [img for _, img in variants]

        candidates: list[pr.PlateCandidate] = []
        trace: list[dict] = []

        for engine in self.engines:
            try:
                per_image = engine.read_batch(images, allow_fallback=allow_fallback)
            except Exception as exc:            # noqa: BLE001 - isolate engines
                log.warning("engine %s crashed on batch: %s", engine.name, exc)
                continue
            for variant, readings in zip(names, per_image):
                for raw, conf in readings:
                    trace.append({"engine": engine.name, "variant": variant,
                                  "raw": raw, "conf": round(float(conf), 3)})
                    if conf < self.cfg.min_confidence:
                        continue
                    if not pr.plausible(raw):
                        continue
                    candidates.append(pr.normalise(
                        raw, float(conf), engine=engine.name, variant=variant))

        candidates.sort(key=lambda c: (c.valid, c.score), reverse=True)
        return OcrResult(candidates, pr.best_of(candidates), trace)
