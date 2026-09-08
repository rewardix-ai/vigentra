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

import cv2
import numpy as np

from . import layout as lay
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
            #
            # Running it on EVERY plate was measured and rejected: it cost 75%
            # more time for one extra near-miss across eight ground-truth crops,
            # and turned one already-close read (GJ11GO1008) into a worse one.
            if (allow_fallback and needs_det and self._ocr is not None
                    and not self._full_failed):
                i = needs_det[0]
                out[i] = out[i] + self._read_full(images[i])
            return out

    def read_track_fused(self, images: Sequence[np.ndarray]) -> Reading | None:
        """Decode ONE plate from every frame of a track, by summing logits.

        The usual path reads each frame separately and then votes on the
        resulting strings. That throws away the thing that makes a track worth
        more than its best frame: the evidence is *complementary per
        character*. Frame 2 may be certain about position 3 and hopeless at
        position 6, frame 5 the reverse. Once each frame has collapsed to a
        string, position 3's certainty and position 6's are indistinguishable -
        both are just one vote for one glyph.

        Summing before the decode keeps them separate. The recogniser emits a
        [timestep, vocabulary] score matrix per frame; adding those across the
        track and running CTC once lets a character that only one frame was
        sure about still win its slot. This is what the ICPR 2026 LRLPR winner
        did with the five frames of each track, and the organisers found
        track-structure fusion - not super-resolution, not model size - was
        what the strongest submissions had in common.

        All frames go through the recogniser in a single batch, which matters:
        the preprocessor normalises widths against the batch's own aspect
        ratios, so one call is what guarantees the matrices share a timestep
        axis and can be added at all.

        Returns None when the engine cannot be reached this way; the caller
        keeps its per-frame result in that case.
        """
        if not images or self._rec is None:
            return None
        predictor = getattr(self._rec, "paddlex_predictor", None)
        if predictor is None:
            return None
        try:
            pre = predictor.pre_tfs
            aligned = _align_track([np.ascontiguousarray(i) for i in images])
            if aligned is None:
                return None
            raw = pre["Read"](imgs=aligned)
            batch = pre["ToBatch"](imgs=pre["ReisizeNorm"](imgs=raw))
            preds = predictor.runner(x=batch)
            matrix = np.asarray(preds[0] if isinstance(preds, (list, tuple)) else preds)
            if matrix.ndim != 3 or matrix.shape[0] < 2:
                return None

            ratios = [img.shape[1] / float(img.shape[0]) for img in raw]
            # Sum in LOG space, not probability space.
            #
            # The recogniser hands back post-softmax probabilities, and adding
            # those is an average: five frames each get an equal vote, so one
            # confident reading is dragged down by four hopeless ones. Adding
            # log-probabilities multiplies the distributions instead, which is
            # the joint likelihood over the track - a character one frame is
            # certain about survives the frames that had no opinion, which is
            # the complementarity the whole exercise is after. This is what
            # "sum the logits" means when what you are given is probabilities.
            # Measured on 14 synthetic tracks: 28.6% summing probabilities,
            # against the numbers in the module test for log space.
            log_sum = np.log(np.clip(matrix, 1e-9, None)).sum(axis=0, keepdims=True)
            log_sum -= log_sum.max(axis=-1, keepdims=True)
            summed = np.exp(log_sum)
            summed /= np.clip(summed.sum(axis=-1, keepdims=True), 1e-9, None)
            summed = summed.astype(np.float32)
            texts, scores = predictor.post_op(
                [summed], wh_ratio_list=ratios[:1], max_wh_ratio=max(ratios)
            )
            if not texts:
                return None
            # post_op sums the per-timestep confidences it decoded, so the
            # score comes back scaled by the number of frames fused. Divide it
            # back down or every fused reading outranks every single-frame one
            # on arithmetic alone.
            # Re-normalised back to a distribution, so the decoder's score is
            # already on a 0..1 scale and must not be divided by frame count.
            confidence = float(scores[0])
            return str(texts[0]), min(1.0, max(0.0, confidence))
        except Exception as exc:  # noqa: BLE001 - never break the per-frame path
            log.debug("logit fusion unavailable (%s); using per-frame reads", exc)
            return None

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
    """True when the crop's shape suggests two rows of characters.

    Kept as the cheap gate on the expensive detect-then-recognise escalation.
    `layout.classify()` is the considered answer and reads the ink rather than
    the shape, but it costs a threshold and a projection per call, and this is
    asked once per variant per crop. Shape is enough to decide whether the
    considered answer is worth computing.
    """
    if img is None or img.size == 0:
        return False
    h, w = img.shape[:2]
    return w > 0 and (h / w) > STACKED_ASPECT


