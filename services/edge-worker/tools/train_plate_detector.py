"""Controlled plate-detector training experiments.

Four configurations, run from the same dataset with the same seed, so the
differences between them are the thing being measured rather than run-to-run
noise:

    A_baseline_640       the deployed setup's resolution, default augmentation
    B_highres_960        A at 960px - matches roi_imgsz in config.yaml
    C_smallobj_aug       B plus augmentation aimed at small objects
    D_tiny_oversample    C plus extra exposure to tiny-plate images

Each writes weights, `args.yaml`, `results.csv` and both checkpoints under
`services/edge-worker/runs/plate/<name>/` (a rerun gets `<name>2`, never an
overwrite). Nothing here touches the deployed weights.

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
import sys
import time
import warnings
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
    # Early stopping is OFF. ultralytics closes mosaic only at
    # epochs - close_mosaic and breaks out first if patience fires, so with it
    # on, whether a run ever trained mosaic-free depended on when it peaked -
    # an undocumented variable in what is meant to be a controlled comparison.
    # Every experiment runs the full schedule; best.pt is still the best epoch.
    patience=0,
    optimizer="auto",
    amp=True,                 # mixed precision: roughly halves activation memory
    fliplr=0.0,               # see module docstring
    flipud=0.0,
    val=True,
    plots=True,
    save_period=-1,
    workers=0,                # 8 GB of RAM; forked workers risk the host OOM
                              # that killed the first attempt, and buy ~nothing on 604 crops
    # Absolute, because ultralytics nests a RELATIVE project under
    # runs/<task>/ and every documented path then pointed at nothing.
    project=str(WORKER_ROOT / "runs" / "plate"),
    # A rerun gets its own directory (name2, name3...). With exist_ok the
    # rerun overwrote best.pt under the feet of every report that cited it,
    # and appended a second headerless block to results.csv.
    exist_ok=False,
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
    degrees=7.0,
    perspective=0.0005,
    translate=0.15,
    hsv_h=0.015,
    hsv_s=0.6,
    hsv_v=0.5,                # the estate is largely night and dim
)
# Deliberately absent: copy_paste (a no-op on box-only labels - ultralytics
# returns early when there are no segments, so an earlier version claimed an
# effect it could not have had) and erasing (classification-only; ignored by
# the detect trainer).

CONSERVATIVE_AUG = dict(
    scale=0.5, mosaic=1.0, close_mosaic=10,
    degrees=0.0, perspective=0.0, translate=0.1,
    hsv_h=0.015, hsv_s=0.7, hsv_v=0.4,
)

EXPERIMENTS: dict[str, dict] = {
    "A_baseline_640": dict(
        model="yolo11s.pt", imgsz=640, batch=8, **CONSERVATIVE_AUG,
        _note="Low-resolution control with stock augmentation."),
    "B_highres_960": dict(
        model="yolo11s.pt", imgsz=960, batch=4, **CONSERVATIVE_AUG,
        _note="Deployed ROI resolution (roi_imgsz=960); only imgsz differs from A."),
    "C_smallobj_aug": dict(
        model="yolo11s.pt", imgsz=960, batch=4, **SMALL_OBJECT_AUG,
        _note="B plus scale jitter / mosaic / small rotation aimed at small objects."),
    "D_tiny_oversample": dict(
        model="yolo11s.pt", imgsz=960, batch=4, _oversample=3,
        **SMALL_OBJECT_AUG,
        _note="C plus 3x exposure to tiny-plate images, at an EQUAL step budget."),
}


# ---------------------------------------------------------------------------
# Curriculum over synthetic hard cases
# ---------------------------------------------------------------------------

#: Stages written by tools/synthesize_hard_cases.py, easiest first. Each stage
#: fine-tunes from the previous stage's best.pt, so the detector meets the
#: smears only after it has learned what a plate looks like - the order a
#: person would teach it in, and the one that does not collapse recall on the
#: plates it could already find.
CURRICULUM_STAGES = (
    "stage1_real_mild",
    "stage2_plus_moderate",
    "stage3_plus_severe",
    "stage4_all",
)


def run_curriculum(name: str, dataset: Path, epochs_per_stage: int, device: str,
                   batch_override: int | None, base_model: str = "yolo11s.pt",
                   stages: tuple[str, ...] = CURRICULUM_STAGES) -> dict:
    """Train stage by stage, each from the previous stage's best weights.

    Same augmentation, imgsz and batch as C_smallobj_aug so the curriculum
    is the only thing that differs from it. Reports the final stage's metrics
    and the best checkpoint of every stage, so a regression at a later stage
    can be caught rather than averaged away.
    """
    from ultralytics import YOLO
    import torch

    spec = dict(EXPERIMENTS["C_smallobj_aug"])
    spec.pop("_note", None); spec.pop("_oversample", None); spec.pop("model", None)
    if batch_override:
        spec["batch"] = batch_override

    weights = base_model
    stages_out = []
    started = time.time()
    for stage in stages:
        data = dataset / f"{stage}.yaml"
        if not data.exists():
            raise SystemExit(f"missing {data} - run tools/synthesize_hard_cases.py first")
        args = {**COMMON, **spec, "data": str(data), "epochs": epochs_per_stage,
                "name": f"{name}_{stage}", "device": device}
        log.info("=" * 70)
        log.info("CURRICULUM %s  stage %s  from %s", name, stage, weights)
        log.info("=" * 70)
        if device != "cpu" and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        results = YOLO(weights).train(**args)
        run_dir = Path(results.save_dir)
        weights = str(run_dir / "weights" / "best.pt")
        box = getattr(results, "box", None)
        stages_out.append({
            "stage": stage, "run_dir": str(run_dir), "best_checkpoint": weights,
            "val_metrics": ({"mAP50": round(float(box.map50), 4),
                             "mAP50_95": round(float(box.map), 4),
                             "precision": round(float(box.mp), 4),
                             "recall": round(float(box.mr), 4)} if box is not None else {}),
        })
        write_json(run_dir / "curriculum_stage.json", stages_out[-1])

    record = {
        "experiment": name,
        "note": "C_smallobj_aug settings, trained stage-by-stage over real + synthetic hard cases",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_h": round((time.time() - started) / 3600, 2),
        "model": base_model, "dataset": str(dataset), "device": device,
        "epochs_per_stage": epochs_per_stage,
        "stages": stages_out,
        "val_metrics": stages_out[-1]["val_metrics"] if stages_out else {},
        "best_checkpoint": weights,
        "run_dir": stages_out[-1]["run_dir"] if stages_out else "",
    }
    return record


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
    with open(dataset / "metadata.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
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


def data_yaml_for(dataset: Path, factor: int) -> Path:
    """The data.yaml an experiment should train against.

    *factor* 0 or 1 means the plain dataset; otherwise a train list with
    tiny-plate images repeated *factor* times.
    """
    if factor <= 1:
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
    factor = int(spec.pop("_oversample", 0) or 0)
    model_name = spec.pop("model")
    if batch_override:
        spec["batch"] = batch_override

    data = data_yaml_for(dataset, factor)
    # Equal STEP budget, not equal epochs. Repeating tiny-plate images makes
    # the epoch ~46% longer, so at equal epochs the oversampled run would also
    # simply have trained longer - and any gain could be either. Scaling the
    # epochs down by the list's growth keeps optimizer steps matched to the
    # other experiments, so the only remaining difference is exposure.
    if factor > 1:
        listing = dataset / f"train_oversampled_x{factor}.txt"
        entries = sum(1 for line in listing.read_text(encoding="utf-8").splitlines()
                      if line.strip())
        plain = len(list((dataset / "images" / "train").glob("*.jpg")))
        scaled = max(1, round(epochs * plain / max(1, entries)))
        log.info("oversample x%d: %d list entries vs %d images -> %d epochs "
                 "for an equal step budget", factor, entries, plain, scaled)
        epochs = scaled
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
        "oversample_factor": factor,
        "epochs_requested": epochs,
        "device": device,
        # max_memory_allocated understates what the card actually holds (the
    # caching allocator's reserved blocks and the CUDA context are on top),
    # so this is a lower bound on the real footprint, and is named as one.
    "peak_vram_allocated_gb": peak_vram_gb,
        "args": dict(args),
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
    parser.add_argument("--device", default=None,
                        help="None lets ultralytics pick; '0' first GPU; 'cpu'.")
    parser.add_argument("--batch", type=int, default=None,
                        help="Override the experiment's batch size (VRAM).")
    parser.add_argument("--summary", default="reports/training_experiments.json")
    parser.add_argument("--stages", default=None,
                        help="Comma-separated curriculum stages to run (default: all "
                             "four). e.g. --stages stage4_all for hard-first.")
    parser.add_argument("--base-model", default="yolo11s.pt",
                        help="Weights to start the first stage from - a trained "
                             "best.pt to fine-tune, or a pretrained yolo11*.pt.")
    parser.add_argument("--name", default="E_curriculum_synth")
    parser.add_argument("--curriculum", default=None, metavar="SYNTH_DATASET",
                        help="Run the curriculum experiment over this synthetic "
                             "dataset (from synthesize_hard_cases.py) instead of "
                             "the named experiments. --epochs is per stage.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.list:
        for name, spec in EXPERIMENTS.items():
            print(f"{name:<24} model={spec['model']:<12} imgsz={spec['imgsz']:<5} "
                  f"batch={spec['batch']:<3} {spec.get('_note','')}")
        return 0

    if args.curriculum:
        summary_path = Path(args.summary)
        records = []
        if summary_path.exists():
            try:
                with open(summary_path, encoding="utf-8") as fh:
                    records = json.load(fh).get("runs", [])
            except Exception:                      # noqa: BLE001
                records = []
        stages = tuple(x.strip() for x in args.stages.split(",")) if args.stages             else CURRICULUM_STAGES
        record = run_curriculum(args.name, Path(args.curriculum), args.epochs,
                                args.device, args.batch, base_model=args.base_model,
                                stages=stages)
        records = [r for r in records if r.get("experiment") != record["experiment"]]
        records.append(record)
        write_json(summary_path, {"updated_at": datetime.now(timezone.utc).isoformat(),
                                  "seed": SEED, "runs": records})
        print("CURRICULUM COMPLETE")
        for st in record["stages"]:
            m = st["val_metrics"]
            print(f"  {st['stage']:<24} mAP50={m.get('mAP50','?')} R={m.get('recall','?')}")
        print(f"best: {record['best_checkpoint']}")
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
    records: list[dict] = []
    if summary_path.exists():
        try:
            with open(summary_path, encoding="utf-8") as fh:
                records = json.load(fh).get("runs", [])
        except Exception:                          # noqa: BLE001
            records = []

    def remember(record: dict) -> None:
        # One record per experiment name: a rerun replaces its predecessor
        # rather than leaving a FAILED entry beside a later success.
        records[:] = [r for r in records if r.get("experiment") != record["experiment"]]
        records.append(record)

    for name in names:
        try:
            remember(run_experiment(name, dataset, args.epochs,
                                    args.device, args.batch))
        except Exception as exc:                   # noqa: BLE001
            log.error("experiment %s failed: %s", name, exc)
            remember({"experiment": name, "error": str(exc)})
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
              f"vram>={record.get('peak_vram_allocated_gb')}GB")
    print(f"\nWritten: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
