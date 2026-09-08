"""Train and evaluate the plate reader on the uniform dataset, worst cases first.

Stage 1 sees only the severe and extreme tiers; stage 2 sees every tier.
The checkpoint kept is the one with the best exact-match rate on the HARD
synthetic validation crops (severe+extreme, composited on real val vehicles)
- not the overall rate, which the easy tiers would dominate.

Evaluation reports, for the synthetic val set and for the real hand-labelled
crops in dataset/real_plates:
  * exact-match rate and character accuracy per tier and per plate-width band
  * the grammar-repaired exact-match rate (what the pipeline would emit)
  * the same numbers for the PaddleOCR engine the pipeline uses today, so the
    comparison is on identical crops

    python tools/train_plate_reader.py --dataset dataset/v4_uniform --name R1_hard_first
    python tools/train_plate_reader.py --eval runs/reader/R1_hard_first/best.pt --compare-paddle
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(HERE))

from anpr import plate_rules as pr                                    # noqa: E402
from anpr.reader import (CHARSET, BLANK, INPUT_H, INPUT_W, ReaderSpec,  # noqa: E402
                         build_model, preprocess, greedy_decode)
from _corpus import write_json                                        # noqa: E402

log = logging.getLogger("train_plate_reader")
SEED = 0
HARD_TIERS = ("severe", "extreme")
#: Checkpoint selection band, px: the worst plates that can still carry
#: characters. Below 24 px the exact-match rate is noise whatever the
#: model; above 60 px every model reads. Selecting here picks the
#: checkpoint that wins the fight that matters.
SELECT_BAND = (24.0, 60.0)
WIDTH_BANDS = ((0, 24, "<24"), (24, 32, "24-32"), (32, 40, "32-40"), (40, 60, "40-60"),
               (60, 100, "60-100"), (100, 10_000, ">=100"))


def band_of(px: float) -> str:
    for lo, hi, name in WIDTH_BANDS:
        if lo <= px < hi:
            return name
    return ">=100"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_rows(dataset: Path, splits: tuple[str, ...]) -> list[dict]:
    with open(dataset / "plates.csv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] in splits]
    for r in rows:
        r["plate_px"] = float(r["plate_px"])
        r["readable"] = int(r["readable"])
    return rows


#: Crops at or above this width skip the upscaler: they are already past the
#: recogniser's input height and SR would only be resampled straight back.
SR_MAX_INPUT_W = 160


def enhance_rows(rows: list[dict], root: Path, sr_weights: Path, cache: Path) -> None:
    """Run the plate upscaler over every crop once; point the rows at the cache.

    Mirrors what enhance.py does in the pipeline: small crops are upscaled
    x4 before any variant is made. Cached on disk keyed by the weights file,
    so a second run (or the evaluation) does not pay for it again.
    """
    from anpr.sr import PlateUpscaler
    up = PlateUpscaler(str(sr_weights), "cuda" if _cuda() else "cpu")
    cache.mkdir(parents=True, exist_ok=True)
    done = 0
    t0 = time.time()
    for r in rows:
        src = root / r["plate_image"]
        dst = cache / (r["plate_image"].replace("/", "__"))
        if not dst.exists():
            img = cv2.imread(str(src))
            if img is None:
                continue
            if img.shape[1] < SR_MAX_INPUT_W:
                img = up.upscale(img)
            cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            done += 1
        r["plate_image_raw"] = r["plate_image"]
        r["plate_image"] = str(dst.relative_to(root)) if dst.is_relative_to(root) else str(dst)
    log.info("enhanced %d crops into %s (%.0fs)", done, cache, time.time() - t0)


def _cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:                                    # noqa: BLE001
        return False


def encode(text: str) -> list[int]:
    return [CHARSET.index(c) + 1 for c in text if c in CHARSET]


def augment(gray: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Light, label-preserving jitter on top of the dataset's own degradation."""
    h, w = gray.shape[:2]
    if rng.random() < 0.5:                       # framing jitter
        dx, dy = int(w * rng.uniform(-0.06, 0.06)), int(h * rng.uniform(-0.10, 0.10))
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        gray = cv2.warpAffine(gray, M, (w, h), borderMode=cv2.BORDER_REPLICATE)
    if rng.random() < 0.5:                       # exposure
        gray = cv2.convertScaleAbs(gray, alpha=rng.uniform(0.7, 1.3), beta=rng.uniform(-30, 30))
    if rng.random() < 0.15:                      # polarity (dark plates)
        gray = 255 - gray
    if rng.random() < 0.3:
        gray = np.clip(gray.astype(np.int16) + rng.normal(0, rng.uniform(2, 10), gray.shape),
                       0, 255).astype(np.uint8)
    return gray


