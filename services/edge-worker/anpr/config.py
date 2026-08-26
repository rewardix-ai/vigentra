"""Configuration loading.

Every tunable in the pipeline lives here so the UI can expose it and so a
site can be re-tuned without touching code.  ``config.yaml`` next to the
project root overrides these defaults; anything absent falls back.

VENDORED. This package is the ANPR engine, imported into the edge worker
rather than reimplemented. Keep local edits to a minimum and note each one, so
the upstream copy can still be diffed against it. Local edits so far:

  * ``ANPR_MODELS_DIR`` / ``ANPR_DATA_DIR`` / ``ANPR_CONFIG`` environment
    overrides, because in a container the weights are mounted rather than
    living beside the source.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
#: Weights are mounted in a container, so the location has to be settable
#: without editing a vendored file.
MODELS_DIR = Path(os.getenv("ANPR_MODELS_DIR") or (ROOT / "models"))
DATA_DIR = Path(os.getenv("ANPR_DATA_DIR") or (ROOT / "data"))


@dataclass
class DetectConfig:
    #: Vehicle detector - gives us stable track IDs and a search ROI.
    vehicle_model: str = "yolov8n.pt"
    #: Plate detector.  Drop your own fine-tuned weights in models/ and point
    #: this at them; that is the single biggest accuracy lever available.
    plate_model: str = "plate_detector.pt"
    #: Frames wider than this are downscaled before the detection passes.
    #: The detectors resize to `imgsz` internally anyway, so running them on a
    #: 4K frame only pays for a bigger CPU-side resize - twice per frame.
    #: Plate crops are still taken from the ORIGINAL full-resolution frame, so
    #: no plate detail is lost.  0 disables downscaling.
    process_width: int = 1920
    #: Max vehicle crops per ROI inference call.  A dense 4K junction can hold
    #: 20+ vehicles, and one batch that size will exhaust a 4 GB card.
    roi_batch: int = 8
    vehicle_conf: float = 0.35
    plate_conf: float = 0.25
    #: Inference size for the full-frame passes.
    vehicle_imgsz: int = 640
    plate_imgsz: int = 640
    #: Inference size for the ROI pass.  Dropping this to 320 is ~25% faster
    #: but measurably loses small/distant plates, so it stays at full size by
    #: default; buy speed with scheduler.frame_stride instead, which costs
    #: far less accuracy.
    roi_imgsz: int = 640
    #: Re-run plate detection inside each vehicle box, upscaled.  Costs time
    #: but is what makes small/distant plates readable.
    roi_pass: bool = True
    #: Vehicle crops smaller than this (px, longest side) are upscaled before
    #: the ROI plate pass.
    roi_min_size: int = 320
    #: COCO classes treated as vehicles: car, motorcycle, bus, truck.
    vehicle_classes: tuple[int, ...] = (2, 3, 5, 7)
    tracker: str = "bytetrack.yaml"
    device: str = "auto"          # "auto" | "cpu" | "0"
    half: bool = True             # fp16 on GPU - roughly 1.6x throughput


@dataclass
class EnhanceConfig:
    #: Target height in px for the OCR input; plates are upscaled to this.
    ocr_height: int = 64
    #: Maximum enhancement variants generated per plate crop.  Each variant
    #: costs one OCR call, so this trades latency for accuracy directly.
    max_variants: int = 4
    #: Perspective-rectify the plate before OCR (large gain on angled views).
    rectify: bool = True
    #: Mean luma below this triggers the low-light branch.
    dark_threshold: float = 85.0
    #: Fraction of blown-out pixels above which the glare branch runs.
    glare_threshold: float = 0.035
    #: Variance-of-Laplacian below this marks the crop as blurred.
    blur_threshold: float = 90.0
    #: Plates narrower than this get super-resolved.
    sr_width_threshold: int = 140
    #: Super-resolution backend: "auto" uses a real SR net when its weights
    #: are present, otherwise high-quality Lanczos + unsharp.
    sr_backend: str = "auto"      # "auto" | "espcn" | "lanczos" | "off"
    sr_scale: int = 4
    clahe_clip: float = 2.5
    clahe_grid: int = 8


@dataclass
class OcrConfig:
    #: Engines to consult, in priority order.  Missing engines are skipped
    #: with a warning rather than crashing the pipeline.
    engines: tuple[str, ...] = ("paddle",)
    paddle_lang: str = "en"
    #: PaddleOCR on CPU keeps the 4 GB of VRAM free for detection + SR.
    paddle_device: str = "cpu"
    #: Recognition model. The mobile v5 recogniser reads a plate in ~33 ms on
    #: CPU against ~724 ms for PP-OCRv6_medium, at the same confidence on
    #: plate-sized crops - a 20x speedup that costs nothing measurable here.
    paddle_rec_model: str = "PP-OCRv5_mobile_rec"
    #: Plate crops are already tight, so recognition alone is the fast path.
    #: Detection is only worth its cost when that path returns something
    #: implausible - which is what a stacked two-row plate looks like.
    paddle_fallback_det: bool = True
    #: Models for that fallback pipeline.  The mobile pair reads a two-row
    #: plate in ~450 ms against ~1830 ms for the v6_medium default, splitting
    #: the rows just as well.  Empty string keeps PaddleOCR's own defaults.
    paddle_fallback_det_model: str = "PP-OCRv5_mobile_det"
    paddle_fallback_rec_model: str = "PP-OCRv5_mobile_rec"
    #: Hard cap on fallback invocations per frame.  Two-row plates are a
    #: minority, but "unreadable" crops are not - without a cap the fallback
    #: fires on most calls and dominates the frame budget.
    max_fallback_per_frame: int = 2
    #: Crops narrower than this are not worth escalating: if the recogniser
    #: could not read them, a text detector will not rescue them either.
    fallback_min_width: int = 60
    #: Interpreter for the out-of-process GPU OCR worker.  CUDA torch and CUDA
    #: paddle cannot share a process (incompatible cuDNN builds, in either
    #: import order), so recognition on the GPU has to live in its own venv.
    #: Only used when "paddle-gpu" appears in `engines`.
    worker_python: str = ".venv-gputest/Scripts/python.exe"
    worker_device: str = "gpu"
    #: Below this, an individual reading is discarded outright.
    min_confidence: float = 0.30
    #: Batch size for the recogniser when several crops are pending.
    batch_size: int = 16


@dataclass
class ConsensusConfig:
    #: Observations required before a track's plate may be confirmed.
    min_observations: int = 3
    #: Winning text must beat the runner-up by this margin to confirm.
    min_margin: float = 0.20
    #: Aggregate score required to confirm.
    min_score: float = 0.62
    #: A grammar-valid reading is worth this multiplier when voting.
    valid_weight: float = 1.6
    #: How many readings to retain per track.
    history: int = 24
    #: Stop spending OCR on a track once it is confirmed this strongly.
    lock_score: float = 0.90


@dataclass
class SchedulerConfig:
    """Controls how OCR effort is spread across many simultaneous vehicles."""
    #: Hard cap on OCR jobs dispatched per frame.  This is what keeps a
    #: crowded frame real-time instead of collapsing.
    max_ocr_per_frame: int = 8
    #: Minimum frames between OCR attempts on the same unconfirmed track.
    track_cooldown: int = 2
    #: Skip OCR on a crop whose quality is worse than the best already seen
    #: for that track by more than this margin.
    quality_regression: float = 0.12
    #: Process only every Nth frame of the source.
    #:
    #: This is the cheapest speed control available and the one to reach for
    #: first.  Consensus needs a handful of independent looks at a vehicle,
    #: not thirty: at 30 fps a car is in shot for 2-4 seconds, so a stride of
    #: 3 still yields ~20-40 candidate frames per vehicle while cutting the
    #: work to a third.  Raise it for 4K or high-fps sources.
    frame_stride: int = 1


@dataclass
class RegionConfig:
    #: Soft prior - readings landing on these states win ties.
    preferred_states: tuple[str, ...] = ("GJ",)


@dataclass
class Config:
    detect: DetectConfig = field(default_factory=DetectConfig)
    enhance: EnhanceConfig = field(default_factory=EnhanceConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)
    consensus: ConsensusConfig = field(default_factory=ConsensusConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    region: RegionConfig = field(default_factory=RegionConfig)
    #: Save a crop of every confirmed plate for auditing.
    save_crops: bool = True
    #: Annotated MJPEG preview width; smaller = less bandwidth to the UI.
    preview_width: int = 960
    jpeg_quality: int = 75

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _merge(dc: Any, data: dict[str, Any]) -> None:
    """Recursively overlay *data* onto dataclass instance *dc* in place."""
    for f in fields(dc):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(dc, f.name)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        elif isinstance(current, tuple) and isinstance(value, list):
            setattr(dc, f.name, tuple(value))
        else:
            setattr(dc, f.name, value)


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration, applying config.yaml when it exists."""
    cfg = Config()
    p = Path(path or os.getenv("ANPR_CONFIG") or (ROOT / "config.yaml"))
    if p.exists():
        import yaml
        with open(p, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _merge(cfg, data)

    # Push the regional prior into the grammar engine.
    from . import plate_rules
    plate_rules.set_preferred_states(cfg.region.preferred_states)
    return cfg


def resolve_model(name: str) -> str:
    """Resolve a model name to a path under models/ when it exists there.

    Falls back to the bare name so ultralytics can auto-download its own
    pretrained weights (yolov8n.pt and friends).
    """
    local = MODELS_DIR / name
    if local.exists():
        return str(local)
    return name
