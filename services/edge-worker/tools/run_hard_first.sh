#!/usr/bin/env bash
# End-to-end, unattended: wait for the running jobs, evaluate C and D, train
# the hard-first model on the synthetic set, evaluate it, render its error
# sheets, and compare it against the baseline on real footage.
#
# Every step writes under reports/; the last line of reports/hard_first.log is
# the summary. Re-runnable: evaluations that already exist are skipped, and a
# new NAME lands in its own run directory.
#
#   NAME=F_hard_first              run name (runs/plate/<NAME>_stage4_all)
#   EPOCHS=12                      epochs on stage4_all
#   TRAIN_EXTRA="--lr0 0.002 --warmup-epochs 1"   extra trainer flags
set -u
cd "$(dirname "$0")/.."
PY=../../.venv/Scripts/python.exe
NAME=${NAME:-F_hard_first}
EPOCHS=${EPOCHS:-12}
TRAIN_EXTRA=${TRAIN_EXTRA:-}
LOG=reports/hard_first.log
step() { echo "=== $(date '+%H:%M:%S') [$NAME] $*" | tee -a "$LOG"; }
evaluated() { [ -f "reports/eval_by_size/$1_test_imgsz960.json" ]; }

step "waiting for D_tiny_oversample and the synthetic set"
until grep -q "EXPERIMENTS COMPLETE\|TRAIN_EXIT=" reports/training_CD.log 2>/dev/null; do sleep 30; done
until [ -f dataset/v3_synth/manifest.json ]; do sleep 30; done
step "both ready"

step "evaluating C and D per size band (test, imgsz 960)"
for e in C_smallobj_aug D_tiny_oversample; do
  w="runs/plate/$e/weights/best.pt"
  [ -f "$w" ] && ! evaluated "$e" && $PY -u tools/eval_by_size.py --weights "$w" --tag "$e" >> "$LOG" 2>&1
done

step "hard-first training: fine-tune B_highres_960 on stage4_all (all tiers, 60% severe+extreme), $EPOCHS epochs $TRAIN_EXTRA"
# 12 epochs, not 25: measured 2.9 it/s at 960/batch 4 puts one epoch over 10,604
# images at ~15 min; fine-tuning from an already-trained checkpoint converges
# well inside that budget, and 6.5 h would cost a day of the seven left.
$PY -u tools/train_plate_detector.py --curriculum dataset/v3_synth \
  --stages stage4_all --base-model runs/plate/B_highres_960/weights/best.pt \
  --name "$NAME" --epochs "$EPOCHS" --device 0 $TRAIN_EXTRA >> "reports/training_${NAME}.log" 2>&1
echo "${NAME}_TRAIN_EXIT=$?" | tee -a "$LOG"

W=$(ls -d runs/plate/${NAME}_stage4_all*/weights/best.pt 2>/dev/null | tail -1)
if [ -z "$W" ]; then step "no $NAME weights - training failed"; exit 1; fi
step "evaluating $NAME per size band at the AP floor and at deployment conf 0.25"
$PY -u tools/eval_by_size.py --weights "$W" --tag "$NAME" >> "$LOG" 2>&1
$PY -u tools/eval_by_size.py --weights "$W" --tag "${NAME}_conf025" --conf 0.25 >> "$LOG" 2>&1
evaluated B_highres_960_conf025 || $PY -u tools/eval_by_size.py --weights runs/plate/B_highres_960/weights/best.pt --tag B_highres_960_conf025 --conf 0.25 >> "$LOG" 2>&1
evaluated baseline_conf025 || $PY -u tools/eval_by_size.py --weights "D:/ANPR/models/plate_detector.pt" --tag baseline_conf025 --conf 0.25 >> "$LOG" 2>&1

step "error sheets for $NAME"
$PY -u tools/error_analysis.py --weights "$W" --tag "$NAME" --conf 0.25 --limit 24 >> "$LOG" 2>&1

step "real footage: baseline vs B vs $NAME through the fixed pipeline (cam06, cam07)"
$PY -u tools/compare_on_footage.py \
  --weights "D:/ANPR/models/plate_detector.pt" runs/plate/B_highres_960/weights/best.pt "$W" \
  --labels baseline B_highres_960 "$NAME" --cameras cam06 cam07 --per-camera 30 \
  --out "reports/footage_comparison_${NAME}.json" >> "$LOG" 2>&1

step "DONE"