def make_dataset_class():
    import torch
    from torch.utils.data import Dataset

    class PlateDataset(Dataset):
        def __init__(self, root: Path, rows: list[dict], train: bool, seed: int) -> None:
            self.root, self.rows, self.train = root, rows, train
            self.rng = np.random.default_rng(seed)

        def __len__(self) -> int:
            return len(self.rows)

        def __getitem__(self, i: int):
            r = self.rows[i]
            img = cv2.imread(str(_resolve(self.root, r["plate_image"])), cv2.IMREAD_GRAYSCALE)
            if img is None:
                img = np.full((INPUT_H, INPUT_W), 128, np.uint8)
            if self.train:
                img = augment(img, self.rng)
            x = torch.from_numpy(preprocess(img))
            y = torch.tensor(encode(r["text"]), dtype=torch.long)
            return x, y, i

    return PlateDataset


def _resolve(root: Path, rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else root / p


def collate(batch):
    import torch
    xs, ys, idx = zip(*batch)
    x = torch.stack(xs)
    lengths = torch.tensor([len(y) for y in ys], dtype=torch.long)
    y = torch.cat(ys) if sum(lengths) > 0 else torch.zeros(0, dtype=torch.long)
    return x, y, lengths, list(idx)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def char_accuracy(pred: str, truth: str) -> float:
    """1 - normalised Levenshtein distance."""
    if not truth and not pred:
        return 1.0
    if not truth or not pred:
        return 0.0
    m, n = len(pred), len(truth)
    d = list(range(n + 1))
    for i in range(1, m + 1):
        prev, d[0] = d[0], i
        for j in range(1, n + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (pred[i - 1] != truth[j - 1]))
            prev = cur
    return 1.0 - d[n] / max(m, n)


def matches(pred: str, truth: str) -> bool:
    return pred in truth.split("|") if truth else pred == ""


def repaired(pred: str) -> str:
    """What the grammar would turn the raw read into."""
    if not pred:
        return ""
    cand = pr.normalise(pred, 1.0)
    return cand.text if cand and cand.valid else pred


def summarise(preds: list[tuple[str, str, float, dict]]) -> dict:
    """preds: (pred, truth, conf, row) -> nested exact/char metrics."""
    def block(items):
        if not items:
            return {"n": 0, "exact": None, "exact_repaired": None, "char_acc": None}
        return {
            "n": len(items),
            "exact": round(float(np.mean([matches(p, t) for p, t, _, _ in items])), 4),
            "exact_repaired": round(float(np.mean([matches(repaired(p), t) for p, t, _, _ in items])), 4),
            "char_acc": round(float(np.mean([char_accuracy(p, t) for p, t, _, _ in items])), 4),
            "mean_conf": round(float(np.mean([c for _, _, c, _ in items])), 3),
        }
    labelled = [p for p in preds if p[1]]
    empties = [p for p in preds if not p[1]]
    out = {
        "overall": block(labelled),
        "hard_tiers": block([p for p in labelled if p[3].get("tier") in HARD_TIERS]),
        "select_band": block([p for p in labelled
                              if SELECT_BAND[0] <= float(p[3]["plate_px"]) < SELECT_BAND[1]]),
        "by_tier": {t: block([p for p in labelled if p[3].get("tier") == t])
                    for t in ("extreme", "severe", "moderate", "mild")},
        "by_width": {b[2]: block([p for p in labelled if band_of(p[3]["plate_px"]) == b[2]])
                     for b in WIDTH_BANDS},
        "by_source": {s: block([p for p in labelled if p[3].get("source") == s])
                      for s in ("composite", "plate_only")},
        "empty_labels": {
            "n": len(empties),
            "silence_rate": round(float(np.mean([p[0] == "" for p in empties])), 4) if empties else None,
            "mean_conf_when_wrong": round(float(np.mean([p[2] for p in empties if p[0]])), 3)
            if any(p[0] for p in empties) else None,
        },
    }
    return out


# ---------------------------------------------------------------------------
# Train
# ---------------------------------------------------------------------------

def predict_rows(model, root: Path, rows: list[dict], device, batch: int = 256) -> list[tuple[str, str, float, dict]]:
    import torch
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(rows), batch):
            part = rows[start:start + batch]
            xs = []
            for r in part:
                img = cv2.imread(str(_resolve(root, r["plate_image"])), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    img = np.full((INPUT_H, INPUT_W), 128, np.uint8)
                xs.append(preprocess(img))
            x = torch.from_numpy(np.stack(xs)).to(device)
            lp = model(x).float().cpu().numpy()
            for r, m in zip(part, lp):
                text, conf = greedy_decode(m)
                out.append((text, r["text"], conf, r))
    return out


def train(dataset: Path, name: str, epochs_hard: int, epochs_all: int, batch: int,
          lr: float, device_arg: str | None, workers: int, limit: int | None,
          sr_weights: Path | None = None, init: Path | None = None) -> dict:
    import torch
    from torch.utils.data import DataLoader

    torch.manual_seed(SEED)
    device = torch.device(device_arg or ("cuda" if torch.cuda.is_available() else "cpu"))
    run_dir = WORKER_ROOT / "runs" / "reader" / name
    if run_dir.exists():
        raise SystemExit(f"{run_dir} exists - pick another --name")
    run_dir.mkdir(parents=True)

    train_rows = load_rows(dataset, ("train", "train_boost"))
    val_rows = load_rows(dataset, ("val_synth", "val_boost"))
    if limit:
        train_rows = train_rows[:limit]
        val_rows = val_rows[: max(200, limit // 10)]
    hard_rows = [r for r in train_rows if r["tier"] in HARD_TIERS]
    log.info("train %d (hard %d)  val %d  device %s", len(train_rows), len(hard_rows), len(val_rows), device)
    if sr_weights is not None:
        cache = dataset / "plates_sr" / sr_weights.parent.name
        enhance_rows(train_rows + val_rows, dataset, sr_weights, cache)

    PlateDataset = make_dataset_class()
    model = build_model(ReaderSpec()).to(device)
    if init is not None:
        ck = torch.load(init, map_location="cpu", weights_only=False)
        model.load_state_dict(ck["model"])
        log.info("initialised from %s (epoch %s)", init, ck.get("epoch"))
    n_params = sum(p.numel() for p in model.parameters())
    log.info("reader params: %.2fM", n_params / 1e6)
    ctc = torch.nn.CTCLoss(blank=BLANK, zero_infinity=True)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")

    stages = [("hard", hard_rows, epochs_hard), ("all", train_rows, epochs_all)]
    total_epochs = epochs_hard + epochs_all
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=lr, total_steps=sum(max(1, len(rows) // batch) * ep for _, rows, ep in stages),
        pct_start=0.15)
    history, best = [], {"hard_exact": -1.0}
    started = time.time()
    epoch_no = 0
    for stage, rows, ep in stages:
        loader = DataLoader(PlateDataset(dataset, rows, True, SEED), batch_size=batch, shuffle=True,
                            num_workers=workers, collate_fn=collate, drop_last=True,
                            persistent_workers=workers > 0)
        for _ in range(ep):
            epoch_no += 1
            model.train()
            losses = []
            t0 = time.time()
            for x, y, lengths, _ in loader:
                x = x.to(device, non_blocking=True)
                with torch.autocast(device.type, enabled=device.type == "cuda"):
                    lp = model(x)                                  # [B, T, C]
                lp = lp.float().permute(1, 0, 2)                    # [T, B, C]
                in_len = torch.full((lp.shape[1],), lp.shape[0], dtype=torch.long)
                loss = ctc(lp, y, in_len, lengths)
                opt.zero_grad(set_to_none=True)
                scaler.scale(loss).backward()
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                scaler.step(opt); scaler.update(); sched.step()
                losses.append(loss.item())
            preds = predict_rows(model, dataset, val_rows, device)
            m = summarise(preds)
            hard_exact = m["select_band"]["exact"] or 0.0
            rec = {"epoch": epoch_no, "stage": stage, "loss": round(float(np.mean(losses)), 4),
                   "val_exact": m["overall"]["exact"], "val_hard_exact": hard_exact,
                   "val_char_acc": m["overall"]["char_acc"],
                   "by_width": {k: v["exact"] for k, v in m["by_width"].items()},
                   "seconds": round(time.time() - t0)}
            history.append(rec)
            log.info("epoch %2d/%d [%s] loss %.3f  val exact %.3f  hard %.3f  char %.3f  "
                     "widths %s  %ds", epoch_no, total_epochs, stage, rec["loss"], rec["val_exact"] or 0,
                     hard_exact, rec["val_char_acc"] or 0, rec["by_width"], rec["seconds"])
            torch.save({"model": model.state_dict(), "spec": ReaderSpec().__dict__, "epoch": epoch_no},
                       run_dir / "last.pt")
            if hard_exact > best["hard_exact"]:
                best = {"hard_exact": hard_exact, "epoch": epoch_no, "metrics": m}
                torch.save({"model": model.state_dict(), "spec": ReaderSpec().__dict__, "epoch": epoch_no},
                           run_dir / "best.pt")
            write_json(run_dir / "history.json", history)

    record = {
        "experiment": name, "dataset": str(dataset), "device": str(device),
        "params_m": round(n_params / 1e6, 2), "batch": batch, "lr": lr,
        "epochs_hard": epochs_hard, "epochs_all": epochs_all,
        "train_crops": len(train_rows), "hard_train_crops": len(hard_rows), "val_crops": len(val_rows),
        "enhanced_with": str(sr_weights) if sr_weights else None,
        "initialised_from": str(init) if init else None,
        "selected_on": f"exact match on synthetic val crops {SELECT_BAND[0]:.0f}-{SELECT_BAND[1]:.0f} px wide",
        "best_epoch": best.get("epoch"), "best": best.get("metrics"),
        "elapsed_min": round((time.time() - started) / 60, 1),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "best_checkpoint": str(run_dir / "best.pt"),
    }
    write_json(run_dir / "experiment.json", record)
    return record


# ---------------------------------------------------------------------------
# Evaluate (synthetic val + real crops, optionally against Paddle)
# ---------------------------------------------------------------------------

def load_reader(weights: Path, device):
    import torch
    ck = torch.load(weights, map_location="cpu", weights_only=False)
    spec = ReaderSpec(**ck.get("spec", {}))
    model = build_model(spec).to(device)
    model.load_state_dict(ck["model"])
    model.eval()
    return model


def real_rows(real_dir: Path) -> list[dict]:
    with open(real_dir / "labels.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["plate_image"] = r["image"]
        r["plate_px"] = float(r["width_px"])
        r["tier"] = "real"; r["source"] = "real"; r["readable"] = 1
    return rows


def paddle_predict(root: Path, rows: list[dict]) -> list[tuple[str, str, float, dict]]:
    from anpr.config import OcrConfig
    from anpr.ocr import PaddleEngine
    from anpr import enhance
    from anpr.config import EnhanceConfig
    eng = PaddleEngine(OcrConfig())
    if not eng.available():
        raise SystemExit("PaddleOCR is not available in this interpreter")
    out = []
    for r in rows:
        img = cv2.imread(str(_resolve(root, r["plate_image"])))
        # The pipeline never hands Paddle a raw crop: it gets the enhanced
        # variants. Give it the same treatment here.
        variants = enhance.variants(img, EnhanceConfig()) if hasattr(enhance, "variants") else [("base", img)]
        images = [v for _, v in variants] or [img]
        readings = eng.read_batch(images, allow_fallback=False)
        best = ("", 0.0)
        for rs in readings:
            for text, conf in rs:
                cleaned = pr.clean(text)
                if conf > best[1] and cleaned:
                    best = (cleaned, float(conf))
        out.append((best[0], r["text"], best[1], r))
    return out


def evaluate(weights: Path, dataset: Path, real_dir: Path | None, device_arg: str | None,
             compare_paddle: bool, tag: str, sr_weights: Path | None = None) -> dict:
    import torch
    device = torch.device(device_arg or ("cuda" if torch.cuda.is_available() else "cpu"))
    model = load_reader(weights, device)
    report = {"weights": str(weights), "evaluated_at": datetime.now(timezone.utc).isoformat(),
              "enhanced_with": str(sr_weights) if sr_weights else None}
    val = load_rows(dataset, ("val_synth", "val_boost"))
    if sr_weights is not None:
        enhance_rows(val, dataset, sr_weights, dataset / "plates_sr" / sr_weights.parent.name)
    report["synthetic_val"] = summarise(predict_rows(model, dataset, val, device))
    if real_dir and (real_dir / "labels.csv").exists():
        rr = real_rows(real_dir)
        if sr_weights is not None:
            enhance_rows(rr, real_dir, sr_weights, real_dir / "plates_sr" / sr_weights.parent.name)
        preds = predict_rows(model, real_dir, rr, device)
        report["real"] = summarise(preds)
        report["real_reads"] = [{"image": p[3].get("plate_image_raw", p[3]["image"]), "truth": p[1], "reader": p[0],
                                 "reader_repaired": repaired(p[0]), "conf": round(p[2], 3)} for p in preds]
        if compare_paddle:
            pp = paddle_predict(real_dir, rr)
            report["real_paddle"] = summarise(pp)
            for entry, p in zip(report["real_reads"], pp):
                entry["paddle"] = p[0]; entry["paddle_repaired"] = repaired(p[0]); entry["paddle_conf"] = round(p[2], 3)
    out = WORKER_ROOT / "reports" / "reader" / f"{tag}.json"
    write_json(out, report)
    return report


def _print_block(title: str, b: dict) -> None:
    print(f"  {title:<14} n={b['n']:<5} exact={b['exact']}  repaired={b['exact_repaired']}  char={b['char_acc']}")


def print_report(rep: dict) -> None:
    for key in ("synthetic_val", "real", "real_paddle"):
        if key not in rep:
            continue
        m = rep[key]
        print(f"== {key}")
        _print_block("overall", m["overall"])
        _print_block("hard tiers", m["hard_tiers"])
        _print_block("24-60px band", m["select_band"])
        for t, b in m["by_tier"].items():
            if b["n"]:
                _print_block(t, b)
        for w, b in m["by_width"].items():
            if b["n"]:
                _print_block(f"width {w}", b)
        if m["empty_labels"]["n"]:
            print(f"  empty labels   n={m['empty_labels']['n']}  silence={m['empty_labels']['silence_rate']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="dataset/v4_uniform")
    ap.add_argument("--name", default="R1_hard_first")
    ap.add_argument("--epochs-hard", type=int, default=6)
    ap.add_argument("--epochs-all", type=int, default=10)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--device", default=None)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None, help="Smoke: cap the training rows.")
    ap.add_argument("--eval", default=None, metavar="WEIGHTS", help="Evaluate instead of training.")
    ap.add_argument("--real", default="dataset/real_plates")
    ap.add_argument("--compare-paddle", action="store_true")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--sr", default=None, metavar="SR_WEIGHTS",
                    help="Pass every crop through this plate upscaler first "
                         "(detection -> enhancement -> OCR), for training and evaluation.")
    ap.add_argument("--init", default=None, metavar="READER_WEIGHTS",
                    help="Start training from these reader weights instead of scratch.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.eval:
        weights = Path(args.eval)
        tag = args.tag or weights.parent.name
        rep = evaluate(weights, Path(args.dataset), Path(args.real), args.device, args.compare_paddle, tag,
                       Path(args.sr) if args.sr else None)
        print_report(rep)
        print(f"Written: reports/reader/{tag}.json")
        return 0

    rec = train(Path(args.dataset), args.name, args.epochs_hard, args.epochs_all, args.batch,
                args.lr, args.device, args.workers, args.limit,
                Path(args.sr) if args.sr else None, Path(args.init) if args.init else None)
    print("READER TRAINING COMPLETE")
    print(f"best epoch {rec['best_epoch']}  24-60px exact {rec['best']['select_band']['exact'] if rec['best'] else None}")
    print(f"best: {rec['best_checkpoint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
