#!/usr/bin/env bash
# Overnight chain after the upscaler loop:
#   M2: continue M1 for 24 epochs at 3e-4, evaluate against S3 on the same
#       plates; keep whichever of M1/M2 is closer to truth under 24 px
#   readers R2 (raw) and R3 (through S3) on the complete crops, real-crop
#       evaluation against PaddleOCR
#   install B (detector), the chosen reader, S3 and the chosen multi-frame
#       model into models/, and compare on real footage with no size floor
# Log: reports/overnight.log (last line "=== DONE").
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
DATASET=dataset/v4_uniform
LOG=reports/overnight.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

# ---- M2 ------------------------------------------------------------------
step "M2: M1 continued, 24 epochs at 3e-4"
$PY -u tools/train_plate_sr.py --dataset "$DATASET" --name M2_cont --frames 5 --epochs 24 --batch 32 --lr 3e-4 \
  --init runs/mfsr/M1_hard_first/best.pt > reports/training_M2_cont.log 2>&1
echo "M2_EXIT=$?" | tee -a "$LOG"
if [ -f runs/mfsr/M2_cont/best.pt ]; then
  $PY -u tools/train_plate_sr.py --dataset "$DATASET" --frames 5 --eval runs/mfsr/M2_cont/best.pt \
    --single runs/sr/S3_wide_cont/best.pt --sheet reports/mfsr_sheet_M2.jpg --count 24 --device cpu >> "$LOG" 2>&1
fi
MF=$($PY - <<'EOF'
import json, pathlib
def hard(tag):
    p = pathlib.Path(f"reports/mfsr/{tag}.json")
    return float(json.load(open(p))["val"]["hard_lr_under_24px"].get("psnr_mf", 0)) if p.exists() else -1
m1, m2 = hard("M1_hard_first"), hard("M2_cont")
print("runs/mfsr/M2_cont/best.pt" if m2 > m1 + 0.05 else "runs/mfsr/M1_hard_first/best.pt")
print(f"M1 {m1:.2f}  M2 {m2:.2f}", file=__import__("sys").stderr)
EOF
)
step "multi-frame kept: $MF"
$PY -u tools/result_sheet.py --single runs/sr/S3_wide_cont/best.pt --multi "$MF" --count 14 --out reports/result_sheet_final_upscalers.jpg >> "$LOG" 2>&1

# ---- readers ---------------------------------------------------------------
step "readers: R2 raw, R3 through S3"
: > reports/reader_sr.log
SR=S3_wide_cont bash tools/run_reader_sr.sh > reports/reader_sr_stdout3.log 2>&1
echo "READERS_EXIT=$?" | tee -a "$LOG"
READER=$($PY - <<'EOF'
import json, pathlib
def exact(tag):
    p = pathlib.Path(f"reports/reader/{tag}.json")
    if not p.exists():
        return -1.0
    return float((json.load(open(p)).get("real") or {}).get("overall", {}).get("exact_repaired") or 0.0)
c = {"runs/reader/R3_enhanced/best.pt": exact("R3_enhanced_sr"),
     "runs/reader/R2_all_crops/best.pt": max(exact("R2_all_crops_sr"), exact("R2_all_crops_raw"))}
best = max(c, key=c.get)
print(best if pathlib.Path(best).exists() else "runs/reader/R2_all_crops/best.pt")
EOF
)
step "reader kept: $READER"

# ---- install + real footage --------------------------------------------------
# DETECTOR=none: no such run, so run_final_compare falls back to B.
READER="$READER" SR=runs/sr/S3_wide_cont/best.pt MFSR="$MF" DETECTOR=none bash tools/run_final_compare.sh
echo "FINAL_EXIT=$?" | tee -a "$LOG"
step "DONE"
