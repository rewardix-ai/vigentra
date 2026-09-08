#!/usr/bin/env bash
# End-to-end, unattended: wait for the running jobs, evaluate C and D, train
# the hard-first model on the synthetic set, evaluate it, render its error
# sheets, and compare it against the baseline on real footage.
#
# Every step writes under reports/; the last line of reports/hard_first.log is
# the summary. Safe to re-run: each step skips nothing, so a rerun overwrites.
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
LOG=reports/hard_first.log
step() { echo "=== $(date '+%H:%M:%S') $*" | tee -a "$LOG"; }

step "waiting for D_tiny_oversample and the synthetic set"
until grep -q "EXPERIMENTS COMPLETE\|TRAIN_EXIT=" reports/training_CD.log 2>/dev/null; do sleep 30; done
until [ -f dataset/v3_synth/manifest.json ]; do sleep 30; done
step "both ready"

step "evaluating C and D per size band (test, imgsz 960)"
for e in C_smallobj_aug D_tiny_oversample; do
  w="runs/plate/$e/weights/best.pt"
  [ -f "$w" ] && $PY -u tools/eval_by_size.py --weights "$w" --tag "$e" >> "$LOG" 2>&1
done

step "hard-first training: fine-tune B_highres_960 on stage4_all (all tiers, 60% severe+extreme)"
# 12 epochs, not 25: measured 2.9 it/s at 960/batch 4 puts one epoch over 10,604
# images at ~15 min; fine-tuning from an already-trained checkpoint converges
# well inside that budget, and 6.5 h would cost a day of the seven left.
$PY -u tools/train_plate_detector.py --curriculum dataset/v3_synth \
  --stages stage4_all --base-model runs/plate/B_highres_960/weights/best.pt \
  --name F_hard_first --epochs 12 --device 0 >> reports/training_F.log 2>&1
echo "F_TRAIN_EXIT=$?" | tee -a "$LOG"

W=$(ls -d runs/plate/F_hard_first_stage4_all*/weights/best.pt 2>/dev/null | tail -1)
if [ -z "$W" ]; then step "no F weights - training failed"; exit 1; fi
step "evaluating F per size band at the AP floor and at deployment conf 0.25"
$PY -u tools/eval_by_size.py --weights "$W" --tag F_hard_first >> "$LOG" 2>&1
$PY -u tools/eval_by_size.py --weights "$W" --tag F_hard_first_conf025 --conf 0.25 >> "$LOG" 2>&1
$PY -u tools/eval_by_size.py --weights runs/plate/B_highres_960/weights/best.pt --tag B_highres_960_conf025 --conf 0.25 >> "$LOG" 2>&1
$PY -u tools/eval_by_size.py --weights "D:/ANPR/models/plate_detector.pt" --tag baseline_conf025 --conf 0.25 >> "$LOG" 2>&1

step "error sheets for F"
$PY -u tools/error_analysis.py --weights "$W" --tag F_hard_first --conf 0.25 --limit 24 >> "$LOG" 2>&1

step "real footage: baseline vs B vs F through the fixed pipeline (cam06, cam07)"
$PY -u tools/compare_on_footage.py \
  --weights "D:/ANPR/models/plate_detector.pt" runs/plate/B_highres_960/weights/best.pt "$W" \
  --labels baseline B_highres_960 F_hard_first --cameras cam06 cam07 --per-camera 30 \
  --out reports/footage_comparison_F.json >> "$LOG" 2>&1

step "DONE"
