"""Controlled plate-detector training experiments.

Four configurations, run from the same dataset with the same seed, so the
differences between them are the thing being measured rather than run-to-run
noise:

    A_baseline_640       the deployed setup's resolution, default augmentation
    B_highres_960        A at 960px - matches roi_imgsz in config.yaml
    C_smallobj_aug       B plus augmentation aimed at small objects
    D_tiny_oversample    C plus extra exposure to tiny-plate images

Each writes weights, `args.yaml`, `results.csv` and both checkpoints under
`runs/plate/<name>/`. Nothing here overwrites the deployed weights.

What the augmentation choices are, and are not, based on
--------------------------------------------------------
The training images are ALREADY real CCTV: vehicle crops cut from H.264 streams
at 400 kbps-2 Mbps. They arrive with genuine compression blocking, motion blur
and sensor noise baked in. Synthesising more of it on top would produce
double-degraded images that no camera ever emits, so the augmentation here
spends its budget on *geometry and exposure* - scale, mosaic, copy-paste,
brightness - which vary genuinely between cameras and times of day, and leaves
the degradation to the footage.

`fliplr` is 0 throughout. A registration is directional text; a mirrored plate
teaches a glyph shape that does not exist.

Usage
-----
    python tools/train_plate_detector.py --list
    python tools/train_plate_detector.py --experiment A_baseline_640
    python tools/train_plate_detector.py --all --epochs 80
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sys
import time
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
for candidate in (str(WORKER_ROOT), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from _corpus import write_json  # noqa: E402

log = logging.getLogger("train")

SEED = 0

#: Shared by every experiment, so only the deliberate differences vary.
COMMON = dict(
    seed=SEED,
    deterministic=True,
    patience=30,
    optimizer="auto",
    amp=True,                 # mixed precision: roughly halves activation memory
    fliplr=0.0,               # see module docstring
    flipud=0.0,
    val=True,
    plots=True,
    save_period=-1,
    workers=0,                # 8 GB of RAM; forked workers risk the host OOM
                          # that killed the first attempt, and buy ~nothing on 604 crops
    project="runs/plate",
    exist_ok=True,
)

#: Augmentation aimed at small objects.
#:
#: `scale` and `mosaic` are the two that matter: both put the same plate into
#: the batch at many apparent distances, which is what teaches scale invariance
#: on a set this small. `copy_paste` multiplies the tiny-plate instances
#: without duplicating whole frames. Rotation stays small because plates on
#: these cameras are close to frontal, and capacity spent on severe rotation is
#: capacity not spent on small plates.
SMALL_OBJECT_AUG = dict(
    scale=0.9,
    mosaic=1.0,
    close_mosaic=10,          # last 10 epochs without mosaic, to settle
    copy_paste=0.3,
    degrees=7.0,
    perspective=0.0005,
    translate=0.15,
    hsv_h=0.015,
    hsv_s=0.6,
    hsv_v=0.5,                # the estate is largely night and dim
    erasing=0.0,              # occluding a 20px plate deletes it entirely
)

CONSERVATIVE_AUG = dict(
    scale=0.5, mosaic=1.0, close_mosaic=10, copy_paste=0.0,
    degrees=0.0, perspective=0.0, translate=0.1,
    hsv_h=0.015, hsv_s=0.7, hsv_v=0.4, erasing=0.0,
)

EXPERIMENTS: dict[str, dict] = {
    "A_baseline_640": dict(
        model="yolo11s.pt", imgsz=640, batch=8, **CONSERVATIVE_AUG,
        _note="Deployed resolution, stock augmentation. The control."),
    "B_highres_960": dict(
        model="yolo11s.pt", imgsz=960, batch=4, **CONSERVATIVE_AUG,
        _note="Only the resolution changes, so any difference is attributable."),
    "C_smallobj_aug": dict(
        model="yolo11s.pt", imgsz=960, batch=4, **SMALL_OBJECT_AUG,
        _note="B plus scale/mosaic/copy-paste aimed at small objects."),
    "D_tiny_oversample": dict(
        model="yolo11s.pt", imgsz=960, batch=4, _oversample=True,
        **SMALL_OBJECT_AUG,
        _note="C plus 3x exposure to images containing a tiny plate."),
}


# ---------------------------------------------------------------------------
# Tiny-plate oversampling
# ---------------------------------------------------------------------------

#: Bands treated as "tiny" for oversampling purposes.
TINY_BANDS = ("EXTREMELY_TINY", "VERY_SMALL")


def build_oversampled_list(dataset: Path, factor: int = 3) -> Path:
    """Write a train list that repeats tiny-plate images *factor* times.

    Ultralytics has no weighted sampler, so exposure is changed by listing an
    image more than once. That is not the same as duplicating data: each
    appearance is independently augmented - different mosaic partners, scale,
    crop and exposure - so the model sees genuinely different pictures of the
    same plate rather than the identical tensor N times.

    The factor is deliberately small. Past about 3x the tiny images start to
    dominate the batch statistics and the larger bands regress, which is the
    failure this whole exercise is trying not to cause in reverse.
    """
    rows = list(csv.DictReader(open(dataset / "metadata.csv", encoding="utf-8")))
    tiny_images = {r["image_file"] for r in rows
                   if r["split"] == "train" and r["plate_size_category"] in TINY_BANDS}

    train_dir = dataset / "images" / "train"
    listing: list[str] = []
    repeated = 0
    for image in sorted(train_dir.glob("*.jpg")):
        rel = f"images/train/{image.name}"
        listing.append(str(image.resolve()))
        if rel in tiny_images:
            listing.extend([str(image.resolve())] * (factor - 1))
            repeated += 1

    out = dataset / f"train_oversampled_x{factor}.txt"
    out.write_text("\n".join(listing) + "\n", encoding="utf-8")
    log.info("oversampled train list: %d entries (%d images repeated %dx) -> %s",
             len(listing), repeated, factor, out.name)
    return out


def data_yaml_for(dataset: Path, oversample: bool, factor: int = 3) -> Path:
    """The data.yaml an experiment should train against."""
    if not oversample:
        return dataset / "data.yaml"
    listing = build_oversampled_list(dataset, factor)
    path = dataset / f"data_oversampled_x{factor}.yaml"
    path.write_text(
        f"# Generated by tools/train_plate_detector.py - do not hand-edit.\n"
        f"# Tiny-plate images repeated {factor}x in the train list; val and\n"
        f"# test are UNTOUCHED, so metrics stay comparable across experiments.\n"
        f"path: {dataset.resolve().as_posix()}\n"
        f"train: {listing.name}\n"
        f"val: images/val\n"
        f"test: images/test\n"
        f"nc: 1\n"
        f"names: ['plate']\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_experiment(name: str, dataset: Path, epochs: int, device: str,
                   batch_override: int | None) -> dict:
    from ultralytics import YOLO
    import torch

    spec = dict(EXPERIMENTS[name])
    note = spec.pop("_note", "")
    oversample = spec.pop("_oversample", False)
    model_name = spec.pop("model")
    if batch_override:
        spec["batch"] = batch_override

    data = data_yaml_for(dataset, oversample)
    args = {**COMMON, **spec, "data": str(data), "epochs": epochs,
            "name": name, "device": device}

    log.info("=" * 70)
    log.info("EXPERIMENT %s", name)
    log.info("  %s", note)
    log.info("  model=%s imgsz=%d batch=%d epochs=%d device=%s",
             model_name, args["imgsz"], args["batch"], epochs, device)
    log.info("=" * 70)

    if device != "cpu" and torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    started = time.time()
    model = YOLO(model_name)
    results = model.train(**args)
    elapsed = time.time() - started

    peak_vram_gb = None
    if device != "cpu" and torch.cuda.is_available():
        peak_vram_gb = round(torch.cuda.max_memory_allocated() / 1024 ** 3, 2)

    run_dir = Path(results.save_dir)
    metrics = {}
    try:
        box = results.box if hasattr(results, "box") else None
        if box is not None:
            metrics = {"mAP50": round(float(box.map50), 4),
                       "mAP50_95": round(float(box.map), 4),
                       "precision": round(float(box.mp), 4),
                       "recall": round(float(box.mr), 4)}
    except Exception as exc:                       # noqa: BLE001
        log.warning("could not read metrics off the result: %s", exc)

    record = {
        "experiment": name,
        "note": note,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(elapsed, 1),
        "elapsed_h": round(elapsed / 3600, 2),
        "model": model_name,
        "dataset": str(dataset),
        "data_yaml": str(data),
        "oversampled": oversample,
        "epochs_requested": epochs,
        "device": device,
        "peak_vram_gb": peak_vram_gb,
        "args": {k: v for k, v in args.items() if not k.startswith("_")},
        "val_metrics": metrics,
        "run_dir": str(run_dir),
        "best_checkpoint": str(run_dir / "weights" / "best.pt"),
        "last_checkpoint": str(run_dir / "weights" / "last.pt"),
    }
    write_json(run_dir / "experiment.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", action="append", default=None,
                        help="Experiment name; repeatable.")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dataset", default="dataset/v2")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--device", default="0",
                        help="'0' for the first GPU, 'cpu' to force CPU.")
    parser.add_argument("--batch", type=int, default=None,
                        help="Override the experiment's batch size (VRAM).")
    parser.add_argument("--summary", default="reports/training_experiments.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list:
        for name, spec in EXPERIMENTS.items():
            print(f"{name:<24} model={spec['model']:<12} imgsz={spec['imgsz']:<5} "
                  f"batch={spec['batch']:<3} {spec.get('_note','')}")
        return 0

    names = list(EXPERIMENTS) if args.all else (args.experiment or [])
    if not names:
        raise SystemExit("Pass --experiment NAME (repeatable), --all, or --list.")
    unknown = [n for n in names if n not in EXPERIMENTS]
    if unknown:
        raise SystemExit(f"Unknown experiment(s): {unknown}")

    dataset = Path(args.dataset)
    if not (dataset / "data.yaml").exists():
        raise SystemExit(f"No data.yaml under {dataset}. Run prepare_dataset.py.")

    summary_path = Path(args.summary)
    records = []
    if summary_path.exists():
        try:
            records = json.load(open(summary_path, encoding="utf-8")).get("runs", [])
        except Exception:                          # noqa: BLE001
            records = []

    for name in names:
        try:
            records.append(run_experiment(name, dataset, args.epochs,
                                          args.device, args.batch))
        except Exception as exc:                   # noqa: BLE001
            log.error("experiment %s failed: %s", name, exc)
            records.append({"experiment": name, "error": str(exc)})
        write_json(summary_path, {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "seed": SEED, "runs": records})

    print("\n" + "=" * 72)
    print("EXPERIMENTS COMPLETE")
    print("=" * 72)
    for record in records:
        if "error" in record:
            print(f"  {record['experiment']:<24} FAILED: {record['error'][:60]}")
            continue
        m = record.get("val_metrics") or {}
        print(f"  {record['experiment']:<24} mAP50={m.get('mAP50','?'):<8} "
              f"R={m.get('recall','?'):<8} {record['elapsed_h']}h "
              f"vram={record.get('peak_vram_gb')}GB")
    print(f"\nWritten: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
