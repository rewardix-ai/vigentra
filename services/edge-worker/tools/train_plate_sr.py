"""Train the plate super-resolution model on the uniform dataset's SR pairs.

Pairs come from tools/build_uniform_dataset.py: `sr/train/<id>_lr.png` is the
degraded crop at its information size, `<id>_hr.jpg` the same region of the
sharp composite at 4x. Loss is L1 on the image plus a gradient (edge) term,
which is what keeps strokes from being smoothed into the background - the
failure a plain L1 model converges to on text.

Selection is on the hard pairs: PSNR over pairs whose LR width is under
24 px, the plates the enhancement layer exists for.

    python tools/train_plate_sr.py --dataset dataset/v4_uniform --name S1_hard_first
    python tools/train_plate_sr.py --eval runs/sr/S1_hard_first/best.pt --sheet reports/sr_sheet.jpg
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
WORKER_ROOT = HERE.parent
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(HERE))

from anpr.sr import SrSpec, build_model, to_tensor, to_image   # noqa: E402
from _corpus import write_json                                # noqa: E402

log = logging.getLogger("train_plate_sr")
SEED = 0
HARD_LR_W = 24
#: Training crops: LR patches this size (HR 4x), random position, so a batch
#: is one tensor and a wide plate contributes several windows.
PATCH_LR = (16, 48)      # h, w


def load_pairs(dataset: Path, split: str) -> list[dict]:
    with open(dataset / "plates.csv", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["split"] == split and r.get("sr_pair")]
    out = []
    for r in rows:
        lr = dataset / f"{r['sr_pair']}_lr.png"
        hr = dataset / f"{r['sr_pair']}_hr.jpg"
        if lr.exists() and hr.exists():
            out.append({"lr": lr, "hr": hr, "tier": r["tier"], "plate_px": float(r["plate_px"]),
                        "night": r["night"], "glare": r.get("glare", "0"), "text": r["text"]})
    return out


def make_dataset_class():
    import torch
    from torch.utils.data import Dataset

    class PairDataset(Dataset):
        def __init__(self, pairs: list[dict], train: bool, seed: int, scale: int) -> None:
            self.pairs, self.train, self.scale = pairs, train, scale
            self.rng = np.random.default_rng(seed)

        def __len__(self) -> int:
            return len(self.pairs)

        def __getitem__(self, i: int):
            p = self.pairs[i]
            lr = cv2.imread(str(p["lr"]))
            hr = cv2.imread(str(p["hr"]))
            ph, pw = PATCH_LR
            # Pad small LR crops up to the patch, replicating edges; HR the same.
            if lr.shape[0] < ph or lr.shape[1] < pw:
                bh, bw = max(0, ph - lr.shape[0]), max(0, pw - lr.shape[1])
                lr = cv2.copyMakeBorder(lr, 0, bh, 0, bw, cv2.BORDER_REPLICATE)
                hr = cv2.copyMakeBorder(hr, 0, bh * self.scale, 0, bw * self.scale, cv2.BORDER_REPLICATE)
            hr = hr[: lr.shape[0] * self.scale, : lr.shape[1] * self.scale]
            y0 = int(self.rng.integers(0, lr.shape[0] - ph + 1)) if self.train else 0
            x0 = int(self.rng.integers(0, lr.shape[1] - pw + 1)) if self.train else 0
            lr = lr[y0:y0 + ph, x0:x0 + pw]
            hr = hr[y0 * self.scale:(y0 + ph) * self.scale, x0 * self.scale:(x0 + pw) * self.scale]
            if self.train and self.rng.random() < 0.5:
                lr, hr = lr[:, ::-1].copy(), hr[:, ::-1].copy()   # mirror: strokes are strokes
            return to_tensor(lr)[0], to_tensor(hr)[0], i

    return PairDataset


def _grad(t):
    import torch
    dx = t[..., :, 1:] - t[..., :, :-1]
    dy = t[..., 1:, :] - t[..., :-1, :]
    return dx, dy


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    return 99.0 if mse < 1e-6 else 10.0 * np.log10(255.0 ** 2 / mse)


def evaluate_pairs(model, pairs: list[dict], device) -> dict:
    """Full-crop PSNR against bicubic, per hardness."""
    import torch
    model.eval()
    rows = []
    with torch.no_grad():
        for p in pairs:
            lr = cv2.imread(str(p["lr"])); hr = cv2.imread(str(p["hr"]))
            if lr is None or hr is None:
                continue
            out = to_image(model(to_tensor(lr).to(device)))
            h = min(out.shape[0], hr.shape[0]); w = min(out.shape[1], hr.shape[1])
            bic = cv2.resize(lr, (out.shape[1], out.shape[0]), interpolation=cv2.INTER_CUBIC)
            rows.append({"lr_w": lr.shape[1], "tier": p["tier"], "night": p["night"], "glare": p["glare"],
                         "psnr_model": psnr(out[:h, :w], hr[:h, :w]),
                         "psnr_bicubic": psnr(bic[:h, :w], hr[:h, :w])})

    def block(sel):
        if not sel:
            return {"n": 0}
        return {"n": len(sel),
                "psnr_model": round(float(np.mean([r["psnr_model"] for r in sel])), 2),
                "psnr_bicubic": round(float(np.mean([r["psnr_bicubic"] for r in sel])), 2)}
    return {
        "overall": block(rows),
        "hard_lr_under_24px": block([r for r in rows if r["lr_w"] < HARD_LR_W]),
        "by_tier": {t: block([r for r in rows if r["tier"] == t]) for t in ("extreme", "severe", "moderate", "mild")},
        "night": block([r for r in rows if r["night"] == "1"]),
        "glare": block([r for r in rows if r["glare"] == "1"]),
    }


def train(dataset: Path, name: str, epochs: int, batch: int, lr: float, device_arg: str | None,
          limit: int | None) -> dict:
    import torch
    from torch.utils.data import DataLoader

    torch.manual_seed(SEED)
    device = torch.device(device_arg or ("cuda" if torch.cuda.is_available() else "cpu"))
    run_dir = WORKER_ROOT / "runs" / "sr" / name
    if run_dir.exists():
        raise SystemExit(f"{run_dir} exists - pick another --name")
    run_dir.mkdir(parents=True)
    spec = SrSpec()
    train_pairs = load_pairs(dataset, "train")
    val_pairs = load_pairs(dataset, "val_synth")
    if limit:
        train_pairs, val_pairs = train_pairs[:limit], val_pairs[: max(50, limit // 10)]
    if not train_pairs:
        raise SystemExit("no SR pairs - build the dataset with sr/ (tools/build_uniform_dataset.py)")
    # Hard first: the sub-24 px pairs are sampled three times as often.
    weights = [3.0 if cv2.imread(str(p["lr"]), cv2.IMREAD_GRAYSCALE).shape[1] < HARD_LR_W else 1.0
               for p in train_pairs] if len(train_pairs) < 40000 else None
    log.info("sr pairs: train %d  val %d  device %s", len(train_pairs), len(val_pairs), device)

    PairDataset = make_dataset_class()
    ds = PairDataset(train_pairs, True, SEED, spec.scale)
    sampler = None
    if weights:
        from torch.utils.data import WeightedRandomSampler
        sampler = WeightedRandomSampler(weights, num_samples=len(weights), replacement=True)
    loader = DataLoader(ds, batch_size=batch, shuffle=sampler is None, sampler=sampler,
                        num_workers=0, drop_last=True)
    model = build_model(spec).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    log.info("sr params: %.2fM", n_params / 1e6)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=len(loader) * epochs, pct_start=0.1)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    best = {"hard_psnr": -1.0}
    history = []
    started = time.time()
    for ep in range(1, epochs + 1):
        model.train()
        losses = []
        t0 = time.time()
        for x, y, _ in loader:
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            with torch.autocast(device.type, enabled=device.type == "cuda"):
                out = model(x)
                l1 = (out - y).abs().mean()
                gx_o, gy_o = _grad(out); gx_t, gy_t = _grad(y)
                edge = (gx_o - gx_t).abs().mean() + (gy_o - gy_t).abs().mean()
                loss = l1 + 0.5 * edge
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt); scaler.update(); sched.step()
            losses.append(loss.item())
        m = evaluate_pairs(model, val_pairs[:600], device)
        hard = m["hard_lr_under_24px"].get("psnr_model", 0.0) or 0.0
        rec = {"epoch": ep, "loss": round(float(np.mean(losses)), 4), "val": m, "seconds": round(time.time() - t0)}
        history.append(rec)
        log.info("epoch %2d/%d loss %.4f  psnr overall %.2f (bicubic %.2f)  hard %.2f (bicubic %.2f)  %ds",
                 ep, epochs, rec["loss"], m["overall"].get("psnr_model", 0), m["overall"].get("psnr_bicubic", 0),
                 hard, m["hard_lr_under_24px"].get("psnr_bicubic", 0), rec["seconds"])
        torch.save({"model": model.state_dict(), "spec": spec.__dict__, "epoch": ep}, run_dir / "last.pt")
        if hard > best["hard_psnr"]:
            best = {"hard_psnr": hard, "epoch": ep, "metrics": m}
            torch.save({"model": model.state_dict(), "spec": spec.__dict__, "epoch": ep}, run_dir / "best.pt")
        write_json(run_dir / "history.json", history)
    record = {"experiment": name, "dataset": str(dataset), "params_m": round(n_params / 1e6, 2),
              "epochs": epochs, "batch": batch, "lr": lr, "train_pairs": len(train_pairs),
              "val_pairs": len(val_pairs), "selected_on": "PSNR on val pairs with LR width < 24 px",
              "best_epoch": best.get("epoch"), "best": best.get("metrics"),
              "elapsed_min": round((time.time() - started) / 60, 1),
              "finished_at": datetime.now(timezone.utc).isoformat(),
              "best_checkpoint": str(run_dir / "best.pt")}
    write_json(run_dir / "experiment.json", record)
    return record


def sheet(weights: Path, dataset: Path, out: Path, count: int, device_arg: str | None) -> None:
    """LR | bicubic | model | truth, for the eye."""
    import torch
    from anpr.sr import PlateUpscaler
    up = PlateUpscaler(str(weights), device_arg or "cpu")
    pairs = load_pairs(dataset, "val_synth")
    rng = np.random.default_rng(1)
    pairs = [pairs[i] for i in rng.choice(len(pairs), size=min(count, len(pairs)), replace=False)]
    pairs.sort(key=lambda p: p["plate_px"])
    tiles = []
    for p in pairs:
        lr = cv2.imread(str(p["lr"])); hr = cv2.imread(str(p["hr"]))
        sr = up.upscale(lr)
        bic = cv2.resize(lr, (sr.shape[1], sr.shape[0]), interpolation=cv2.INTER_CUBIC)
        hr = cv2.resize(hr, (sr.shape[1], sr.shape[0]))
        W = 260
        def fit(im):
            s = min(W / im.shape[1], 70 / im.shape[0])
            im = cv2.resize(im, (max(1, int(im.shape[1] * s)), max(1, int(im.shape[0] * s))), interpolation=cv2.INTER_NEAREST)
            c = np.full((72, W, 3), 255, np.uint8); c[:im.shape[0], :im.shape[1]] = im; return c
        row = np.hstack([fit(cv2.resize(lr, (sr.shape[1], sr.shape[0]), interpolation=cv2.INTER_NEAREST)),
                         fit(bic), fit(sr), fit(hr)])
        cv2.putText(row, f"{p['tier']} lr {lr.shape[1]}px {p['text']}", (2, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 200), 1)
        tiles.append(row)
    header = np.full((24, 4 * 260, 3), 255, np.uint8)
    for i, t in enumerate(("input (nearest)", "bicubic x4", "plate SR x4", "truth")):
        cv2.putText(header, t, (i * 260 + 4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    grid = np.vstack([header] + tiles)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), grid, [cv2.IMWRITE_JPEG_QUALITY, 90])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="dataset/v4_uniform")
    ap.add_argument("--name", default="S1_hard_first")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--device", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--eval", default=None, metavar="WEIGHTS")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--count", type=int, default=24)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.eval:
        import torch
        device = torch.device(args.device or "cpu")
        ck = torch.load(args.eval, map_location="cpu", weights_only=False)
        model = build_model(SrSpec(**ck.get("spec", {}))).to(device); model.load_state_dict(ck["model"])
        m = evaluate_pairs(model, load_pairs(Path(args.dataset), "val_synth"), device)
        tag = Path(args.eval).parent.name
        write_json(WORKER_ROOT / "reports" / "sr" / f"{tag}.json", {"weights": args.eval, "val": m})
        for k in ("overall", "hard_lr_under_24px", "night", "glare"):
            print(f"  {k:<20} {m[k]}")
        for t, b in m["by_tier"].items():
            print(f"  {t:<20} {b}")
        if args.sheet:
            sheet(Path(args.eval), Path(args.dataset), Path(args.sheet), args.count, args.device)
            print(f"sheet: {args.sheet}")
        print(f"Written: reports/sr/{tag}.json")
        return 0
    rec = train(Path(args.dataset), args.name, args.epochs, args.batch, args.lr, args.device, args.limit)
    print("SR TRAINING COMPLETE")
    print(f"best epoch {rec['best_epoch']}  hard psnr {rec['best']['hard_lr_under_24px'] if rec['best'] else None}")
    print(f"best: {rec['best_checkpoint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