def read_stacked(engine: "OcrEngine", crop: np.ndarray) -> list[Reading]:
    """Read a stacked plate one band at a time, joined in reading order.

    The escalation path in `read_batch` gets there eventually - it hands the
    crop to a full text detector, which finds the two rows and `_order_lines`
    re-joins them. That works and it costs a detector pass, and it only runs
    after the fast path has already failed and produced a wrong answer that
    the grammar had to reject.

    Splitting on the ink profile reaches the same place without either cost:
    two ordinary recognition calls on two single-line crops, which is what
    every recogniser here is actually good at. The joined reading is emitted
    alongside the individual bands, because a plate whose upper row is the
    state code and lower row the number is only legal when joined, while a
    misclassified single-line crop is only legal un-split - and the grammar
    discards whichever of the two does not parse.
    """
    verdict = lay.classify(crop)
    if not verdict.is_two_line:
        return []
    bands = lay.split_bands(crop, verdict)
    if len(bands) < 2:
        return []

    per_band = engine.read_batch(bands, allow_fallback=False)
    out: list[Reading] = []
    best: list[Reading] = []
    for readings in per_band:
        if not readings:
            best.append(("", 0.0))
            continue
        top = max(readings, key=lambda r: r[1])
        best.append(top)
        out.extend(readings)

    if all(text for text, _ in best):
        joined = "".join(text for text, _ in best)
        mean = sum(score for _, score in best) / len(best)
        out.append((joined, mean))
    return out


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
# The plate reader
# --------------------------------------------------------------------------

class ReaderEngine:
    """The CRNN from anpr/reader.py, trained on plates rather than on text.

    Reads every variant in one batch and, because its output is a CTC score
    matrix on a fixed 48-column grid, can also decode a whole track at once:
    the log-probabilities of each frame are summed after alignment and decoded
    once, so a character only one frame was sure about still wins its slot.
    That is the fusion the Paddle head could not support (see
    OcrConfig.fuse_track_logits); here the head is ours.
    """

    name = "reader"

    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._device = None
        self._lock = threading.Lock()
        try:
            import torch
            from .config import resolve_model
            from . import reader as rd
            path = resolve_model(cfg.reader_model)
            ck = torch.load(path, map_location="cpu", weights_only=False)
            spec = rd.ReaderSpec(**ck.get("spec", {}))
            model = rd.build_model(spec)
            model.load_state_dict(ck["model"])
            model.eval()
            device = torch.device(cfg.reader_device if cfg.reader_device != "auto"
                                  else ("cuda" if torch.cuda.is_available() else "cpu"))
            self._model = model.to(device)
            self._device = device
            self._rd = rd
            log.info("plate reader loaded from %s on %s (epoch %s)", path, device, ck.get("epoch"))
        except Exception as exc:                # noqa: BLE001 - optional engine
            log.info("plate reader not available (%s); engine disabled", exc)

    def available(self) -> bool:
        return self._model is not None

    def _matrices(self, images: Sequence[np.ndarray]) -> np.ndarray:
        import torch
        x = np.stack([self._rd.preprocess(im) for im in images])
        with self._lock, torch.no_grad():
            out = self._model(torch.from_numpy(x).to(self._device))
        return out.float().cpu().numpy()

    def read_batch(self, images: Sequence[np.ndarray],
                   allow_fallback: bool = True) -> list[list[Reading]]:
        if not self.available() or not images:
            return [[] for _ in images]
        try:
            mats = self._matrices(images)
        except Exception as exc:                # noqa: BLE001
            log.debug("reader batch failed: %s", exc)
            return [[] for _ in images]
        out: list[list[Reading]] = []
        for m in mats:
            text, conf = self._rd.greedy_decode(m)
            out.append([(text, conf)] if text else [])
        return out

    def read_track_fused(self, images: Sequence[np.ndarray]) -> Reading | None:
        """One decode over a track: aligned frames, summed log-probabilities."""
        if not self.available() or len(images) < 2:
            return None
        try:
            aligned = _align_track([np.ascontiguousarray(i) for i in images])
            if aligned is None or len(aligned) < 2:
                return None
            fused = self._rd.fuse_log_probs(list(self._matrices(aligned)))
            if fused is None:
                return None
            text, conf = self._rd.greedy_decode(fused)
            return (text, conf) if text else None
        except Exception as exc:                # noqa: BLE001
            log.debug("reader fusion failed: %s", exc)
            return None


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
    "reader": ReaderEngine,
    "paddle": PaddleEngine,
    "paddle-gpu": RemoteEngine,
    "tesseract": TesseractEngine,
}


