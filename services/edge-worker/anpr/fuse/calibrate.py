"""Confidence calibration (spec 8.3): temperature scaling fitted on a held-out
split so that a reported 0.75 means ~75% correct. Also emits the reliability
diagram to reports/calibration.png.

Usage:
    python -m anpr.fuse.calibrate reports/eval_<ts>.json --out config/thresholds.yaml
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml


def fit_temperature(conf: np.ndarray, correct: np.ndarray, grid=np.linspace(0.3, 4.0, 75)) -> float:
    """Minimise NLL of correct ~ Bernoulli(sigmoid(logit(conf)/T))."""
    conf = np.clip(conf, 1e-4, 1 - 1e-4)
    logit = np.log(conf / (1 - conf))
    y = correct.astype(np.float64)
    best_T, best_nll = 1.0, np.inf
    for T in grid:
        p = 1 / (1 + np.exp(-logit / T))
        p = np.clip(p, 1e-6, 1 - 1e-6)
        nll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        if nll < best_nll:
            best_T, best_nll = float(T), float(nll)
    return best_T


def expected_calibration_error(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            ece += m.mean() * abs(correct[m].mean() - conf[m].mean())
    return float(ece)


def reliability_diagram(conf: np.ndarray, correct: np.ndarray, out_png: str | Path, T: float | None = None,
                        bins: int = 10) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    edges = np.linspace(0, 1, bins + 1)
    accs, confs, counts = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        accs.append(correct[m].mean() if m.any() else np.nan)
        confs.append(conf[m].mean() if m.any() else (lo + hi) / 2)
        counts.append(int(m.sum()))
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.2))
    ax[0].plot([0, 1], [0, 1], "k--", lw=1, label="perfect")
    ax[0].bar((edges[:-1] + edges[1:]) / 2, np.nan_to_num(accs), width=1 / bins, alpha=0.6, label="observed")
    ax[0].set_xlabel("reported confidence")
    ax[0].set_ylabel("fraction correct")
    ax[0].set_title(f"Reliability (n={len(conf)}, ECE={expected_calibration_error(conf, correct):.3f}"
                    + (f", T={T:.2f}" if T else "") + ")")
    ax[0].legend()
    ax[1].bar((edges[:-1] + edges[1:]) / 2, counts, width=1 / bins, alpha=0.6)
    ax[1].set_xlabel("reported confidence")
    ax[1].set_ylabel("count")
    ax[1].set_title("confidence histogram")
    fig.tight_layout()
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=120)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_json")
    ap.add_argument("--out", default="config/thresholds.yaml")
    ap.add_argument("--png", default="reports/calibration.png")
    a = ap.parse_args()
    with open(a.eval_json, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    rows = [r for r in d["per_track"] if r.get("pred") and r.get("correct") is not None and r["legible"]]
    if len(rows) < 5:
        print(f"only {len(rows)} scored tracks; not fitting")
        return
    conf = np.array([r["confidence"] for r in rows])
    corr = np.array([bool(r["correct"]) for r in rows])
    T = fit_temperature(conf, corr)
    print(f"fitted temperature T={T:.3f} on {len(rows)} tracks; ECE before={expected_calibration_error(conf, corr):.3f}")
    reliability_diagram(conf, corr, a.png, T)
    with open(a.out, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg.setdefault("confidence", {})["temperature"] = float(T)
    with open(a.out, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    print(f"wrote temperature to {a.out}; diagram at {a.png}")


if __name__ == "__main__":
    main()
