#!/usr/bin/env bash
# After the overnight chain: a properly long reader run from the R2
# checkpoint with the in-RAM cache (epochs are GPU-bound), then the same
# real-crop evaluation; if it beats the reader the chain installed, the
# final comparison is re-run with it.
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
LOG=reports/r4.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for the overnight chain"
until grep -q "^=== .* DONE" reports/overnight.log 2>/dev/null; do sleep 30; done

INIT=runs/reader/R2_all_crops/best.pt
[ -f "$INIT" ] || INIT=""
step "R4: cached, 5 hard + 35 all epochs, from ${INIT:-scratch}"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --name R4_long --epochs-hard 5 --epochs-all 35 \
  --batch 256 --lr 1e-3 --device cuda --cache ${INIT:+--init "$INIT"} > reports/training_R4_long.log 2>&1
echo "R4_EXIT=$?" | tee -a "$LOG"
[ -f runs/reader/R4_long/best.pt ] || { step "no R4 weights"; exit 1; }

step "R4: real-crop evaluation, raw and through S3, against PaddleOCR"
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R4_long/best.pt --compare-paddle --tag R4_long_raw >> "$LOG" 2>&1
$PY -u tools/train_plate_reader.py --dataset "$DATASET" --eval runs/reader/R4_long/best.pt --compare-paddle --sr runs/sr/S3_wide_cont/best.pt --tag R4_long_sr >> "$LOG" 2>&1

BEST=$($PY - <<'EOF'
import json, pathlib
def exact(tag):
    p = pathlib.Path(f"reports/reader/{tag}.json")
    return float((json.load(open(p)).get("real") or {}).get("overall", {}).get("exact_repaired") or 0.0) if p.exists() else -1
r4 = max(exact("R4_long_raw"), exact("R4_long_sr"))
prev = max(exact("R3_enhanced_sr"), exact("R2_all_crops_sr"), exact("R2_all_crops_raw"))
print(f"R4 {r4:.3f} vs earlier {prev:.3f}", file=__import__("sys").stderr)
print("R4" if r4 > prev else "keep")
EOF
)
step "verdict: $BEST"
if [ "$BEST" = "R4" ]; then
  MF=$(ls runs/mfsr/M2_cont/best.pt 2>/dev/null || ls runs/mfsr/M1_hard_first/best.pt)
  READER=runs/reader/R4_long/best.pt SR=runs/sr/S3_wide_cont/best.pt MFSR="$MF" DETECTOR=none bash tools/run_final_compare.sh
  echo "FINAL_R4_EXIT=$?" | tee -a "$LOG"
fi
step "DONE"