def _align_track(crops: "list[np.ndarray]") -> "list[np.ndarray] | None":
    """Put every crop of a track on the same grid before their logits are added.

    Summing a recogniser's output across frames is only meaningful if a given
    character lands on the same timesteps in each of them. CTC output is a
    sequence over horizontal position, so a plate sitting eight pixels further
    right in one frame contributes its glyphs to the wrong columns, and the sum
    is a smear of two misaligned readings rather than reinforced evidence.

    Measured, on 14 synthetic tracks: fusing unaligned crops scored 14.3%
    against 92.9% for plain string voting - far worse than not fusing at all.
    The competition's tracks are pre-cropped to the plate, so alignment is
    handed to them; here it has to be earned.

    ECC on the grayscale gives a translation that survives the blur and
    blocking these crops carry. Frames that will not converge are dropped
    rather than fused misaligned, and if too few survive the caller falls back
    to per-frame reads.
    """
    if len(crops) < 2:
        return None
    target_h = 48
    scaled = []
    for crop in crops:
        h, w = crop.shape[:2]
        if h < 4 or w < 4:
            continue
        width = max(16, int(round(w * target_h / float(h))))
        scaled.append(cv2.resize(crop, (width, target_h), interpolation=cv2.INTER_CUBIC))
    if len(scaled) < 2:
        return None

    # The widest crop is the reference: it has the most horizontal detail, and
    # warping others onto it never has to invent columns.
    reference = max(scaled, key=lambda c: c.shape[1])
    width = reference.shape[1]
    ref_grey = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)

    out = [reference]
    for crop in scaled:
        if crop is reference:
            continue
        canvas = cv2.resize(crop, (width, target_h), interpolation=cv2.INTER_CUBIC)
        grey = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
        try:
            warp = np.eye(2, 3, dtype=np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
            cv2.findTransformECC(ref_grey, grey, warp, cv2.MOTION_TRANSLATION,
                                 criteria, None, 5)
            shift = float(abs(warp[0, 2]))
            # A huge shift means ECC latched onto noise, not the plate.
            if shift > width * 0.25:
                continue
            canvas = cv2.warpAffine(
                canvas, warp, (width, target_h),
                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                borderMode=cv2.BORDER_REPLICATE)
        except cv2.error:
            continue
        out.append(canvas)

    return out if len(out) >= 2 else None


def _vote_across_variants(candidates: "list[pr.PlateCandidate]") -> "pr.PlateCandidate | None":
    """One more candidate: what the variants AGREE on, character by character.

    Each variant is the same plate rendered differently, so where they disagree
    they disagree about a glyph, not about a plate - and the disagreement is
    usually one variant's artefact. Measured on this estate: a crop whose
    upscaled rendering read GJ11CL7005 and whose native pixels read GJ11GL7005
    is resolved correctly by a majority over the two, plus a third variant that
    also saw C. Taking only the single best-scoring variant throws that
    corroboration away, and on a tie it picks by list order, which is not
    evidence.

    Deliberately conservative:

      * only reads of the SAME length vote together, because a vote across
        different lengths is a vote across different segmentations and would
        invent a character;
      * it needs at least two agreeing readings, so this can never manufacture
        an answer from a single variant;
      * the result is scored as the mean of its contributors and then passed
        through the ordinary grammar, so it competes with the others rather
        than overriding them, and a vote that lands on nonsense still loses.
    """
    usable = [c for c in candidates if c.text and c.variant != "vote"]
    if len(usable) < 2:
        return None

    by_length: dict[int, list] = {}
    for cand in usable:
        by_length.setdefault(len(cand.text), []).append(cand)
    group = max(by_length.values(), key=lambda g: (len(g), sum(c.score for c in g)))
    if len(group) < 2:
        return None

    width = len(group[0].text)
    voted = []
    for i in range(width):
        tally: dict[str, float] = {}
        for cand in group:
            ch = cand.text[i]
            # Weighted by the reading's own score, so a confident variant
            # counts for more than a doubtful one.
            tally[ch] = tally.get(ch, 0.0) + max(cand.score, 0.01)
        voted.append(max(tally.items(), key=lambda kv: kv[1])[0])

    text = "".join(voted)
    if any(text == c.text for c in group):
        return None          # the vote agrees with a variant already present

    mean = sum(c.score for c in group) / len(group)
    return pr.normalise(text, mean, engine="ensemble", variant="vote")


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
    def fusion_engine(self):
        """The first engine that can decode a whole track at once, if any.

        Only the in-process Paddle recogniser exposes the score matrix before
        the CTC decode; tesseract and the subprocess engine return finished
        strings. So fusion is an optional capability of an engine rather than
        something the ensemble can guarantee, and the caller falls back to
        per-frame reads when nothing here offers it.
        """
        for engine in self.engines:
            if hasattr(engine, "read_track_fused"):
                return engine
        return None

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
            # A stacked plate gets read band by band as well as whole. The
            # base variant is enough to split on - every variant is the same
            # crop enhanced differently, so they share a layout, and profiling
            # each one would repeat the same measurement at the same answer.
            stacked: list[tuple[str, Reading]] = []
            if images:
                try:
                    for reading in read_stacked(engine, images[0]):
                        stacked.append(("stacked", reading))
                except Exception as exc:        # noqa: BLE001 - never fatal
                    log.debug("stacked read failed on %s: %s", engine.name, exc)

            pairs = [(variant, reading)
                     for variant, readings in zip(names, per_image)
                     for reading in readings]
            pairs.extend(stacked)

            for variant, (raw, conf) in pairs:
                trace.append({"engine": engine.name, "variant": variant,
                              "raw": raw, "conf": round(float(conf), 3)})
                if conf < self.cfg.min_confidence:
                    continue
                if not pr.plausible(raw):
                    continue
                candidates.append(pr.normalise(
                    raw, float(conf), engine=engine.name, variant=variant))

        voted = _vote_across_variants(candidates)
        if voted is not None:
            candidates.append(voted)

        candidates.sort(key=lambda c: (c.valid, c.score), reverse=True)
        return OcrResult(candidates, pr.best_of(candidates), trace)
