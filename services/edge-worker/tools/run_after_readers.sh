#!/usr/bin/env bash
# When R4 (long, hard-first) and R5 (width-staged) are both done: evaluate
# R5 on the real crops raw and through S3, pick the best reader of R3/R4/R5
# by grammar-repaired exact match on the real crops, install it with B, S3
# and M2, and run the real-footage comparison with no size floor.
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
LOG=reports/after_readers.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for R4 and R5"
until grep -q "^=== .* DONE" reports/r4.log 2>/dev/null && grep -q "R5_EXIT=" reports/r5.log 2>/dev/null; do sleep 30; done

if [ -f runs/reader/R5_staged/best.pt ]; then
  step "R5: real-crop evaluation, raw and through S3"
  $PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R5_staged/best.pt --tag R5_staged_raw >> "$LOG" 2>&1
  $PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R5_staged/best.pt --sr runs/sr/S3_wide_cont/best.pt --tag R5_staged_sr >> "$LOG" 2>&1
fi

READER=$($PY - <<'EOF'
import json, pathlib
def exact(tag):
    p = pathlib.Path(f"reports/reader/{tag}.json")
    if not p.exists():
        return -1.0
    d = json.load(open(p))
    return float((d.get("real") or {}).get("overall", {}).get("exact_repaired") or 0.0)
c = {"runs/reader/R5_staged/best.pt": max(exact("R5_staged_raw"), exact("R5_staged_sr")),
     "runs/reader/R4_long/best.pt": max(exact("R4_long_raw"), exact("R4_long_sr")),
     "runs/reader/R3_enhanced/best.pt": exact("R3_enhanced_sr")}
c = {k: v for k, v in c.items() if pathlib.Path(k).exists()}
best = max(c, key=c.get)
print(" ".join(f"{pathlib.Path(k).parent.name}={v:.3f}" for k, v in c.items()), file=__import__("sys").stderr)
print(best)
EOF
)
step "reader kept: $READER"
READER="$READER" SR=runs/sr/S3_wide_cont/best.pt MFSR=runs/mfsr/M2_cont/best.pt DETECTOR=none bash tools/run_final_compare.sh
echo "FINAL_EXIT=$?" | tee -a "$LOG"
step "DONE"
