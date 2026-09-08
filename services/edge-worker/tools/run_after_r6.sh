#!/usr/bin/env bash
# When R6 is done: evaluate it on the real crops raw and through S3; if it
# beats the reader installed by run_after_readers, install it and re-run the
# real-footage comparison.
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
LOG=reports/after_r6.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for R6 and the earlier chain"
until grep -q "R6_EXIT=" reports/r6.log 2>/dev/null && grep -q "^=== .* DONE" reports/after_readers.log 2>/dev/null; do sleep 30; done
[ -f runs/reader/R6_long/best.pt ] || { step "no R6 weights"; exit 1; }

step "R6: real-crop evaluation, raw and through S3, against PaddleOCR"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R6_long/best.pt --compare-paddle --tag R6_long_raw >> "$LOG" 2>&1
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R6_long/best.pt --compare-paddle --sr runs/sr/S3_wide_cont/best.pt --tag R6_long_sr >> "$LOG" 2>&1

VERDICT=$($PY - <<'EOF'
import json, pathlib
def exact(tag):
    p = pathlib.Path(f"reports/reader/{tag}.json")
    return float((json.load(open(p)).get("real") or {}).get("overall", {}).get("exact_repaired") or 0.0) if p.exists() else -1
r6 = max(exact("R6_long_raw"), exact("R6_long_sr"))
prev = max(exact("R5_staged_raw"), exact("R5_staged_sr"), exact("R4_long_raw"), exact("R4_long_sr"), exact("R3_enhanced_sr"))
print(f"R6 {r6:.3f} vs earlier best {prev:.3f}", file=__import__("sys").stderr)
print("R6" if r6 > prev else "keep")
EOF
)
step "verdict: $VERDICT"
if [ "$VERDICT" = "R6" ]; then
  READER=runs/reader/R6_long/best.pt SR=runs/sr/S3_wide_cont/best.pt MFSR=runs/mfsr/M2_cont/best.pt DETECTOR=none bash tools/run_final_compare.sh
  echo "FINAL_R6_EXIT=$?" | tee -a "$LOG"
fi
step "DONE"
